"""出货单API - 完工确认后出货,4联单打印,生效后自动产生应收"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role
from app.core.audit import log_audit
from app.api.approvals import bjt_now
from app.models.system import User
from app.models.workshop import WorkOrder, Completion, ShipmentOrder
from app.models.order import Order, OrderItem
from app.models.finance import FinanceDoc
from app.models.fund import FundFlow
from app.schemas import Resp

router = APIRouter(prefix="/api/shipments", tags=["shipments"])


@router.post("")
def create(order_id: int, completion_id: int = None, user: User = Depends(require_role("OPERATION", "MANAGER", "ADMIN")),
           db: Session = Depends(get_db)):
    """从完工单创建出货单 - 自动产生应收"""
    order = db.query(Order).get(order_id)
    if not order:
        raise HTTPException(400, "订单不存在")
    if order.delivery_status not in ("PENDING", "PENDING_DELIVERY"):
        raise HTTPException(400, f"订单交付状态{order.delivery_status}不可出货")
    # 获取完工单
    cp = None
    if completion_id:
        cp = db.query(Completion).get(completion_id)
        if not cp or cp.status != "CONFIRMED":
            raise HTTPException(400, "完工单未确认,不可出货")
    # 生成单号
    seq = db.query(ShipmentOrder).count() + 1
    ship_no = f"SH-{bjt_now().strftime('%Y%m%d')}-{seq:04d}"
    # 从订单明细构建出货明细
    items = []
    total_qty = 0
    for it in order.items:
        items.append({
            "part_name": it.part_name, "part_spec": it.part_spec,
            "qty": float(it.quantity or 0), "unit": it.unit or "",
            "craft_type": it.craft_type, "material_thickness": it.material_thickness,
            "paint_spec": it.paint_spec,
        })
        total_qty += float(it.quantity or 0)
    ship = ShipmentOrder(
        ship_no=ship_no, order_id=order_id, completion_id=completion_id,
        work_order_id=cp.work_order_id if cp else None,
        customer_id=order.customer_id,
        customer_name=db.query(Order).get(order_id).customer and db.query(Order).get(order_id).customer.name or "",
        company_id=order.company_id, ship_date=bjt_now(),
        items=items, total_qty=total_qty, created_by=user.id,
    )
    # 获取客户名
    from app.models.customer import Customer
    cust = db.query(Customer).get(order.customer_id)
    if cust:
        ship.customer_name = cust.name
    db.add(ship)
    db.flush()
    # 自动产生应收单
    fd = FinanceDoc(
        doc_no=f"AR-{ship_no}",
        doc_type="RECEIVABLE",
        counterparty_id=order.customer_id,
        counterparty_name=ship.customer_name,
        account_date=bjt_now(),
        amount=float(order.total_amount or 0),
        settled_amount=0,
        due_date=_calc_due_date(bjt_now(), cust.settlement_cycle if cust else None),
        status="OPEN",
        order_id=order_id,
        source_event=f"出货单{ship_no}生效",
    )
    db.add(fd)
    db.flush()
    ship.finance_doc_id = fd.id
    # 更新订单交付状态
    order.delivery_status = "DELIVERED"
    order.delivered_at = bjt_now()
    log_audit(db, user, "create", "shipment", ship.id, after={"ship_no": ship_no, "finance_doc_id": fd.id})
    db.commit()
    return Resp.ok(_to_dict(ship, db))


def _calc_due_date(ship_date, settlement_cycle):
    """根据结算周期算应收到期日"""
    from datetime import timedelta
    if not settlement_cycle:
        return ship_date + timedelta(days=30)  # 默认30天
    if "现" in settlement_cycle or "款到" in settlement_cycle:
        return ship_date  # 款到发货=当天到期
    days = 0
    for w in settlement_cycle.replace("月结", "").replace("天", "").split("/"):
        try:
            days = max(days, int(w))
        except:
            pass
    if days == 0:
        days = 30
    return ship_date + timedelta(days=days)


@router.get("")
def list_(page: int = 1, size: int = 20,
          user: User = Depends(require_role("OPERATION", "MANAGER", "FINANCE", "GM", "ADMIN")),
          db: Session = Depends(get_db)):
    q = db.query(ShipmentOrder)
    total = q.count()
    rows = q.order_by(ShipmentOrder.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [_to_dict(s, db) for s in rows]}


def _to_dict(s, db):
    return {
        "id": s.id, "ship_no": s.ship_no, "order_id": s.order_id,
        "completion_id": s.completion_id, "work_order_id": s.work_order_id,
        "customer_id": s.customer_id, "customer_name": s.customer_name,
        "company_id": s.company_id, "ship_date": s.ship_date.isoformat() if s.ship_date else None,
        "status": s.status, "items": s.items, "total_qty": float(s.total_qty or 0),
        "finance_doc_id": s.finance_doc_id, "printed_at": s.printed_at.isoformat() if s.printed_at else None,
        "remark": s.remark, "created_at": s.created_at.isoformat() if s.created_at else None,
    }
