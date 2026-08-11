from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role, apply_scope_filter
from app.core.audit import log_audit
from app.core.event_bus import emit
from app.models.system import User
from app.models.order import Order, OrderItem
from app.models.workshop import WorkOrder
from app.schemas import Resp

router = APIRouter(prefix="/api/work-orders", tags=["work_order"])


class WOIn(BaseModel):
    order_id: int
    order_item_id: Optional[int] = None
    batch_no: str
    workshop: str  # A/B
    plan_qty: float
    plan_finish_date: Optional[datetime] = None
    work_manager_user_id: Optional[int] = None
    outsource_supplier_id: Optional[int] = None
    outsource_cost: float = 0
    remark: Optional[str] = None


@router.post("")
def create(body: WOIn, user: User = Depends(require_role("OPERATION", "ADMIN")),
           db: Session = Depends(get_db)):
    o = db.query(Order).get(body.order_id)
    if not o:
        raise HTTPException(400, "订单不存在")
    if o.status != "EFFECTIVE":
        raise HTTPException(400, "订单未生效,不可下加工单")
    seq = db.query(WorkOrder).count() + 1
    wo = WorkOrder(
        work_order_no=f"WO-{datetime.utcnow().strftime('%Y%m%d')}-{seq:04d}",
        order_id=body.order_id, order_item_id=body.order_item_id,
        batch_no=body.batch_no, workshop=body.workshop,
        status="CREATED", plan_qty=body.plan_qty,
        plan_finish_date=body.plan_finish_date,
        work_manager_user_id=body.work_manager_user_id,
        outsource_supplier_id=body.outsource_supplier_id,
        outsource_cost=body.outsource_cost,
        operator_user_id=user.id,
        remark=body.remark,
    )
    db.add(wo)
    db.flush()
    log_audit(db, user, "create", "work_order", wo.id, after={"work_order_no": wo.work_order_no})
    db.commit()
    return Resp.ok({"id": wo.id, "work_order_no": wo.work_order_no})


@router.post("/{wid}/release")
def release(wid: int, user: User = Depends(require_role("OPERATION", "ADMIN")),
            db: Session = Depends(get_db)):
    wo = db.query(WorkOrder).get(wid)
    if not wo:
        raise HTTPException(404, "加工单不存在")
    if wo.status != "CREATED":
        raise HTTPException(400, f"加工单状态{wo.status}不可下达")
    before = wo.status
    wo.status = "RELEASED"
    wo.released_at = datetime.utcnow()
    log_audit(db, user, "state_change", "work_order", wid, before=before, after=wo.status)
    db.flush()
    emit(db, "work_order.released", "work_order", wid, {"work_order_no": wo.work_order_no}, user)
    db.commit()
    return Resp.ok({"id": wid, "status": wo.status})


@router.get("")
def list_(status: Optional[str] = None, page: int = 1, size: int = 20,
          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(WorkOrder)
    q = apply_scope_filter(user, db, q, "work_orders")
    if status:
        q = q.filter(WorkOrder.status == status)
    total = q.count()
    rows = q.order_by(WorkOrder.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [_to_dict(w, db) for w in rows]}


@router.get("/{wid}")
def get(wid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    w = db.query(WorkOrder).get(wid)
    if not w:
        raise HTTPException(404, "加工单不存在")
    return Resp.ok(_to_dict(w, db))


def _to_dict(w: WorkOrder, db: Session) -> dict:
    o = db.query(Order).get(w.order_id) if w.order_id else None
    return {
        "id": w.id, "work_order_no": w.work_order_no,
        "order_id": w.order_id, "order_no": o.order_no if o else "",
        "batch_no": w.batch_no, "workshop": w.workshop, "status": w.status,
        "plan_qty": float(w.plan_qty or 0), "actual_qty": float(w.actual_qty or 0),
        "released_at": w.released_at.isoformat() if w.released_at else None,
        "completed_at": w.completed_at.isoformat() if w.completed_at else None,
        "work_manager_user_id": w.work_manager_user_id,
        "outsource_supplier_id": w.outsource_supplier_id,
        "outsource_cost": float(w.outsource_cost or 0),
        "rework_count": w.rework_count, "remark": w.remark,
    }
