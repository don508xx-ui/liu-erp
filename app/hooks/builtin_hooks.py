"""
业财联动核心钩子 - 5大事件钩子,配置驱动
所有财务单据/库存流水由钩子自动生成,禁止人工直接创建
"""
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from app.core.event_bus import register, emit
from app.core.notify import send as notify_send
from app.core.audit import log_audit
from app.api.approvals import bjt_now
from app.models.system import User, Role
from app.models.customer import Customer
from app.models.order import Order, OrderItem
from app.models.workshop import WorkOrder, Completion, CompletionItem
from app.models.inventory import InventoryItem, InventoryTxn, MaterialRequisition, CustomerConsignLog
from app.models.finance import FinanceDoc, WorkOrderCost, Account
from sqlalchemy import func
from app.models.purchase import Purchase, PurchaseItem
import logging

logger = logging.getLogger(__name__)


# ---------- 工具 ----------

def _gen_no(prefix: str, seq: int, date_str: str = None, suffix: str = "") -> str:
    ds = date_str or bjt_now().strftime("%Y%m%d")
    return f"{prefix}-{ds}-{seq:04d}{suffix}"


def _users_by_role(db: Session, role_code: str):
    return db.query(User).join(Role, User.role_id == Role.id).filter(Role.code == role_code, User.status == "ACTIVE").all()


def _notify_role(db: Session, role_code: str, template_code: str, channel: str, variables: dict):
    for u in _users_by_role(db, role_code):
        recipient = u.email if channel == "EMAIL" else str(u.id)
        notify_send(db, template_code, channel, recipient, u.name, variables)


# ---------- 1. 订单提交: 通知运营+总经理 ----------

@register("order.submitted")
def _order_submitted(db: Session, et, eid, payload, user):
    order = db.query(Order).get(eid)
    cust = db.query(Customer).get(order.customer_id) if order else None
    vars = {
        "order_no": order.order_no,
        "customer": cust.name if cust else "",
        "amount": str(order.total_amount),
        "sales": user.name if user else "",
    }
    _notify_role(db, "OPERATION", "order.submitted.notice", "INAPP", vars)
    _notify_role(db, "GM", "order.submitted.notice", "INAPP", vars)


# ---------- 2. 订单生效: 建应收草稿 + 抄送 + 处理预收款 ----------

@register("order.effective")
def _order_effective_finance(db: Session, et, eid, payload, user):
    order = db.query(Order).get(eid)
    if not order:
        return
    cust = db.query(Customer).get(order.customer_id)

    # 建应收DRAFT - 带双公司主体分流
    ar = FinanceDoc(
        doc_no=_gen_no("AR", order.id),
        doc_type="RECEIVABLE",
        status="DRAFT",  # 完工确认后转OPEN
        related_type="ORDER",
        related_id=order.id,
        counterparty_type="CUSTOMER",
        counterparty_id=order.customer_id,
        counterparty_name=cust.name if cust else "",
        amount=order.total_amount,
        company_id=order.company_id,  # 双公司分流
        billing_type=order.billing_type,  # SPECIAL_VAT/NORMAL/CASH
        account_date=bjt_now(),
        due_date=bjt_now() + timedelta(days=30),
        source_event="order.effective",
    )
    db.add(ar)
    db.flush()
    log_audit(db, user, "auto_create", "finance_doc", ar.id,
              after={"doc_no": ar.doc_no, "type": "RECEIVABLE", "amount": float(ar.amount)})

    # 预收款自动部分核销 - 同公司主体
    if order.prepayment_amount and order.prepayment_amount > 0:
        rc = FinanceDoc(
            doc_no=_gen_no("RC", order.id),
            doc_type="RECEIPT",
            status="SETTLED",
            related_type="ORDER",
            related_id=order.id,
            counterparty_type="CUSTOMER",
            counterparty_id=order.customer_id,
            counterparty_name=cust.name if cust else "",
            amount=order.prepayment_amount,
            settled_amount=order.prepayment_amount,
            company_id=order.company_id,
            billing_type=order.billing_type,
            account_date=bjt_now(),
            source_event="order.effective",
        )
        db.add(rc)
        db.flush()
        ar.settled_amount = (ar.settled_amount or 0) + order.prepayment_amount
        log_audit(db, user, "auto_create", "finance_doc", rc.id,
                  after={"doc_no": rc.doc_no, "type": "RECEIPT", "amount": float(rc.amount)})


