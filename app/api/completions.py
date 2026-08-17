from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role
from app.core.audit import log_audit
from app.core.event_bus import emit
from app.api.approvals import bjt_now
from app.models.system import User
from app.models.workshop import WorkOrder, Completion, CompletionItem
from app.models.inventory import InventoryItem, MaterialRequisition
from app.schemas import Resp

router = APIRouter(prefix="/api/completions", tags=["completion"])


class CItemIn(BaseModel):
    item_id: Optional[int] = None
    item_name: Optional[str] = None
    theoretical_qty: float  # BOM理论用量
    actual_qty: float       # 实际用量
    return_qty: float = 0
    unit_cost: float = 0


class CompletionIn(BaseModel):
    work_order_id: int
    finished_qty: float
    qualified_qty: float
    rework_qty: float = 0
    scrap_qty: float = 0
    labor_hours: float = 0
    labor_cost: float = 0
    overhead_cost: float = 0
    remark: Optional[str] = None
    items: List[CItemIn] = []


@router.post("")
def create(body: CompletionIn, user: User = Depends(require_role("MANAGER", "ADMIN")),
           db: Session = Depends(get_db)):
    wo = db.query(WorkOrder).get(body.work_order_id)
    if not wo:
        raise HTTPException(400, "加工单不存在")
    if wo.status not in ("RELEASED", "PROCESSING", "COMPLETED"):
        raise HTTPException(400, f"加工单状态{wo.status}不可填完工")
    seq = db.query(Completion).count() + 1
    cp = Completion(
        completion_no=f"CP-{bjt_now().strftime('%Y%m%d')}-{seq:04d}",
        work_order_id=body.work_order_id, status="DRAFT",
        finished_qty=body.finished_qty, qualified_qty=body.qualified_qty,
        rework_qty=body.rework_qty, scrap_qty=body.scrap_qty,
        labor_hours=body.labor_hours, labor_cost=body.labor_cost,
        overhead_cost=body.overhead_cost,
        operator_user_id=user.id, remark=body.remark,
    )
    db.add(cp)
    db.flush()
    # 自动从领料单带出理论用量
    req = db.query(MaterialRequisition).filter(
        MaterialRequisition.work_order_id == wo.id,
        MaterialRequisition.status == "CONFIRMED"
    ).first()
    for it in body.items:
        ci = CompletionItem(
            completion_id=cp.id, item_id=it.item_id, item_name=it.item_name,
            theoretical_qty=it.theoretical_qty, actual_qty=it.actual_qty,
            return_qty=it.return_qty, unit_cost=it.unit_cost,
        )
        db.add(ci)
    # 若没填明细,自动从领料单拉
    if not body.items and req:
        for ri in (req.items or []):
            item = db.query(InventoryItem).get(ri["item_id"])
            db.add(CompletionItem(
                completion_id=cp.id, item_id=ri["item_id"], item_name=ri.get("item_name"),
                theoretical_qty=ri.get("theoretical_qty", ri["qty"]),
                actual_qty=ri["qty"],  # 默认实际=领用
                return_qty=0,
                unit_cost=float(item.unit_cost or 0) if item else 0,
            ))
    db.flush()
    log_audit(db, user, "create", "completion", cp.id, after={"completion_no": cp.completion_no})
    from app.api.approvals import start_flow
    inst = start_flow(db, "COMPLETION", cp.id, user)
    if inst:
        cp.approval_instance_id = inst.id
    db.commit()
    return Resp.ok({"id": cp.id, "completion_no": cp.completion_no})


@router.post("/{cid}/confirm")
def confirm(cid: int, user: User = Depends(require_role("OPERATION", "MANAGER", "ADMIN")),
            db: Session = Depends(get_db)):
    """确认完工单 - 若存在进行中的COMPLETION流程实例,则禁止直接确认,需走审批中心"""
    cp = db.query(Completion).get(cid)
    if not cp:
        raise HTTPException(404, "完工单不存在")
    if cp.status != "DRAFT":
        raise HTTPException(400, f"完工单状态{cp.status}不可确认")
    # 检查是否有关联的进行中COMPLETION流程实例
    from app.models.approval import FlowInstance
    running = db.query(FlowInstance).filter(
        FlowInstance.biz_type == "COMPLETION",
        FlowInstance.biz_id == cid,
        FlowInstance.status == "RUNNING",
    ).first()
    if running:
        raise HTTPException(400, "该完工单已进入审批流程,请到审批中心处理当前节点")
    before = cp.status
    cp.status = "CONFIRMED"
    cp.confirmed_by_user_id = user.id
    cp.confirmed_at = bjt_now()
    log_audit(db, user, "state_change", "completion", cid, before=before, after=cp.status)
    db.flush()
    emit(db, "completion.confirmed", "completion", cid, {"completion_no": cp.completion_no}, user)
    db.commit()
    return Resp.ok({"id": cid, "status": cp.status, "total_cost": float(cp.total_cost or 0)})


@router.get("")
def list_(page: int = 1, size: int = 20,
          user: User = Depends(require_role("MANAGER", "OPERATION", "ADMIN")), db: Session = Depends(get_db)):
    q = db.query(Completion)
    total = q.count()
    rows = q.order_by(Completion.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [_to_dict(c, db) for c in rows]}


def _to_dict(c: Completion, db: Session) -> dict:
    wo = db.query(WorkOrder).get(c.work_order_id) if c.work_order_id else None
    return {
        "id": c.id, "completion_no": c.completion_no,
        "work_order_id": c.work_order_id, "work_order_no": wo.work_order_no if wo else "",
        "status": c.status,
        "finished_qty": float(c.finished_qty or 0), "qualified_qty": float(c.qualified_qty or 0),
        "rework_qty": float(c.rework_qty or 0), "scrap_qty": float(c.scrap_qty or 0),
        "labor_hours": float(c.labor_hours or 0), "labor_cost": float(c.labor_cost or 0),
        "overhead_cost": float(c.overhead_cost or 0), "total_cost": float(c.total_cost or 0),
        "confirmed_at": c.confirmed_at.isoformat() if c.confirmed_at else None,
        "items": [{
            "item_id": ci.item_id, "item_name": ci.item_name,
            "theoretical_qty": float(ci.theoretical_qty or 0),
            "actual_qty": float(ci.actual_qty or 0),
            "return_qty": float(ci.return_qty or 0),
            "utilization_rate": float(ci.utilization_rate or 0),
            "cost_amount": float(ci.cost_amount or 0),
        } for ci in c.items],
    }
