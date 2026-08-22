from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.core.db import get_db
from app.core.auth import hash_password, verify_password, create_access_token, get_current_user
from app.models.system import User, Role
from app.core.audit import log_audit
import time
import threading

router = APIRouter(prefix="/api/auth", tags=["auth"])


# === 登录防爆破: 内存滑动窗口, 同IP 1分钟内失败5次锁5分钟 ===
_fail_log = {}
_lock = threading.Lock()
MAX_FAILS = 5
WIN = 60
LOCKOUT = 300


def _check_rate(ip: str):
    now = time.time()
    with _lock:
        rec = _fail_log.get(ip)
        if rec and now < rec.get("locked_until", 0):
            raise HTTPException(429, f"尝试过于频繁,请{int(rec['locked_until']-now)}秒后再试")
        if rec:
            rec["fails"] = [t for t in rec["fails"] if now - t < WIN]


def _record_fail(ip: str):
    now = time.time()
    with _lock:
        rec = _fail_log.setdefault(ip, {"fails": [], "locked_until": 0})
        rec["fails"].append(now)
        if len(rec["fails"]) >= MAX_FAILS:
            rec["locked_until"] = now + LOCKOUT
            rec["fails"] = []


def _clear_fail(ip: str):
    with _lock:
        _fail_log.pop(ip, None)


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    _check_rate(ip)
    username = body.username.strip() if body.username else ""
    u = db.query(User).filter(User.username == username).first()
    if not u or not verify_password(body.password, u.password_hash):
        _record_fail(ip)
        raise HTTPException(401, "用户名或密码错误")
    if u.status != "ACTIVE":
        raise HTTPException(401, "用户已禁用")
    _clear_fail(ip)
    role = db.query(Role).filter(Role.id == u.role_id).first() if u.role_id else None
    token = create_access_token(str(u.id), {"username": u.username, "role": role.code if role else None})
    log_audit(db, u, "login", "user", u.id)
    db.commit()
    return {"token": token, "user": {"id": u.id, "name": u.name, "username": u.username, "role": role.code if role else None}}


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    role = db.query(Role).filter(Role.id == user.role_id).first() if user.role_id else None
    return {"id": user.id, "name": user.name, "username": user.username, "role": role.code if role else None, "phone": user.phone, "email": user.email}
