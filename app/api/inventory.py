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
def create_item(body: ItemIn, user: User = Depends(require_role("OPERATION", "ADMIN", "FINANCE")),
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
               user: User = Depends(require_role("OPERATION", "MANAGER", "FINANCE", "ADMIN")), db: Session = Depends(get_db)):
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


TXN_TYPE_LABEL = {"IN":"入库","OUT":"出库","RETURN":"退库","ADJUST":"调整"}
REF_DOC_LABEL = {
    "REQUISITION":"领料单","COMPLETION":"完工入库","PURCHASE":"采购收货",
    "SHIPMENT":"销售出货","MANUAL":"手工登记","STOCK_CHECK":"盘点调账",
    "RETURN_MAT":"退料入库","RETURN_GOODS":"销售退货入库","SAMPLE":"打样出库",
    "OUTSOURCE":"外协出库",
}

@router.get("/txns")
def list_txns(item_id: Optional[int] = None, txn_type: Optional[str] = None,
              ref_doc_type: Optional[str] = None, keyword: Optional[str] = None,
              date_from: Optional[str] = None, date_to: Optional[str] = None,
              page: int = 1, size: int = 50,
              user: User = Depends(require_role("OPERATION", "FINANCE", "ADMIN")), db: Session = Depends(get_db)):
    from sqlalchemy import and_
    q = db.query(InventoryTxn).join(InventoryItem, InventoryTxn.item_id == InventoryItem.id)
    if item_id:
        q = q.filter(InventoryTxn.item_id == item_id)
    if txn_type:
        q = q.filter(InventoryTxn.txn_type == txn_type)
    if ref_doc_type:
        q = q.filter(InventoryTxn.ref_doc_type == ref_doc_type)
    if keyword:
        q = q.filter(InventoryItem.name.like(f"%{keyword}%"))
    if date_from:
        q = q.filter(InventoryTxn.occurred_at >= date_from + "T00:00:00")
    if date_to:
        q = q.filter(InventoryTxn.occurred_at <= date_to + "T23:59:59")
    total = q.count()
    # 汇总KPI
    in_qty = db.query(InventoryTxn).filter(InventoryTxn.txn_type == "IN")
    out_qty = db.query(InventoryTxn).filter(InventoryTxn.txn_type == "OUT")
    if date_from: in_qty = in_qty.filter(InventoryTxn.occurred_at >= date_from+"T00:00:00"); out_qty = out_qty.filter(InventoryTxn.occurred_at >= date_from+"T00:00:00")
    if date_to: in_qty = in_qty.filter(InventoryTxn.occurred_at <= date_to+"T23:59:59"); out_qty = out_qty.filter(InventoryTxn.occurred_at <= date_to+"T23:59:59")
    from sqlalchemy import func as _f
    in_sum = in_qty.with_entities(_f.coalesce(_f.sum(InventoryTxn.amount), 0)).scalar() or 0
    out_sum = out_qty.with_entities(_f.coalesce(_f.sum(InventoryTxn.amount), 0)).scalar() or 0

    rows = q.order_by(InventoryTxn.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total,
            "summary": {"in_amount": float(in_sum or 0), "out_amount": float(out_sum or 0), "net_amount": float(in_sum or 0) - float(out_sum or 0), "txn_count": total},
            "data": [{
        "id": t.id, "txn_no": t.txn_no,
        "txn_type": t.txn_type, "txn_type_label": TXN_TYPE_LABEL.get(t.txn_type, t.txn_type),
        "item_id": t.item_id, "item_name": t.item.name if t.item else ("物料#"+str(t.item_id)),
        "item_code": t.item.code if t.item else "", "unit": t.item.unit if t.item else "",
        "qty": float(t.quantity or 0),
        "unit_cost": float(t.unit_cost or 0), "amount": float(t.amount or 0),
        "work_order_id": t.work_order_id, "order_id": t.order_id,
        "ref_doc_type": t.ref_doc_type, "ref_doc_type_label": REF_DOC_LABEL.get(t.ref_doc_type, t.ref_doc_type or "-"),
        "ref_doc_id": t.ref_doc_id,
        "warehouse": t.warehouse or "-",
        "occurred_at": t.occurred_at.isoformat() if t.occurred_at else None,
        "remark": t.remark or "",
    } for t in rows]}


