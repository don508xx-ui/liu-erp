from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role, apply_scope_filter, mask_customer, get_user_role_code
from app.core.audit import log_audit
from app.core.event_bus import emit
from app.models.system import User
from app.models.customer import Customer
from app.models.order import Order, OrderItem
from app.api.approvals import start_flow, bjt_now
from app.schemas import Resp

router = APIRouter(prefix="/api/orders", tags=["order"])


class ItemIn(BaseModel):
    seq: int = 1
    part_name: str
    part_spec: Optional[str] = None
    price_type: str  # BY_PIECE/BY_AREA/BY_WEIGHT
    quantity: float
    unit: str
    unit_price: float
    material_mode: str = "SELF"  # CUSTOMER/SELF
    paint_spec: Optional[str] = None
    paint_item_id: Optional[int] = None  # 精确关联物料ID
    process_requirement: Optional[str] = None


class OrderIn(BaseModel):
    customer_id: int
    company_id: Optional[int] = None  # 开票主体(双公司分流)
    billing_type: Optional[str] = None  # SPECIAL_VAT/NORMAL/CASH
    contract_id: Optional[int] = None  # 关联合同
    opportunity_id: Optional[int] = None  # 关联商机
    prepayment_amount: float = 0
    prepayment_ratio: float = 0
    remark: Optional[str] = None
    items: List[ItemIn]
    form_data: Optional[dict] = None  # 流程设计器动态表单数据(画布配置的扩展字段)


@router.post("")
def create(body: OrderIn, user: User = Depends(require_role("SALES", "ADMIN", "GM")),
           db: Session = Depends(get_db)):
    cust = db.query(Customer).get(body.customer_id)
    if not cust:
        raise HTTPException(400, "客户不存在")
    total = sum(it.quantity * it.unit_price for it in body.items)
    extra = body.form_data or None
    if body.remark and extra:
        extra = {"_remark": body.remark, **extra} if isinstance(extra, dict) else {"_remark": body.remark}
    elif body.remark:
        extra = {"_remark": body.remark}
    o = Order(
        order_no="TEMP",
        customer_id=body.customer_id,
        company_id=body.company_id, billing_type=body.billing_type,
        contract_id=body.contract_id, opportunity_id=body.opportunity_id,
        status="DRAFT",
        total_amount=total,
        prepayment_amount=body.prepayment_amount,
        prepayment_ratio=body.prepayment_ratio,
        sales_user_id=user.id,
        remark=body.remark,
        extra=extra,
    )
    db.add(o)
    db.flush()
    # 用自增ID生成唯一单号,避免并发冲突
    o.order_no = f"SO-{bjt_now().strftime('%Y%m%d')}-{o.id:04d}"
    for it in body.items:
        oi = OrderItem(
            order_id=o.id, seq=it.seq, part_name=it.part_name, part_spec=it.part_spec,
            price_type=it.price_type, quantity=it.quantity, unit=it.unit,
            unit_price=it.unit_price, amount=it.quantity * it.unit_price,
            # 保存 paint_item_id
            material_mode=it.material_mode, paint_spec=it.paint_spec,
            paint_item_id=it.paint_item_id,
            process_requirement=it.process_requirement,
        )
        db.add(oi)
    db.flush()
    log_audit(db, user, "create", "order", o.id, after={"order_no": o.order_no, "total": float(total)})
    db.commit()
    return Resp.ok({"id": o.id, "order_no": o.order_no, "total_amount": float(total)})


@router.post("/{oid}/submit")
def submit(oid: int, user: User = Depends(require_role("SALES", "ADMIN", "GM")),
           db: Session = Depends(get_db)):
    o = db.query(Order).get(oid)
    if not o:
        raise HTTPException(404, "订单不存在")
    if o.status not in ("DRAFT", "RETURNED", "REJECTED"):
        raise HTTPException(400, f"订单状态{o.status}不可提交")
    before = o.status
    o.status = "SUBMITTED"
    o.signed_at = bjt_now()
    log_audit(db, user, "state_change", "order", oid, before=before, after=o.status)
    db.flush()
    emit(db, "order.submitted", "order", oid, {"order_no": o.order_no}, user)
    
    # 启动核心生产流工作流
    inst = start_flow(db, "CORE_PRODUCTION", oid, user)
    if inst:
        o.approval_instance_id = inst.id
    else:
        o.status = "APPROVED"  # 无审批流定义,直接通过
    
    db.commit()
    return Resp.ok({"id": oid, "status": o.status, "approval_instance_id": o.approval_instance_id})


