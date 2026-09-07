from datetime import datetime, timedelta, timezone
from secrets import compare_digest
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.auth_session import AuthSession
from app.models.profile import UserProfile
from app.models.user import User
from app.schemas.user import UserCreate, UserLogin


class RefreshTokenError(Exception):
    pass


class RefreshTokenReuseError(RefreshTokenError):
    pass


def utcnow() -> datetime:
    """返回与数据库字段约定一致的无时区 UTC 时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def register_user(
    db: AsyncSession,
    payload: UserCreate,
    *,
    user_agent: str | None,
    ip_address: str | None,
) -> tuple[User, AuthSession, str]:
    """注册用户、创建默认画像和登录会话，并在一个事务中提交。"""
    exists = await db.scalar(select(User).where(User.email == payload.email))
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        email=payload.email,
        username=payload.username,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    await db.flush()
    db.add(UserProfile(user_id=user.id))
    session, refresh_token = await create_auth_session(
        db,
        user,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(user)
    return user, session, refresh_token


async def authenticate_user(
    db: AsyncSession,
    payload: UserLogin,
    *,
    user_agent: str | None,
    ip_address: str | None,
) -> tuple[User, AuthSession, str]:
    """校验账号密码，更新最近登录时间并创建新的登录会话。"""
    user = await db.scalar(select(User).where(User.email == payload.email))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    user.last_login_at = utcnow()
    session, refresh_token = await create_auth_session(
        db,
        user,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(user)
    return user, session, refresh_token


async def rotate_and_commit_refresh_token(
    db: AsyncSession,
    refresh_token: str,
    *,
    user_agent: str | None,
    ip_address: str | None,
) -> tuple[User, AuthSession, str]:
    """轮换 Refresh Token，并提交轮换或重放检测产生的会话状态。"""
    try:
        result = await rotate_refresh_token(
            db,
            refresh_token,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        await db.commit()
        await db.refresh(result[0])
        return result
    except RefreshTokenError:
        # 重放检测会撤销会话；普通校验失败提交只读事务也不会产生副作用。
        await db.commit()
        raise


async def logout_session(db: AsyncSession, refresh_token: str | None) -> None:
    """撤销 Refresh Token 对应的当前设备会话。"""
    if not refresh_token:
        return
    await revoke_session_from_refresh_token(db, refresh_token)
    await db.commit()


async def logout_all_sessions(db: AsyncSession, user_id: int) -> None:
    """撤销用户的全部有效登录会话。"""
    await revoke_all_user_sessions(db, user_id)
    await db.commit()


async def create_auth_session(
    db: AsyncSession,
    user: User,
    *,
    user_agent: str | None,
    ip_address: str | None,
) -> tuple[AuthSession, str]:
    """创建持久化登录会话并签发与之绑定的 Refresh Token。"""
    session_id = str(uuid4())
    expires_at = utcnow() + timedelta(days=settings.refresh_token_expire_days)
    refresh_token_jti = str(uuid4())
    refresh_token_issued_at = utcnow().replace(microsecond=0)
    refresh_token = create_refresh_token(
        str(user.id),
        session_id,
        expires_at,
        token_id=refresh_token_jti,
        issued_at=refresh_token_issued_at,
    )
    session = AuthSession(
        id=session_id,
        user_id=user.id,
        refresh_token_hash=hash_refresh_token(refresh_token),
        refresh_token_jti=refresh_token_jti,
        refresh_token_issued_at=refresh_token_issued_at,
        expires_at=expires_at,
        user_agent=(user_agent or "")[:512] or None,
        ip_address=(ip_address or "")[:45] or None,
    )
    db.add(session)
    await db.flush()
    return session, refresh_token


async def rotate_refresh_token(
    db: AsyncSession,
    refresh_token: str,
    *,
    user_agent: str | None,
    ip_address: str | None,
) -> tuple[User, AuthSession, str]:
    """在行锁内轮换 Refresh Token，并检测超出宽限窗口的旧 Token 重放。"""
    claims = decode_refresh_token(refresh_token)
    if claims is None:
        raise RefreshTokenError("Invalid or expired refresh token")

    session = await db.scalar(
        select(AuthSession)
        .where(AuthSession.id == claims.session_id)
        .with_for_update()
    )
    now = utcnow()
    if session is None or str(session.user_id) != claims.subject:
        raise RefreshTokenError("Invalid refresh token session")
    if session.revoked_at is not None or session.expires_at <= now:
        raise RefreshTokenError("Refresh token session is no longer active")
    incoming_hash = hash_refresh_token(refresh_token)
    is_current_token = compare_digest(session.refresh_token_hash, incoming_hash)
    is_recent_previous_token = bool(
        session.previous_refresh_token_hash
        and session.previous_token_valid_until
        and session.previous_token_valid_until >= now
        and compare_digest(session.previous_refresh_token_hash, incoming_hash)
    )
    if not is_current_token and not is_recent_previous_token:
        session.revoked_at = now
        await db.flush()
        raise RefreshTokenReuseError("Refresh token reuse detected")

    user = await db.get(User, session.user_id)
    if user is None:
        session.revoked_at = now
        await db.flush()
        raise RefreshTokenError("Refresh token user no longer exists")

    if is_current_token:
        session.previous_refresh_token_hash = session.refresh_token_hash
        session.previous_token_valid_until = now + timedelta(
            seconds=settings.refresh_token_reuse_grace_seconds
        )
        session.refresh_token_jti = str(uuid4())
        session.refresh_token_issued_at = now.replace(microsecond=0)

    # 并发请求携带刚轮换的旧 token 时，重建并返回同一个新 token，避免误判重放。
    rotated_token = create_refresh_token(
        str(user.id),
        session.id,
        session.expires_at,
        token_id=session.refresh_token_jti,
        issued_at=session.refresh_token_issued_at,
    )
    session.refresh_token_hash = hash_refresh_token(rotated_token)
    session.last_used_at = now
    session.user_agent = (user_agent or session.user_agent or "")[:512] or None
    session.ip_address = (ip_address or session.ip_address or "")[:45] or None
    await db.flush()
    return user, session, rotated_token


async def revoke_session_from_refresh_token(db: AsyncSession, refresh_token: str) -> None:
    """校验 Token 归属后撤销对应会话；无效 Token 按幂等退出处理。"""
    claims = decode_refresh_token(refresh_token)
    if claims is None:
        return
    session = await db.scalar(
        select(AuthSession)
        .where(AuthSession.id == claims.session_id)
        .with_for_update()
    )
    if session is not None and str(session.user_id) == claims.subject and session.revoked_at is None:
        session.revoked_at = utcnow()
        await db.flush()


async def revoke_all_user_sessions(db: AsyncSession, user_id: int) -> None:
    """批量标记用户的全部有效会话为已撤销。"""
    await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
