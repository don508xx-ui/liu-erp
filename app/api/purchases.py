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
from app.models.purchase import Supplier, PurchaseRequest, Purchase, PurchaseItem
from app.models.inventory import InventoryItem
from app.schemas import Resp

router = APIRouter(prefix="/api/purchases", tags=["purchase"])


class SupplierIn(BaseModel):
    code: str
    name: str
    contact: Optional[str] = None
    phone: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account: Optional[str] = None


@router.post("/suppliers")
def create_supplier(body: SupplierIn, user: User = Depends(require_role("FINANCE", "OPERATION", "ADMIN")),
                    db: Session = Depends(get_db)):
    if db.query(Supplier).filter(Supplier.code == body.code).first():
        raise HTTPException(400, "编码已存在")
    s = Supplier(**body.model_dump(), status="ACTIVE")
    db.add(s)
    db.flush()
    db.commit()
    return Resp.ok({"id": s.id})


@router.get("/suppliers")
def list_suppliers(user: User = Depends(require_role("OPERATION", "FINANCE", "ADMIN")), db: Session = Depends(get_db)):
    rows = db.query(Supplier).filter(Supplier.status == "ACTIVE").all()
    return {"code": 0, "data": [{"id": s.id, "code": s.code, "name": s.name, "contact": s.contact, "phone": s.phone} for s in rows]}


class PItemIn(BaseModel):
    item_id: Optional[int] = None
    item_name: str
    spec: Optional[str] = None
    qty: float
    unit: str
    unit_price: float


class POIn(BaseModel):
    supplier_id: int
    items: List[PItemIn]
    request_id: Optional[int] = None
    remark: Optional[str] = None


@router.post("")
def create_po(body: POIn, user: User = Depends(require_role("FINANCE", "OPERATION", "ADMIN")),
              db: Session = Depends(get_db)):
    sup = db.query(Supplier).get(body.supplier_id)
    if not sup:
        raise HTTPException(400, "供应商不存在")
    total = sum(it.qty * it.unit_price for it in body.items)
    seq = db.query(Purchase).count() + 1
    po = Purchase(
        po_no=f"PO-{bjt_now().strftime('%Y%m%d')}-{seq:04d}",
        supplier_id=body.supplier_id, request_id=body.request_id,
        status="DRAFT", total_amount=total, remark=body.remark,
    )
    db.add(po)
    db.flush()
    for it in body.items:
        db.add(PurchaseItem(
            purchase_id=po.id, item_id=it.item_id, item_name=it.item_name,
            spec=it.spec, qty=it.qty, unit=it.unit, unit_price=it.unit_price,
            amount=it.qty * it.unit_price,
        ))
    db.flush()
    log_audit(db, user, "create", "purchase", po.id, after={"po_no": po.po_no})
    db.commit()
    return Resp.ok({"id": po.id, "po_no": po.po_no})


@router.post("/{pid}/order")
def order_po(pid: int, user: User = Depends(require_role("FINANCE", "ADMIN")),
             db: Session = Depends(get_db)):
    po = db.query(Purchase).get(pid)
    if not po:
        raise HTTPException(404, "采购单不存在")
    if po.status != "DRAFT":
        raise HTTPException(400, f"状态{po.status}不可下单")
    po.status = "ORDERED"
    po.ordered_at = bjt_now()
    db.commit()
    return Resp.ok({"id": pid, "status": po.status})


@router.post("/{pid}/receive")
def receive_po(pid: int, user: User = Depends(require_role("OPERATION", "ADMIN")),
               db: Session = Depends(get_db)):
    po = db.query(Purchase).get(pid)
    if not po:
        raise HTTPException(404, "采购单不存在")
    if po.status != "ORDERED":
        raise HTTPException(400, f"状态{po.status}不可入库")
    log_audit(db, user, "state_change", "purchase", pid, before=po.status, after="RECEIVED")
    db.flush()
    emit(db, "purchase.received", "purchase", pid, {"po_no": po.po_no}, user)
    db.commit()
    return Resp.ok({"id": pid, "status": po.status})


@router.get("")
def list_(status: Optional[str] = None, user: User = Depends(require_role("OPERATION", "FINANCE", "ADMIN", "DEPARTMENT_HEAD")),
          db: Session = Depends(get_db)):
    q = db.query(Purchase)
    if status:
        q = q.filter(Purchase.status == status)
    rows = q.order_by(Purchase.id.desc()).all()
    return {"code": 0, "data": [{
        "id": p.id, "po_no": p.po_no, "supplier_id": p.supplier_id,
        "supplier_name": p.supplier.name if p.supplier else "",
        "status": p.status, "total_amount": float(p.total_amount or 0),
        "finance_doc_id": p.finance_doc_id,
    } for p in rows]}