@router.post("/{oid}/effect")
def effect(oid: int, user: User = Depends(require_role("OPERATION", "ADMIN")),
           db: Session = Depends(get_db)):
    """手动生效订单 - 仅当关联的审批流已全部完成时允许"""
    from app.models.approval import FlowInstance
    o = db.query(Order).get(oid)
    if not o:
        raise HTTPException(404, "订单不存在")
    if o.status != "SUBMITTED":
        raise HTTPException(400, f"订单状态{o.status}不可生效")
    # 检查关联的CORE_PRODUCTION流程是否已完成
    if o.approval_instance_id:
        inst = db.query(FlowInstance).get(o.approval_instance_id)
        if inst and inst.status == "RUNNING":
            raise HTTPException(400, "订单审批流程仍在进行中，请等待审批完成")
    before = o.status
    o.status = "EFFECTIVE"
    o.effective_at = bjt_now()
    log_audit(db, user, "state_change", "order", oid, before=before, after=o.status)
    db.flush()
    emit(db, "order.effective", "order", oid, {"order_no": o.order_no}, user)
    db.commit()
    return Resp.ok({"id": oid, "status": o.status})


@router.post("/{oid}/return")
def return_order(oid: int, body: dict, user: User = Depends(require_role("OPERATION", "ADMIN")),
                 db: Session = Depends(get_db)):
    o = db.query(Order).get(oid)
    if not o:
        raise HTTPException(404, "订单不存在")
    if o.status != "SUBMITTED":
        raise HTTPException(400, f"订单状态{o.status}不可退单")
    before = o.status
    o.status = "RETURNED"
    o.return_count = (o.return_count or 0) + 1
    o.return_reason = body.get("reason", "")
    log_audit(db, user, "state_change", "order", oid, before=before, after=o.status)
    db.commit()
    return Resp.ok({"id": oid, "status": o.status})


@router.get("")
def list_(keyword: Optional[str] = None, status: Optional[str] = None,
          page: int = 1, size: int = 20,
          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Order)
    q = apply_scope_filter(user, db, q, "orders")
    if status:
        q = q.filter(Order.status == status)
    if keyword:
        q = q.filter(Order.order_no.contains(keyword))
    total = q.count()
    rows = q.order_by(Order.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [_to_dict(o, db, user) for o in rows]}


@router.get("/{oid}")
def get(oid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    o = db.query(Order).filter(Order.id == oid).first()
    if not o:
        raise HTTPException(404, "订单不存在")
    # 数据隔离: SALES只见自己的订单
    rc = get_user_role_code(user, db)
    if rc == "SALES" and o.sales_user_id and o.sales_user_id != user.id:
        raise HTTPException(403, "无权查看该订单")
    return Resp.ok(_to_dict(o, db, user, with_items=True))


def _to_dict(o: Order, db: Session, user=None, with_items=False) -> dict:
    cust = db.query(Customer).filter(Customer.id == o.customer_id).first() if o.customer_id else None
    # 客户名脱敏:有user则按角色,否则返回真名(内部调用)
    if cust and user is not None:
        show = mask_customer(user, db, cust)
        cust_name = show["name"]
    else:
        cust_name = cust.name if cust else ""
    d = {
        "id": o.id, "order_no": o.order_no, "customer_id": o.customer_id,
        "customer_name": cust_name,
        "customer_short_code": cust.short_code if cust else None,
        "status": o.status, "total_amount": float(o.total_amount or 0),
        "prepayment_amount": float(o.prepayment_amount or 0),
        "prepayment_ratio": float(o.prepayment_ratio or 0),
        "company_id": o.company_id, "billing_type": o.billing_type,
        "contract_id": o.contract_id, "opportunity_id": o.opportunity_id,
        "delivery_status": o.delivery_status,
        "delivered_at": o.delivered_at.isoformat() if o.delivered_at else None,
        "sales_user_id": o.sales_user_id, "return_count": o.return_count,
        "return_reason": o.return_reason, "remark": o.remark,
        "effective_at": o.effective_at.isoformat() if o.effective_at else None,
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }
    if with_items:
        d["items"] = [{
            "id": it.id, "seq": it.seq, "part_name": it.part_name, "part_spec": it.part_spec,
            "price_type": it.price_type, "quantity": float(it.quantity or 0), "unit": it.unit,
            "unit_price": float(it.unit_price or 0), "amount": float(it.amount or 0),
            "material_mode": it.material_mode, "paint_spec": it.paint_spec,
            "paint_item_id": it.paint_item_id,
            "process_requirement": it.process_requirement,
        } for it in o.items]
    return d
