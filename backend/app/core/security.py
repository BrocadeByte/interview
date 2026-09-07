from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings


password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@dataclass(frozen=True)
class TokenClaims:
    subject: str
    session_id: str
    token_id: str
    token_type: str
    expires_at: datetime


def hash_password(password: str) -> str:
    """使用配置的密码哈希算法生成可存储的密码摘要。"""
    return password_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """校验明文密码是否与已保存的密码哈希匹配。"""
    return password_context.verify(password, password_hash)


def create_access_token(
    subject: str,
    session_id: str,
    *,
    expires_delta: timedelta | None = None,
) -> str:
    """签发包含用户、登录会话、唯一标识和有效期的访问令牌。"""
    now = datetime.now(timezone.utc)
    lifetime = expires_delta if expires_delta is not None else timedelta(
        minutes=settings.access_token_expire_minutes
    )
    expires_at = now + lifetime
    payload = {
        "sub": subject,
        "sid": session_id,
        "jti": str(uuid4()),
        "type": "access",
        "iat": now,
        "exp": expires_at,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    subject: str,
    session_id: str,
    expires_at: datetime,
    *,
    token_id: str | None = None,
    issued_at: datetime | None = None,
) -> str:
    """统一签发与过期时间的时区后，使用刷新密钥签发绑定登录会话的刷新令牌。"""
    now = issued_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    payload = {
        "sub": subject,
        "sid": session_id,
        "jti": token_id or str(uuid4()),
        "type": "refresh",
        "iat": now,
        "exp": expires_at,
    }
    return jwt.encode(payload, settings.refresh_jwt_secret_key, algorithm=settings.jwt_algorithm)


def hash_refresh_token(token: str) -> str:
    """计算刷新令牌的 SHA-256 摘要，供服务端保存和比对。"""
    return sha256(token.encode("utf-8")).hexdigest()


def decode_access_token(token: str) -> TokenClaims | None:
    """使用访问密钥验证并解析访问令牌，无效时返回空值。"""
    return _decode_token(token, expected_type="access", secret=settings.jwt_secret_key)


def decode_refresh_token(token: str) -> TokenClaims | None:
    """使用刷新密钥验证并解析刷新令牌，无效时返回空值。"""
    return _decode_token(token, expected_type="refresh", secret=settings.refresh_jwt_secret_key)


def _decode_token(token: str, *, expected_type: str, secret: str) -> TokenClaims | None:
    """验证令牌签名、有效期、类型及必需声明，解析失败时返回空值。"""
    try:
        payload = jwt.decode(token, secret, algorithms=[settings.jwt_algorithm])
        subject = payload.get("sub")
        session_id = payload.get("sid")
        token_id = payload.get("jti")
        token_type = payload.get("type")
        expires_at = payload.get("exp")
        if not all((subject, session_id, token_id, expires_at)) or token_type != expected_type:
            return None
        return TokenClaims(
            subject=str(subject),
            session_id=str(session_id),
            token_id=str(token_id),
            token_type=str(token_type),
            expires_at=datetime.fromtimestamp(float(expires_at), tz=timezone.utc),
        )
    except (JWTError, TypeError, ValueError, OverflowError):
        return None
