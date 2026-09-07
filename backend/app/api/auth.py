"""认证 HTTP 接口：负责 Cookie 与响应格式，账号业务由 Service 执行。"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token
from app.models.user import User
from app.schemas.user import SessionStatus, Token, UserCreate, UserLogin, UserRead
from app.services.auth_service import (
    RefreshTokenError,
    authenticate_user,
    logout_all_sessions,
    logout_session,
    register_user,
    rotate_and_commit_refresh_token,
)


router = APIRouter(prefix="/auth", tags=["auth"])


# 注册新用户，创建默认求职画像，并返回登录 token。
@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(
    payload: UserCreate,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> Token:
    """注册用户并把 Service 返回的 Refresh Token 写入安全 Cookie。"""
    user, session, refresh_token = await register_user(
        db,
        payload,
        **_request_metadata(request),
    )
    _set_refresh_cookie(response, refresh_token, session.expires_at)
    return _token_response(user, session.id)


# 用户登录，校验邮箱和密码，成功后返回登录 token。
@router.post("/login", response_model=Token)
async def login(
    payload: UserLogin,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> Token:
    """登录用户并把 Service 返回的 Refresh Token 写入安全 Cookie。"""
    user, session, refresh_token = await authenticate_user(
        db,
        payload,
        **_request_metadata(request),
    )
    _set_refresh_cookie(response, refresh_token, session.expires_at)
    return _token_response(user, session.id)


# 使用 HttpOnly Cookie 中的 refresh token 轮换登录会话并签发短效 access token。
@router.post("/refresh", response_model=Token)
async def refresh(request: Request, db: AsyncSession = Depends(get_db)) -> Token | Response:
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if not refresh_token:
        return _unauthorized_refresh_response()
    try:
        user, session, rotated_token = await rotate_and_commit_refresh_token(
            db,
            refresh_token,
            **_request_metadata(request),
        )
    except RefreshTokenError:
        return _unauthorized_refresh_response()

    response = JSONResponse(content=_token_response(user, session.id).model_dump(mode="json"))
    _set_refresh_cookie(response, rotated_token, session.expires_at)
    return response


# 匿名安全的会话探测始终返回 200；存在有效 Refresh Token 时同时完成轮换续期。
@router.get("/session", response_model=SessionStatus)
async def session_status(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if not refresh_token:
        return _session_status_response(SessionStatus(authenticated=False))
    try:
        user, session, rotated_token = await rotate_and_commit_refresh_token(
            db,
            refresh_token,
            **_request_metadata(request),
        )
    except RefreshTokenError:
        response = _session_status_response(SessionStatus(authenticated=False))
        _clear_refresh_cookie(response)
        return response

    token = _token_response(user, session.id)
    response = _session_status_response(
        SessionStatus(
            authenticated=True,
            access_token=token.access_token,
            token_type=token.token_type,
            expires_in=token.expires_in,
            user=token.user,
        )
    )
    _set_refresh_cookie(response, rotated_token, session.expires_at)
    return response


# 退出当前设备。即使 access token 已过期，也可以用 refresh cookie 撤销会话。
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> None:
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    await logout_session(db, refresh_token)
    _clear_refresh_cookie(response)


# 撤销当前用户的全部登录会话。
@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await logout_all_sessions(db, current_user.id)
    _clear_refresh_cookie(response)


# 获取当前登录用户的基础信息。
@router.get("/me", response_model=UserRead)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


def _token_response(user: User, session_id: str) -> Token:
    """根据已持久化的登录会话签发短效 Access Token 响应。"""
    return Token(
        access_token=create_access_token(str(user.id), session_id),
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserRead.model_validate(user),
    )


def _request_metadata(request: Request) -> dict[str, str | None]:
    """提取审计登录会话所需的客户端元数据。"""
    return {
        "user_agent": request.headers.get("user-agent"),
        "ip_address": request.client.host if request.client else None,
    }


def _set_refresh_cookie(response: Response, token: str, expires_at: datetime) -> None:
    """按统一安全配置写入 HttpOnly Refresh Token Cookie。"""
    now = datetime.now(timezone.utc)
    aware_expires_at = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=max(0, int((aware_expires_at - now).total_seconds())),
        expires=aware_expires_at,
        path=settings.auth_cookie_path,
        domain=settings.auth_cookie_domain,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )


def _clear_refresh_cookie(response: Response) -> None:
    """使用与写入完全一致的属性清理 Refresh Token Cookie。"""
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.auth_cookie_path,
        domain=settings.auth_cookie_domain,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )


def _unauthorized_refresh_response() -> JSONResponse:
    """返回统一的续期失败响应，并同步清理失效 Cookie。"""
    response = JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Invalid or expired refresh token"},
    )
    _clear_refresh_cookie(response)
    return response


def _session_status_response(payload: SessionStatus) -> JSONResponse:
    """返回禁止缓存的匿名安全会话探测结果。"""
    return JSONResponse(
        content=payload.model_dump(mode="json"),
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