@register("order.effective")
def _order_effective_notify(db: Session, et, eid, payload, user):
    order = db.query(Order).get(eid)
    cust = db.query(Customer).get(order.customer_id) if order else None
    vars = {
        "order_no": order.order_no,
        "customer": cust.name if cust else "",
        "amount": str(order.total_amount),
        "prepayment": str(order.prepayment_amount),
    }
    _notify_role(db, "OPERATION", "order.effective.notice", "INAPP", vars)
    _notify_role(db, "GM", "order.effective.notice", "INAPP", vars)


# ---------- 3. 加工单下达: 自动生成领料单 + 通知厂长 ----------

@register("work_order.released")
def _wo_released_requisition(db: Session, et, eid, payload, user):
    wo = db.query(WorkOrder).get(eid)
    if not wo:
        return
    order = db.query(Order).get(wo.order_id)
    items_data = []
    # 客供料订单:不生成领料单(料是客户的)
    is_customer_material = False
    if wo.order_item_id:
        oi = db.query(OrderItem).get(wo.order_item_id)
        if oi and oi.material_mode == "CUSTOMER":
            is_customer_material = True
    if not is_customer_material:
        # BOM: 优先用订单明细中精确关联的paint_item_id,再按paint_spec模糊匹配
        paints = db.query(InventoryItem).filter(InventoryItem.category == "PAINT_POWDER",
                                                InventoryItem.status == "ACTIVE").all()
        target_paint = None
        if wo.order_item_id:
            oi = db.query(OrderItem).get(wo.order_item_id)
            if oi:
                if oi.paint_item_id:
                    target_paint = db.query(InventoryItem).filter(
                        InventoryItem.id == oi.paint_item_id,
                        InventoryItem.status == "ACTIVE"
                    ).first()
                if not target_paint and oi.paint_spec:
                    # 模糊匹配: 先按名称精确匹配,再按包含匹配,再按规格匹配
                    target_paint = next((p for p in paints if oi.paint_spec == (p.name or "")), None)
                    if not target_paint:
                        target_paint = next((p for p in paints if oi.paint_spec.lower() in (p.name or "").lower()), None)
                    if not target_paint:
                        target_paint = next((p for p in paints if oi.paint_spec.lower() in (p.spec or "").lower()), None)
        # 最后才fallback到第一个涂料(仅当无任何匹配且无paint_spec时)
        if not target_paint and not (wo.order_item_id and oi and oi.paint_spec):
            target_paint = paints[0] if paints else None
        if target_paint:
            # 理论用量: 简化按面积*0.15kg/m² 或按件*0.05kg/件
            theoretical = float(wo.plan_qty or 0) * 0.15
            items_data.append({
                "item_id": target_paint.id,
                "item_name": target_paint.name,
                "spec": target_paint.spec,
                "qty": round(theoretical, 3),
                "unit": target_paint.unit,
                "theoretical_qty": round(theoretical, 3),
            })

        req = MaterialRequisition(
            req_no=_gen_no("REQ", wo.id),
            work_order_id=wo.id,
            status="PENDING",
            items=items_data,
        )
        db.add(req)
        db.flush()
        log_audit(db, user, "auto_create", "material_requisition", req.id,
                  after={"req_no": req.req_no, "items": items_data})

    # 客供料: 记台账
    if is_customer_material and wo.order_item_id:
        oi = db.query(OrderItem).get(wo.order_item_id)
        cl = CustomerConsignLog(
            order_id=wo.order_id,
            work_order_id=wo.id,
            customer_id=order.customer_id if order else None,
            part_name=oi.part_name if oi else "",
            part_spec=oi.part_spec if oi else "",
            received_qty=oi.quantity if oi else 0,
            received_at=bjt_now(),
            status="RECEIVED",
        )
        db.add(cl)
        db.flush()


@register("work_order.released")
def _wo_released_notify(db: Session, et, eid, payload, user):
    wo = db.query(WorkOrder).get(eid)
    vars = {"work_order_no": wo.work_order_no, "workshop": wo.workshop or ""}
    _notify_role(db, "MANAGER", "work_order.released.notice", "INAPP", vars)


# ---------- 4. 领料确认: 写材料成本 + 扣库存 ----------

