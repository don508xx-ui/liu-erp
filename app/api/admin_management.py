"""管理员: 用户+角色管理 (仅ADMIN角色)"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, object_session
from pydantic import BaseModel
from typing import Optional, List

from app.core.db import get_db
from app.core.auth import get_current_user, hash_password
from app.models.system import User, Role
from app.schemas import Resp, PageResp

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _admin_only(user: User) -> None:
    """所有管理员API共用: 非ADMIN直接403"""
    if not user.role_id:
        raise HTTPException(403, "无角色")
    sess = object_session(user)
    role = sess.query(Role).filter(Role.id == user.role_id).first() if sess else None
    if not role:
        raise HTTPException(403, "角色不存在")
    if role.code != "ADMIN":
        raise HTTPException(403, "仅系统管理员可操作")


# ========== 角色管理 ==========

@router.get("/roles")
def list_roles(db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    _admin_only(user)
    roles = db.query(Role).order_by(Role.id.asc()).all()
    return Resp.ok([{
        "id": r.id, "code": r.code, "name": r.name,
        "description": getattr(r, "description", None) or "",
    } for r in roles])


class RoleCreate(BaseModel):
    code: str
    name: str
    description: Optional[str] = ""


@router.post("/roles")
def create_role(body: RoleCreate,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    _admin_only(user)
    if db.query(Role).filter(Role.code == body.code.upper()).first():
        raise HTTPException(400, f"角色编码 {body.code} 已存在")
    r = Role(code=body.code.upper(), name=body.name, description=body.description)
    db.add(r); db.commit(); db.refresh(r)
    return Resp.ok({"id": r.id, "code": r.code, "name": r.name})


class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


@router.put("/roles/{rid}")
def update_role(rid: int, body: RoleUpdate,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    _admin_only(user)
    r = db.query(Role).filter(Role.id == rid).first()
    if not r: raise HTTPException(404, "角色不存在")
    if body.name is not None: r.name = body.name
    if body.description is not None: r.description = body.description
    db.commit()
    return Resp.ok({"id": r.id})


@router.delete("/roles/{rid}")
def delete_role(rid: int,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    _admin_only(user)
    r = db.query(Role).filter(Role.id == rid).first()
    if not r: raise HTTPException(404, "角色不存在")
    # 有用户占用的角色禁止删除
    cnt = db.query(User).filter(User.role_id == rid).count()
    if cnt > 0: raise HTTPException(400, f"该角色下仍有 {cnt} 个用户,无法删除")
    db.delete(r); db.commit()
    return Resp.ok()


# ========== 用户管理 ==========

@router.get("/users")
def list_users(page: int = 1, size: int = 50, keyword: str = "",
               role_id: Optional[int] = None,
               db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    _admin_only(user)
    q = db.query(User)
    if keyword:
        q = q.filter((User.username.like(f"%{keyword}%")) | (User.name.like(f"%{keyword}%")))
    if role_id:
        q = q.filter(User.role_id == role_id)
    total = q.count()
    rows = q.order_by(User.id.desc()).offset((page - 1) * size).limit(size).all()
    role_map = {r.id: {"id": r.id, "code": r.code, "name": r.name} for r in db.query(Role).all()}
    return PageResp(
        total=total,
        data=[{
            "id": u.id,
            "username": u.username,
            "real_name": getattr(u, "real_name", None) or u.name or "",
            "name": u.name or "",
            "role": role_map.get(u.role_id),
            "status": u.status or "ACTIVE",
            "created_at": u.created_at.isoformat() if getattr(u, "created_at", None) else "",
        } for u in rows]
    )


class UserCreate(BaseModel):
    username: str
    real_name: str
    role_id: int
    password: str = "123456"
    status: Optional[str] = "ACTIVE"


@router.post("/users")
def create_user(body: UserCreate,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    _admin_only(user)
    if len(body.username) < 2: raise HTTPException(400, "账号至少2位")
    if len(body.password) < 4: raise HTTPException(400, "密码至少4位")
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(400, f"账号 {body.username} 已存在")
    role = db.query(Role).filter(Role.id == body.role_id).first()
    if not role: raise HTTPException(400, "所选角色不存在")
    u = User(
        username=body.username,
        password_hash=hash_password(body.password),
        name=body.real_name,
        role_id=body.role_id,
        status=body.status or "ACTIVE",
    )
    db.add(u); db.commit(); db.refresh(u)
    return Resp.ok({"id": u.id, "username": u.username})


class UserUpdate(BaseModel):
    real_name: Optional[str] = None
    role_id: Optional[int] = None
    status: Optional[str] = None
    password: Optional[str] = None


@router.put("/users/{uid}")
def update_user(uid: int, body: UserUpdate,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    _admin_only(user)
    u = db.query(User).filter(User.id == uid).first()
    if not u: raise HTTPException(404, "用户不存在")
    if body.real_name is not None: u.name = body.real_name
    if body.role_id is not None:
        if not db.query(Role).filter(Role.id == body.role_id).first():
            raise HTTPException(400, "角色不存在")
        u.role_id = body.role_id
    if body.status is not None: u.status = body.status
    if body.password:
        if len(body.password) < 4: raise HTTPException(400, "密码至少4位")
        u.password_hash = hash_password(body.password)
    db.commit()
    return Resp.ok({"id": u.id})


@router.delete("/users/{uid}")
def delete_user(uid: int,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    _admin_only(user)
    if uid == user.id:
        raise HTTPException(400, "不能删除自己")
    u = db.query(User).filter(User.id == uid).first()
    if not u: raise HTTPException(404, "用户不存在")
    db.delete(u); db.commit()
    return Resp.ok()
