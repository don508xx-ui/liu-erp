from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role
from app.core.audit import log_audit
from app.core.event_bus import emit
from app.api.approvals import bjt_now
from app.models.system import User
from app.models.inventory import MaterialRequisition, InventoryItem
from app.schemas import Resp

router = APIRouter(prefix="/api/requisitions", tags=["requisition"])


@router.get("")
def list_(status: Optional[str] = None, page: int = 1, size: int = 20,
          user: User = Depends(require_role("OPERATION", "MANAGER", "ADMIN")), db: Session = Depends(get_db)):
    q = db.query(MaterialRequisition)
    if status:
        q = q.filter(MaterialRequisition.status == status)
    total = q.count()
    rows = q.order_by(MaterialRequisition.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [{
        "id": r.id, "req_no": r.req_no, "work_order_id": r.work_order_id,
        "status": r.status, "items": r.items,
        "confirmed_at": r.confirmed_at.isoformat() if r.confirmed_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]}


@router.post("/{rid}/confirm")
def confirm(rid: int, user: User = Depends(require_role("OPERATION", "ADMIN")),
            db: Session = Depends(get_db)):
    r = db.query(MaterialRequisition).get(rid)
    if not r:
        raise HTTPException(404, "领料单不存在")
    if r.status != "PENDING":
        raise HTTPException(400, f"领料单状态{r.status}不可确认")
    # 库存校验
    for it in (r.items or []):
        item = db.query(InventoryItem).get(it["item_id"])
        if not item:
            raise HTTPException(400, f"物料{it.get('item_name')}不存在")
        if float(item.stock_qty or 0) < float(it["qty"]):
            raise HTTPException(400, f"物料{item.name}库存不足:当前{item.stock_qty},需{it['qty']}")
    before = r.status
    r.status = "CONFIRMED"
    r.warehouse_keeper_user_id = user.id if user else None
    r.confirmed_at = bjt_now()
    log_audit(db, user, "state_change", "requisition", rid, before=before, after="CONFIRMED")
    db.flush()
    emit(db, "material.confirmed", "requisition", rid, {"req_no": r.req_no}, user)
    db.commit()
    return Resp.ok({"id": rid, "status": "CONFIRMED"})


@router.post("/{rid}/reject")
def reject(rid: int, user: User = Depends(require_role("OPERATION", "ADMIN")),
           db: Session = Depends(get_db)):
    r = db.query(MaterialRequisition).get(rid)
    if not r:
        raise HTTPException(404, "领料单不存在")
    if r.status != "PENDING":
        raise HTTPException(400, "状态不可拒")
    before = r.status
    r.status = "REJECTED"
    log_audit(db, user, "state_change", "requisition", rid, before=before, after=r.status)
    db.commit()
    return Resp.ok({"id": rid, "status": r.status})
