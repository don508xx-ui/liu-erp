from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.core.db import get_db
from app.core.auth import hash_password, verify_password, create_access_token, get_current_user
from app.models.system import User, Role
from app.core.audit import log_audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    username = body.username.strip() if body.username else ""
    u = db.query(User).filter(User.username == username).first()
    if not u or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    if u.status != "ACTIVE":
        raise HTTPException(401, "用户已禁用")
    role = db.query(Role).filter(Role.id == u.role_id).first() if u.role_id else None
    token = create_access_token(str(u.id), {"username": u.username, "role": role.code if role else None})
    log_audit(db, u, "login", "user", u.id)
    db.commit()
    return {"token": token, "user": {"id": u.id, "name": u.name, "username": u.username, "role": role.code if role else None}}


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    role = db.query(Role).filter(Role.id == user.role_id).first() if user.role_id else None
    return {"id": user.id, "name": user.name, "username": user.username, "role": role.code if role else None, "phone": user.phone, "email": user.email}