@register("material.confirmed")
def _material_confirmed(db: Session, et, eid, payload, user):
    req = db.query(MaterialRequisition).get(eid)
    if not req:
        return
    # 状态已在API层设置,钩子只做:扣库存+材料成本+流水

    for idx, it in enumerate(req.items or []):
        item = db.query(InventoryItem).get(it["item_id"])
        if not item:
            continue
        qty = float(it["qty"])
        unit_cost = float(item.unit_cost or 0)
        amount = round(qty * unit_cost, 2)
        # 扣库存
        item.stock_qty = float(item.stock_qty or 0) - qty
        # 出库流水(每行独立txn_no)
        txn = InventoryTxn(
            txn_no=_gen_no("TXN-OUT", req.id, suffix=f"-{idx+1}"),
            txn_type="OUT",
            item_id=item.id,
            quantity=qty,
            unit_cost=unit_cost,
            amount=amount,
            work_order_id=req.work_order_id,
            ref_doc_type="REQUISITION",
            ref_doc_id=req.id,
            operator_user_id=user.id if user else None,
        )
        db.add(txn)
        # 写工单成本
        cost = WorkOrderCost(
            work_order_id=req.work_order_id,
            cost_type="MATERIAL",
            amount=amount,
            source_doc_type="REQUISITION",
            source_doc_id=req.id,
        )
        db.add(cost)
        db.flush()
        log_audit(db, user, "auto_create", "inventory_txn", txn.id,
                  after={"txn_no": txn.txn_no, "type": "OUT", "qty": qty})


# ---------- 5. 完工确认: 应收转OPEN + 成本结转 + 成品入库 + 退料 + 通知销售 ----------

@register("completion.confirmed")
def _completion_finance(db: Session, et, eid, payload, user):
    cp = db.query(Completion).get(eid)
    if not cp:
        return
    wo = db.query(WorkOrder).get(cp.work_order_id)
    order = db.query(Order).get(wo.order_id) if wo else None

    # 1. 人工成本+制造费用写入work_order_costs
    if cp.labor_cost and cp.labor_cost > 0:
        db.add(WorkOrderCost(
            work_order_id=wo.id, cost_type="LABOR", amount=cp.labor_cost,
            source_doc_type="COMPLETION", source_doc_id=cp.id,
        ))
    if cp.overhead_cost and cp.overhead_cost > 0:
        db.add(WorkOrderCost(
            work_order_id=wo.id, cost_type="OVERHEAD", amount=cp.overhead_cost,
            source_doc_type="COMPLETION", source_doc_id=cp.id,
        ))
    db.flush()

    # 2. 汇总总成本(SQL聚合)
    total = db.query(func.sum(WorkOrderCost.amount)).filter(WorkOrderCost.work_order_id == wo.id).scalar()
    total_cost = float(total or 0)
    cp.total_cost = total_cost
    db.flush()

    # 3. 应收转OPEN(找到这笔订单的DRAFT应收)
    if order:
        ar = db.query(FinanceDoc).filter(
            FinanceDoc.related_type == "ORDER",
            FinanceDoc.related_id == order.id,
            FinanceDoc.doc_type == "RECEIVABLE",
        ).first()
        if ar and ar.status == "DRAFT":
            ar.status = "OPEN"
            db.flush()
            log_audit(db, user, "state_change", "finance_doc", ar.id,
                      after={"status": "OPEN"})

    # 4. 成品入库 + 退料入仓
    finished_item = db.query(InventoryItem).filter(
        InventoryItem.category == "FINISHED_GOOD"
    ).first()
    if finished_item:
        finished_item.stock_qty = float(finished_item.stock_qty or 0) + float(cp.qualified_qty or 0)
        db.add(InventoryTxn(
            txn_no=_gen_no("TXN-IN", cp.id),
            txn_type="IN",
            item_id=finished_item.id,
            quantity=float(cp.qualified_qty or 0),
            unit_cost=total_cost / float(cp.qualified_qty) if cp.qualified_qty else 0,
            amount=total_cost,
            work_order_id=wo.id,
            order_id=order.id if order else None,
            ref_doc_type="COMPLETION",
            ref_doc_id=cp.id,
            operator_user_id=user.id if user else None,
        ))

    # 5. 退料入仓(完工单明细的return_qty)
    for ci in cp.items:
        if ci.return_qty and ci.return_qty > 0:
            item = db.query(InventoryItem).get(ci.item_id) if ci.item_id else None
            if item:
                item.stock_qty = float(item.stock_qty or 0) + float(ci.return_qty)
                db.add(InventoryTxn(
                    txn_no=_gen_no("TXN-RET", ci.id),
                    txn_type="RETURN",
                    item_id=item.id,
                    quantity=float(ci.return_qty),
                    unit_cost=float(ci.unit_cost or 0),
                    amount=float(ci.unit_cost or 0) * float(ci.return_qty),
                    work_order_id=wo.id,
                    ref_doc_type="COMPLETION",
                    ref_doc_id=cp.id,
                ))
        # 利用率计算
        if ci.actual_qty and ci.actual_qty > 0 and ci.theoretical_qty:
            ci.utilization_rate = round(float(ci.theoretical_qty) / float(ci.actual_qty) * 100, 2)
        if ci.actual_qty and ci.unit_cost:
            ci.cost_amount = round(float(ci.actual_qty) * float(ci.unit_cost), 2)
    db.flush()

    # 6. 工单状态 → COMPLETED
    wo.status = "COMPLETED"
    wo.completed_at = bjt_now()
    db.flush()