@router.get("/consign-log")
def consign_log(user: User = Depends(require_role("OPERATION", "ADMIN")),
                customer_id: Optional[int] = None, order_id: Optional[int] = None,
                status: Optional[str] = None, keyword: Optional[str] = None,
                db: Session = Depends(get_db)):
    from app.models.order import Order
    from app.models.customer import Customer
    q = db.query(CustomerConsignLog)
    if customer_id:
        q = q.filter(CustomerConsignLog.customer_id == customer_id)
    if order_id:
        q = q.filter(CustomerConsignLog.order_id == order_id)
    if status:
        q = q.filter(CustomerConsignLog.status == status)
    if keyword:
        q = q.filter(CustomerConsignLog.part_name.like(f"%{keyword}%"))
    total = q.count()
    rows = q.order_by(CustomerConsignLog.id.desc()).limit(200).all()
    cust_map = {c.id: c.name for c in db.query(Customer).filter(Customer.id.in_([r.customer_id for r in rows if r.customer_id])).all()} if rows else {}
    order_map = {o.id: o.order_no for o in db.query(Order).filter(Order.id.in_([r.order_id for r in rows if r.order_id])).all()} if rows else {}
    return {"code": 0, "total": total, "data": [{
        "id": r.id, "order_id": r.order_id, "order_no": order_map.get(r.order_id, ""),
        "work_order_id": r.work_order_id,
        "customer_id": r.customer_id, "customer_name": cust_map.get(r.customer_id, ""),
        "part_name": r.part_name, "part_spec": r.part_spec,
        "received_qty": float(r.received_qty or 0), "consumed_qty": float(r.consumed_qty or 0),
        "returned_qty": float(r.returned_qty or 0),
        "stock_qty": float(r.received_qty or 0) - float(r.consumed_qty or 0) - float(r.returned_qty or 0),
        "status": r.status,
        "received_at": r.received_at.isoformat() if r.received_at else None,
        "returned_at": r.returned_at.isoformat() if r.returned_at else None,
        "remark": r.remark or "",
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]}


class ConsignIn(BaseModel):
    order_id: Optional[int] = None
    work_order_id: Optional[int] = None
    customer_id: Optional[int] = None
    part_name: str
    part_spec: Optional[str] = None
    received_qty: float
    remark: Optional[str] = None


@router.post("/consign-log")
def create_consign(body: ConsignIn, user: User = Depends(require_role("OPERATION", "ADMIN")),
                   db: Session = Depends(get_db)):
    from app.api.approvals import bjt_now
    from app.models.order import Order
    from app.models.workshop import WorkOrder
    cust_id = body.customer_id
    if not cust_id and body.order_id:
        o = db.query(Order).get(body.order_id)
        if o: cust_id = o.customer_id
    if not cust_id and body.work_order_id:
        wo = db.query(WorkOrder).get(body.work_order_id)
        if wo: cust_id = wo.customer_id
    seq = db.query(CustomerConsignLog).count() + 1
    r = CustomerConsignLog(
        order_id=body.order_id, work_order_id=body.work_order_id, customer_id=cust_id,
        part_name=body.part_name, part_spec=body.part_spec,
        received_qty=body.received_qty, consumed_qty=0, returned_qty=0,
        received_at=bjt_now(), status="RECEIVED", remark=body.remark,
    )
    db.add(r)
    db.commit()
    return Resp.ok({"id": r.id, "status": r.status})


class ConsignMoveIn(BaseModel):
    qty: float
    remark: Optional[str] = None


@router.post("/consign-log/{rid}/consume")
def consign_consume(rid: int, body: ConsignMoveIn,
                    user: User = Depends(require_role("OPERATION", "ADMIN")),
                    db: Session = Depends(get_db)):
    from app.api.approvals import bjt_now
    r = db.query(CustomerConsignLog).get(rid)
    if not r:
        raise HTTPException(404, "台账记录不存在")
    avail = float(r.received_qty or 0) - float(r.consumed_qty or 0) - float(r.returned_qty or 0)
    if body.qty > avail + 0.0005:
        raise HTTPException(400, f"可消耗数量不足: 剩余{round(avail,3)}, 欲消耗{body.qty}")
    r.consumed_qty = float(r.consumed_qty or 0) + body.qty
    if float(r.consumed_qty) + float(r.returned_qty or 0) + 0.0005 >= float(r.received_qty or 0):
        r.status = "CONSUMED"
    if body.remark:
        r.remark = (r.remark or "") + f"\n[消耗 {body.qty}] {body.remark}"
    db.commit()
    return Resp.ok({"id": rid, "consumed_qty": float(r.consumed_qty), "status": r.status})


@router.post("/consign-log/{rid}/return")
def consign_return(rid: int, body: ConsignMoveIn,
                   user: User = Depends(require_role("OPERATION", "ADMIN")),
                   db: Session = Depends(get_db)):
    from app.api.approvals import bjt_now
    r = db.query(CustomerConsignLog).get(rid)
    if not r:
        raise HTTPException(404, "台账记录不存在")
    avail = float(r.received_qty or 0) - float(r.consumed_qty or 0) - float(r.returned_qty or 0)
    if body.qty > avail + 0.0005:
        raise HTTPException(400, f"可退回数量不足: 剩余{round(avail,3)}, 欲退回{body.qty}")
    r.returned_qty = float(r.returned_qty or 0) + body.qty
    r.returned_at = bjt_now()
    if float(r.consumed_qty) + float(r.returned_qty or 0) + 0.0005 >= float(r.received_qty or 0):
        r.status = "RETURNED"
    if body.remark:
        r.remark = (r.remark or "") + f"\n[退回 {body.qty}] {body.remark}"
    db.commit()
    return Resp.ok({"id": rid, "returned_qty": float(r.returned_qty), "status": r.status})
