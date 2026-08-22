from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role
from app.core.audit import log_audit
from app.core.event_bus import emit
from app.api.approvals import bjt_now
from app.models.system import User
from app.models.purchase import Supplier, PurchaseRequest, Purchase, PurchaseItem
from app.models.inventory import InventoryItem, InventoryTxn
from app.models.finance import FinanceDoc
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
    supplier_id: Optional[int] = None
    items: List[PItemIn] = []
    request_id: Optional[int] = None
    remark: Optional[str] = None
    form_data: Optional[dict] = None  # 画布动态表单全字段(以画布设计为准,零硬编码)


@router.post("")
def create_po(body: POIn, user: User = Depends(require_role("FINANCE", "OPERATION", "ADMIN")),
              db: Session = Depends(get_db)):
    fd = body.form_data or {}
    # 供应商: 兼容画布ref_picker(key=supplier_ref)与旧字段(supplier_id)
    supplier_id = fd.get("supplier_id") or fd.get("supplier_ref") or body.supplier_id
    if not supplier_id:
        raise HTTPException(400, "请选择供应商")
    sup = db.query(Supplier).get(supplier_id)
    if not sup:
        raise HTTPException(400, "供应商不存在")
    items_src = fd.get("items")
    if items_src:
        p_items = [{
            "item_name": it.get("item_name") or it.get("name") or "",
            "spec": it.get("spec"),
            "qty": it.get("qty", 0),
            "unit": it.get("unit") or "pcs",
            "unit_price": it.get("est_price", it.get("unit_price", 0)),
        } for it in items_src if isinstance(it, dict)]
    else:
        p_items = [{"item_name": it.item_name, "spec": it.spec, "qty": it.qty,
                    "unit": it.unit, "unit_price": it.unit_price} for it in body.items]
    total = fd.get("total_amount")
    if total is None:
        total = sum((it["qty"] or 0) * (it["unit_price"] or 0) for it in p_items)
    seq = db.query(Purchase).count() + 1
    po = Purchase(
        po_no=f"PO-{bjt_now().strftime('%Y%m%d')}-{seq:04d}",
        supplier_id=supplier_id, request_id=body.request_id,
        status="DRAFT", total_amount=total, remark=fd.get("remark") or body.remark,
        extra=fd or None,
    )
    db.add(po)
    db.flush()
    for it in p_items:
        db.add(PurchaseItem(
            purchase_id=po.id, item_id=None, item_name=it["item_name"],
            spec=it["spec"], qty=it["qty"], unit=it["unit"], unit_price=it["unit_price"],
            amount=(it["qty"] or 0) * (it["unit_price"] or 0),
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


class ReceiveItemIn(BaseModel):
    item_id: int
    received_qty: float
    unit_cost: Optional[float] = None


class ReceiveIn(BaseModel):
    items: List[ReceiveItemIn]
    warehouse: Optional[str] = None
    remark: Optional[str] = None


@router.post("/{pid}/receive")
def receive_po(pid: int, body: ReceiveIn,
               user: User = Depends(require_role("OPERATION", "FINANCE", "ADMIN")),
               db: Session = Depends(get_db)):
    """采购收货→自动入库+生成应付单: 不再走事件钩子, 直接生成 InventoryTxn/FinanceDoc 避免重复处理"""
    po = db.query(Purchase).get(pid)
    if not po:
        raise HTTPException(404, "采购单不存在")
    if po.status in ("CLOSED", "RECEIVED"):
        raise HTTPException(400, f"采购单状态 {po.status} 不可再收货")
    if not body.items:
        raise HTTPException(400, "收货明细不能为空")
    now = bjt_now()
    # 通过 item_id 反查 PurchaseItem (匹配 item_id 或 item_name)
    name_map = {}
    for it in db.query(InventoryItem).filter(
        InventoryItem.id.in_([i.item_id for i in body.items if i.item_id])
    ).all():
        name_map[it.id] = it.name
    po_items = db.query(PurchaseItem).filter(PurchaseItem.purchase_id == pid).all()
    # 建索引: item_id → PurchaseItem / item_name → PurchaseItem
    by_iid, by_name = {}, {}
    for pi in po_items:
        if pi.item_id:
            by_iid[pi.item_id] = pi
        if pi.item_name:
            by_name[pi.item_name] = pi
    total_recv_amount = 0.0
    seq = 0
    for ri in body.items:
        if ri.received_qty <= 0:
            continue
        inv = db.query(InventoryItem).get(ri.item_id)
        if not inv:
            raise HTTPException(400, f"物料 item_id={ri.item_id} 不存在")
        unit_cost = ri.unit_cost if ri.unit_cost is not None else float(inv.unit_cost or 0)
        line_amount = round(ri.received_qty * unit_cost, 2)
        # 更新库存
        inv.stock_qty = float(inv.stock_qty or 0) + ri.received_qty
        seq += 1
        db.add(InventoryTxn(
            txn_no=f"TXN-PUR-{now.strftime('%Y%m%d')}-{pid:04d}-{seq:02d}",
            txn_type="IN", item_id=inv.id,
            quantity=ri.received_qty, unit_cost=unit_cost, amount=line_amount,
            ref_doc_type="PURCHASE", ref_doc_id=pid,
            warehouse=body.warehouse, operator_user_id=user.id, occurred_at=now,
            remark=body.remark,
        ))
        # 同步 PurchaseItem.received_qty
        pi = by_iid.get(inv.id) or by_name.get(inv.name)
        if pi:
            pi.received_qty = float(pi.received_qty or 0) + ri.received_qty
            # 若未指定 unit_cost, 沿用 PO 行单价
            if ri.unit_cost is None and pi.unit_price:
                unit_cost = float(pi.unit_price)
                line_amount = round(ri.received_qty * unit_cost, 2)
        total_recv_amount += line_amount
    # 状态判定: 全部收齐 → RECEIVED, 否则 PARTIAL_RECEIVED
    all_recv = True
    for pi in po_items:
        if float(pi.qty or 0) - float(pi.received_qty or 0) > 0.005:
            all_recv = False
            break
    new_status = "RECEIVED" if (all_recv and po_items) else "PARTIAL_RECEIVED"
    old_status = po.status
    po.status = new_status
    po.received_at = now if new_status == "RECEIVED" else po.received_at
    # 生成应付单 (单次收货单独立建, 便于多次分批入库的核销)
    sup_name = po.supplier.name if po.supplier else ""
    seq_ap = (db.query(FinanceDoc).filter(FinanceDoc.doc_type == "PAYABLE").count() or 0) + 1
    ap = FinanceDoc(
        doc_no=f"AP-{now.strftime('%Y%m%d')}-{seq_ap:04d}",
        doc_type="PAYABLE", status="OPEN",
        related_type="PURCHASE", related_id=pid,
        counterparty_type="SUPPLIER", counterparty_id=po.supplier_id,
        counterparty_name=sup_name,
        amount=round(total_recv_amount, 2), settled_amount=0,
        account_date=now, due_date=now + timedelta(days=30),
        source_event="purchase.receive_manual", remark=body.remark,
    )
    db.add(ap)
    db.flush()
    if not po.finance_doc_id:
        po.finance_doc_id = ap.id
    log_audit(db, user, "state_change", "purchase", pid,
              before=old_status, after=new_status)
    db.flush()
    # 不再 emit purchase.received 事件: builtin_hooks._purchase_received 会全量加库存+建应付,
    # 与本接口的明细收货逻辑冲突会双计; 直接在此完成所有工作即可。
    db.commit()
    return Resp.ok({"id": pid, "status": po.status,
                    "finance_doc_id": ap.id, "finance_doc_no": ap.doc_no,
                    "received_amount": round(total_recv_amount, 2)})


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
