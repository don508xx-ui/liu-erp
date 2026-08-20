from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role, apply_scope_filter, mask_customer, get_user_role_code
from app.core.audit import log_audit
from app.models.system import User
from app.models.customer import Customer
from app.schemas import Resp

router = APIRouter(prefix="/api/customers", tags=["customer"])


class CustomerIn(BaseModel):
    code: str
    name: str
    short_code: Optional[str] = None
    tax_no: Optional[str] = None
    address: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    industry: Optional[str] = None
    settlement_cycle: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account: Optional[str] = None
    default_company_id: Optional[int] = None
    remark: Optional[str] = None
    extra: Optional[dict] = None


@router.post("")
def create(body: CustomerIn, user: User = Depends(require_role("SALES", "OPERATION", "GM", "ADMIN")),
           db: Session = Depends(get_db)):
    if db.query(Customer).filter(Customer.code == body.code).first():
        raise HTTPException(400, "客户编码已存在")
    data = body.model_dump()
    # 销售创建的客户归属自己;其他角色不归属(共享)
    if get_user_role_code(user, db) == "SALES":
        data["owner_user_id"] = user.id
    c = Customer(**data, status="ACTIVE")
    db.add(c)
    db.flush()
    log_audit(db, user, "create", "customer", c.id, after=body.model_dump())
    db.commit()
    return Resp.ok({"id": c.id, "code": c.code})


@router.get("")
def list_(keyword: Optional[str] = None, page: int = 1, size: int = 20,
          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Customer).filter(Customer.status == "ACTIVE")
    q = apply_scope_filter(user, db, q, "customers")
    if keyword:
        q = q.filter(Customer.name.contains(keyword) | Customer.code.contains(keyword) | Customer.short_code.contains(keyword))
    total = q.count()
    rows = q.order_by(Customer.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [mask_customer(user, db, c) for c in rows]}


@router.get("/all")
def all_(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """下拉用:不分页,脱敏后返回"""
    q = db.query(Customer).filter(Customer.status == "ACTIVE")
    q = apply_scope_filter(user, db, q, "customers")
    rows = q.order_by(Customer.id.desc()).all()
    return Resp.ok([mask_customer(user, db, c) for c in rows])


@router.get("/{cid}")
def get(cid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == cid).first()
    if not c:
        raise HTTPException(404, "客户不存在")
    # 数据隔离: SALES只见自己的客户, ADMIN/OPERATION/GM豁免
    rc = get_user_role_code(user, db)
    if rc == "SALES" and c.owner_user_id and c.owner_user_id != user.id:
        raise HTTPException(403, "无权查看该客户")
    return Resp.ok(mask_customer(user, db, c))


@router.put("/{cid}")
def update(cid: int, body: CustomerIn, user: User = Depends(require_role("SALES", "OPERATION", "ADMIN")),
           db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == cid).first()
    if not c:
        raise HTTPException(404, "客户不存在")
    # 数据隔离: SALES只能改自己的客户
    rc = get_user_role_code(user, db)
    if rc == "SALES" and c.owner_user_id and c.owner_user_id != user.id:
        raise HTTPException(403, "无权修改该客户")
    before = mask_customer(user, db, c)
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    log_audit(db, user, "update", "customer", cid, before=before, after=mask_customer(user, db, c))
    db.commit()
    return Resp.ok({"id": cid})


@router.delete("/{cid}")
def delete(cid: int, user: User = Depends(require_role("ADMIN")),
           db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == cid).first()
    if not c:
        raise HTTPException(404, "客户不存在")
    c.status = "DELETED"
    log_audit(db, user, "delete", "customer", cid, before=mask_customer(user, db, c))
    db.commit()
    return Resp.ok({"id": cid})