@register("completion.confirmed")
def _completion_notify(db: Session, et, eid, payload, user):
    cp = db.query(Completion).get(eid)
    wo = db.query(WorkOrder).get(cp.work_order_id) if cp else None
    order = db.query(Order).get(wo.order_id) if wo else None
    cust = db.query(Customer).get(order.customer_id) if order else None
    vars = {
        "completion_no": cp.completion_no,
        "work_order_no": wo.work_order_no if wo else "",
        "order_no": order.order_no if order else "",
        "customer": cust.name if cust else "",
        "amount": str(order.total_amount) if order else "",
    }
    _notify_role(db, "OPERATION", "completion.confirmed.notice", "INAPP", vars)
    _notify_role(db, "GM", "completion.confirmed.notice", "INAPP", vars)


# ---------- 6. 通知销售催款(运营确认完工后) ----------

@register("completion.confirmed")
def _completion_notify_sales(db: Session, et, eid, payload, user):
    cp = db.query(Completion).get(eid)
    wo = db.query(WorkOrder).get(cp.work_order_id) if cp else None
    order = db.query(Order).get(wo.order_id) if wo else None
    cust = db.query(Customer).get(order.customer_id) if order else None
    vars = {
        "order_no": order.order_no if order else "",
        "customer": cust.name if cust else "",
        "amount": str(order.total_amount - (order.prepayment_amount or 0)) if order else "0",
    }
    if order and order.sales_user_id:
        sales = db.query(User).get(order.sales_user_id)
        if sales:
            notify_send(db, "payment.remind", "INAPP", str(sales.id), sales.name, vars)


# ---------- 7. 采购入库: 建应付 + 入库 ----------

@register("purchase.received")
def _purchase_received(db: Session, et, eid, payload, user):
    po = db.query(Purchase).get(eid)
    if not po:
        return
    po.status = "RECEIVED"
    po.received_at = bjt_now()
    db.flush()
    # 入库
    for idx, it in enumerate(po.items):
        item = db.query(InventoryItem).get(it.item_id) if it.item_id else None
        if item:
            item.stock_qty = float(item.stock_qty or 0) + float(it.qty)
            db.add(InventoryTxn(
                txn_no=_gen_no("TXN-PUR", po.id, suffix=f"-{idx+1}"),
                txn_type="IN",
                item_id=item.id,
                quantity=float(it.qty),
                unit_cost=float(it.unit_price or 0),
                amount=float(it.amount or 0),
                ref_doc_type="PURCHASE",
                ref_doc_id=po.id,
                operator_user_id=user.id if user else None,
            ))
    # 建应付
    sup_name = po.supplier.name if po.supplier else ""
    ap = FinanceDoc(
        doc_no=_gen_no("AP", po.id),
        doc_type="PAYABLE",
        status="OPEN",
        related_type="PURCHASE",
        related_id=po.id,
        counterparty_type="SUPPLIER",
        counterparty_id=po.supplier_id,
        counterparty_name=sup_name,
        amount=po.total_amount,
        account_date=bjt_now(),
        due_date=bjt_now() + timedelta(days=30),
        source_event="purchase.received",
    )
    db.add(ap)
    db.flush()
    po.finance_doc_id = ap.id
    db.flush()


# ---------- 8. 工资确认: 建付款(期间费用) ----------

@register("payroll.confirmed")
def _payroll_confirmed(db: Session, et, eid, payload, user):
    from app.models.finance import PayrollRun
    pr = db.query(PayrollRun).get(eid)
    if not pr:
        return
    pr.status = "CONFIRMED"
    pr.confirmed_at = bjt_now()
    db.flush()
    pay = FinanceDoc(
        doc_no=_gen_no("PY", pr.id),
        doc_type="PAYMENT",
        status="OPEN",
        related_type="PAYROLL",
        related_id=pr.id,
        counterparty_type="EMPLOYEE",
        counterparty_name=f"工资-{pr.period}",
        amount=pr.total_amount,
        account_date=bjt_now(),
        source_event="payroll.confirmed",
    )
    db.add(pay)
    db.flush()
    pr.finance_doc_id = pay.id
    db.flush()


