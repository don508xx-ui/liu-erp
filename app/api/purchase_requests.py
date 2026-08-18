from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role
from app.core.audit import log_audit
from app.models.system import User
from app.models.purchase import PurchaseRequest
from app.api.approvals import start_flow, bjt_now
from app.schemas import Resp

router = APIRouter(prefix="/api/purchase-requests", tags=["purchase_request"])


class PRItemIn(BaseModel):
    item_id: Optional[int] = None
    name: str
    spec: Optional[str] = None
    qty: float
    unit: str
    est_price: float


class PRIn(BaseModel):
    items: List[PRItemIn]
    reason: Optional[str] = None


@router.post("")
def create(body: PRIn, user: User = Depends(require_role("OPERATION", "FINANCE", "MANAGER", "DEPARTMENT_HEAD", "ADMIN")),
           db: Session = Depends(get_db)):
    total = sum(it.qty * it.est_price for it in body.items)
    seq = db.query(PurchaseRequest).count() + 1
    pr = PurchaseRequest(
        req_no=f"PR-{bjt_now().strftime('%Y%m%d')}-{seq:04d}",
        requester_user_id=user.id, items=[it.model_dump() for it in body.items],
        total_amount=total, status="DRAFT", reason=body.reason,
    )
    db.add(pr)
    db.flush()
    log_audit(db, user, "create", "purchase_request", pr.id, after={"req_no": pr.req_no})
    db.commit()
    return Resp.ok({"id": pr.id, "req_no": pr.req_no})


@router.post("/{pid}/submit")
def submit(pid: int, body: dict = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pr = db.query(PurchaseRequest).get(pid)
    if not pr:
        raise HTTPException(404, "采购申请不存在")
    if pr.status not in ("DRAFT", "REJECTED"):
        raise HTTPException(400, f"状态{pr.status}不可提交")
    pr.status = "SUBMITTED"
    db.flush()
    
    # 构建biz_data：包含表单数据和业务单据基本信息
    biz_data = {}
    if body and body.get("form_data"):
        biz_data = body["form_data"]
    # 添加业务单据基本信息
    biz_data["_biz_info"] = {
        "req_no": pr.req_no,
        "total_amount": float(pr.total_amount or 0),
        "reason": pr.reason,
        "items": pr.items,
    }
    
    # 启动审批流 - 优先CORE_PRODUCTION(11节点全链路),无则回退PROCUREMENT
    inst = start_flow(db, "CORE_PRODUCTION", pid, user, biz_data=biz_data)
    if not inst:
        inst = start_flow(db, "PROCUREMENT", pid, user, biz_data=biz_data)
    if inst:
        pr.approval_instance_id = inst.id
    else:
        pr.status = "APPROVED"
    db.commit()
    return Resp.ok({"id": pid, "status": pr.status, "approval_instance_id": pr.approval_instance_id})


@router.get("")
def list_(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(PurchaseRequest).order_by(PurchaseRequest.id.desc()).all()
    return {"code": 0, "data": [{
        "id": r.id, "req_no": r.req_no, "status": r.status,
        "total_amount": float(r.total_amount or 0), "reason": r.reason,
        "approval_instance_id": r.approval_instance_id,
        "items": r.items,
    } for r in rows]}
