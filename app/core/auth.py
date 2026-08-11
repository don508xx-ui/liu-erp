from datetime import datetime, timedelta
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from app.config import settings
from app.core.db import get_db
from app.models.system import User, AgentApiToken

# 用pbkdf2_sha256避免bcrypt版本兼容问题(passlib 1.7.4 + bcrypt 4+/5 不兼容)
pwd_ctx = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def hash_password(p: str) -> str:
    return pwd_ctx.hash(p)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_ctx.verify(plain, hashed)
    except Exception:
        return False


def create_access_token(sub: str, extra: dict = None) -> str:
    payload = {"sub": sub, "exp": datetime.utcnow() + timedelta(minutes=settings.JWT_TTL_MINUTES)}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
    except JWTError:
        return None


def get_current_user(token: str = Depends(oauth2), db: Session = Depends(get_db)) -> User:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token无效")
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user or user.status != "ACTIVE":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不可用")
    return user


def get_current_agent(token: str = Depends(oauth2), db: Session = Depends(get_db)):
    """Agent scoped token - 用verify校验(hash盐随机,不能用==)"""
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无token")
    tokens = db.query(AgentApiToken).filter(AgentApiToken.status == "ACTIVE").all()
    for t in tokens:
        if verify_password(token, t.token_hash):
            if t.expires_at and t.expires_at < datetime.utcnow():
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token过期")
            return t
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Agent token无效")
