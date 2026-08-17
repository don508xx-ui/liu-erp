from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role
from app.core.audit import log_audit
from app.models.system import User
from app.models.inventory import InventoryItem, InventoryTxn, CustomerConsignLog
from app.schemas import Resp

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


class ItemIn(BaseModel):
    code: str
    name: str
    spec: Optional[str] = None
    unit: str
    category: str  # RAW_MATERIAL/PAINT_POWDER/CONSUMABLE/FINISHED_GOOD
    stock_qty: float = 0
    safety_qty: float = 0
    unit_cost: float = 0
    location: Optional[str] = None


@router.post("/items")
def create_item(body: ItemIn, user: User = Depends(require_role("WAREHOUSE", "ADMIN", "FINANCE")),
                db: Session = Depends(get_db)):
    if db.query(InventoryItem).filter(InventoryItem.code == body.code).first():
        raise HTTPException(400, "物料编码已存在")
    it = InventoryItem(**body.model_dump(), status="ACTIVE")
    db.add(it)
    db.flush()
    log_audit(db, user, "create", "inventory_item", it.id, after=body.model_dump())
    db.commit()
    return Resp.ok({"id": it.id})


@router.get("/items")
def list_items(category: Optional[str] = None, keyword: Optional[str] = None,
               user: User = Depends(require_role("WAREHOUSE", "MANAGER", "FINANCE", "ADMIN")), db: Session = Depends(get_db)):
    q = db.query(InventoryItem).filter(InventoryItem.status == "ACTIVE")
    if category:
        q = q.filter(InventoryItem.category == category)
    if keyword:
        q = q.filter(InventoryItem.name.contains(keyword) | InventoryItem.code.contains(keyword))
    rows = q.order_by(InventoryItem.id).all()
    return {"code": 0, "data": [{
        "id": i.id, "code": i.code, "name": i.name, "spec": i.spec, "unit": i.unit,
        "category": i.category, "stock_qty": float(i.stock_qty or 0),
        "safety_qty": float(i.safety_qty or 0), "unit_cost": float(i.unit_cost or 0),
        "location": i.location,
    } for i in rows]}


@router.get("/txns")
def list_txns(item_id: Optional[int] = None, txn_type: Optional[str] = None,
              page: int = 1, size: int = 20,
              user: User = Depends(require_role("WAREHOUSE", "FINANCE", "ADMIN")), db: Session = Depends(get_db)):
    q = db.query(InventoryTxn)
    if item_id:
        q = q.filter(InventoryTxn.item_id == item_id)
    if txn_type:
        q = q.filter(InventoryTxn.txn_type == txn_type)
    total = q.count()
    rows = q.order_by(InventoryTxn.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [{
        "id": t.id, "txn_no": t.txn_no, "txn_type": t.txn_type,
        "item_id": t.item_id, "quantity": float(t.quantity or 0),
        "unit_cost": float(t.unit_cost or 0), "amount": float(t.amount or 0),
        "work_order_id": t.work_order_id, "order_id": t.order_id,
        "ref_doc_type": t.ref_doc_type, "ref_doc_id": t.ref_doc_id,
        "occurred_at": t.occurred_at.isoformat() if t.occurred_at else None,
    } for t in rows]}


@router.get("/consign-log")
def consign_log(user: User = Depends(require_role("WAREHOUSE", "ADMIN")), db: Session = Depends(get_db)):
    rows = db.query(CustomerConsignLog).order_by(CustomerConsignLog.id.desc()).all()
    return {"code": 0, "data": [{
        "id": r.id, "order_id": r.order_id, "work_order_id": r.work_order_id,
        "customer_id": r.customer_id, "part_name": r.part_name, "part_spec": r.part_spec,
        "received_qty": float(r.received_qty or 0), "consumed_qty": float(r.consumed_qty or 0),
        "returned_qty": float(r.returned_qty or 0), "status": r.status,
    } for r in rows]}