# ---------- 9. 收款核销应收 ----------

@register("receipt.created")
def _receipt_settle(db: Session, et, eid, payload, user):
    rc = db.query(FinanceDoc).get(eid)
    if not rc or rc.doc_type != "RECEIPT":
        return
    # 找对应应收(同公司主体优先)
    if rc.related_type == "ORDER" and rc.related_id:
        ar = db.query(FinanceDoc).filter(
            FinanceDoc.related_type == "ORDER",
            FinanceDoc.related_id == rc.related_id,
            FinanceDoc.doc_type == "RECEIVABLE",
        ).first()
        if ar:
            ar.settled_amount = float(ar.settled_amount or 0) + float(rc.amount or 0)
            if ar.settled_amount >= float(ar.amount or 0):
                ar.status = "SETTLED"
                ar.settled_at = bjt_now()
            db.flush()


# ---------- 10. 发货确认: 应收到期 + 通知销售催款 ----------

@register("order.delivered")
def _order_delivered_finance(db: Session, et, eid, payload, user):
    """发货确认后:订单应收保持OPEN(已完工时),记录发货事实"""
    delivery_no = payload.get("delivery_no") if payload else ""
    order_id = payload.get("order_id") if payload else None
    if not order_id:
        return
    order = db.query(Order).get(order_id)
    if not order:
        return
    # 应收due_date调整为发货后30天(发货即确认债权)
    ar = db.query(FinanceDoc).filter(
        FinanceDoc.related_type == "ORDER",
        FinanceDoc.related_id == order.id,
        FinanceDoc.doc_type == "RECEIVABLE",
    ).first()
    if ar and ar.status in ("OPEN", "DRAFT"):
        ar.due_date = bjt_now() + timedelta(days=30)
        if ar.status == "DRAFT":
            ar.status = "OPEN"
        db.flush()
        log_audit(db, user, "auto_update", "finance_doc", ar.id,
                  after={"due_date": ar.due_date.isoformat(), "trigger": "delivered"})


@register("order.delivered")
def _order_delivered_notify(db: Session, et, eid, payload, user):
    order_id = payload.get("order_id") if payload else None
    order = db.query(Order).get(order_id) if order_id else None
    cust = db.query(Customer).get(order.customer_id) if order else None
    vars = {
        "order_no": order.order_no if order else "",
        "customer": cust.name if cust else "",
        "amount": str(order.total_amount - (order.prepayment_amount or 0)) if order else "0",
        "delivery_no": payload.get("delivery_no", "") if payload else "",
    }
    # 通知销售催尾款
    if order and order.sales_user_id:
        sales = db.query(User).get(order.sales_user_id)
        if sales:
            notify_send(db, "payment.remind", "INAPP", str(sales.id), sales.name, vars)
    _notify_role(db, "FINANCE", "order.delivered.notice", "INAPP", vars)


# ---------- 11. 调价审批通过: 调整应收金额 ----------

@register("adjustment.approved")
def _adjustment_approved(db: Session, et, eid, payload, user):
    """GM审批通过后:追溯调整对应订单应收的adjusted_amount"""
    from app.models.sales import SalesAdjustment
    adj = db.query(SalesAdjustment).get(eid)
    if not adj:
        return
    ar = db.query(FinanceDoc).filter(
        FinanceDoc.related_type == "ORDER",
        FinanceDoc.related_id == adj.order_id,
        FinanceDoc.doc_type == "RECEIVABLE",
    ).first()
    if ar:
        ar.adjusted_amount = adj.adjusted_amount
        db.flush()
        log_audit(db, user, "auto_update", "finance_doc", ar.id,
                  after={"adjusted_amount": float(adj.adjusted_amount),
                         "original": float(adj.original_amount),
                         "diff": float(adj.diff_amount or 0)})
    # 通知发起销售
    sales = db.query(User).get(adj.initiator_user_id) if adj.initiator_user_id else None
    if sales:
        order = db.query(Order).get(adj.order_id)
        notify_send(db, "adjustment.approved", "INAPP", str(sales.id), sales.name, {
            "adj_no": adj.adj_no,
            "order_no": order.order_no if order else "",
            "adjusted_amount": str(adj.adjusted_amount),
        })
