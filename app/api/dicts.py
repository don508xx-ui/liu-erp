"""通用字典API - 工艺/涂料/工件/行业/结算周期/车间/开票类型等下拉源"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role
from app.core.audit import log_audit
from app.models.system import User
from app.models.dict import Dict
from app.schemas import Resp

router = APIRouter(prefix="/api/dicts", tags=["dict"])


class DictIn(BaseModel):
    type: str
    code: str
    name: str
    parent_code: Optional[str] = None
    sort: int = 0
    remark: Optional[str] = None


@router.get("")
def list_(type: Optional[str] = None, parent_code: Optional[str] = None,
          keyword: Optional[str] = None,
          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Dict).filter(Dict.status == "ACTIVE")
    if type:
        q = q.filter(Dict.type == type)
    if parent_code:
        q = q.filter(Dict.parent_code == parent_code)
    if keyword:
        q = q.filter(Dict.name.contains(keyword) | Dict.code.contains(keyword))
    rows = q.order_by(Dict.type, Dict.sort, Dict.id).all()
    return {"code": 0, "data": [_to_dict(d) for d in rows]}


@router.get("/grouped")
def grouped(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """按type分组返回,供前端下拉一次性加载"""
    rows = db.query(Dict).filter(Dict.status == "ACTIVE").order_by(Dict.type, Dict.sort, Dict.id).all()
    out: dict = {}
    for d in rows:
        out.setdefault(d.type, []).append(_to_dict(d))
    return Resp.ok(out)


@router.get("/{did}")
def get(did: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    d = db.query(Dict).filter(Dict.id == did).first()
    if not d:
        raise HTTPException(404, "字典项不存在")
    return Resp.ok(_to_dict(d))


@router.post("")
def create(body: DictIn, user: User = Depends(require_role("ADMIN")),
           db: Session = Depends(get_db)):
    if db.query(Dict).filter(Dict.type == body.type, Dict.code == body.code).first():
        raise HTTPException(400, "字典编码已存在")
    d = Dict(**body.model_dump(), status="ACTIVE")
    db.add(d)
    db.flush()
    log_audit(db, user, "create", "dict", d.id, after=body.model_dump())
    db.commit()
    return Resp.ok({"id": d.id})


@router.put("/{did}")
def update(did: int, body: DictIn, user: User = Depends(require_role("ADMIN")),
           db: Session = Depends(get_db)):
    d = db.query(Dict).filter(Dict.id == did).first()
    if not d:
        raise HTTPException(404, "字典项不存在")
    before = _to_dict(d)
    for k, v in body.model_dump().items():
        setattr(d, k, v)
    log_audit(db, user, "update", "dict", did, before=before, after=_to_dict(d))
    db.commit()
    return Resp.ok({"id": did})


@router.delete("/{did}")
def delete(did: int, user: User = Depends(require_role("ADMIN")),
           db: Session = Depends(get_db)):
    d = db.query(Dict).filter(Dict.id == did).first()
    if not d:
        raise HTTPException(404, "字典项不存在")
    d.status = "DELETED"
    log_audit(db, user, "delete", "dict", did, before=_to_dict(d))
    db.commit()
    return Resp.ok({"id": did})


def _to_dict(d: Dict) -> dict:
    return {
        "id": d.id, "type": d.type, "code": d.code, "name": d.name,
        "parent_code": d.parent_code, "sort": d.sort, "status": d.status,
        "remark": d.remark,
    }
