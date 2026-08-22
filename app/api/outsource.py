"""外协单 - 委托第三方加工,必须关联销售订单,总经理直审后自动生成应付单+凭证"""
from typing import Optional
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role
from app.core.audit import log_audit
from app.schemas import Resp
from app.models.system import User
from app.models.order import Order, OrderItem
from app.models.purchase import Supplier
from app.models.fund import FundAccount
from app.models.finance import FinanceDoc, FinanceItem, Account
from app.models.outsource import OutsourceOrder
from app.api.approvals import bjt_now

router = APIRouter(prefix="/api/outsource", tags=["outsource"])

STATUS_LABEL = {"SUBMITTED": "待审批", "APPROVED": "已通过", "REJECTED": "已驳回", "PAID": "已付款"}
PAY_METHOD_LABEL = {"CASH": "现金", "TELEGRAPHIC": "电汇", "ACCEPTANCE": "承兑"}


def _ledger(db: Session, code: str, name: str, type_: str, direction: str) -> Account:
    acc = db.query(Account).filter(Account.code == code).first()
    if not acc:
        acc = Account(code=code, name=name, type=type_, direction=direction,
                      is_required=1, level=1, status="ACTIVE")
        db.add(acc)
        db.flush()
    return acc


def _post_voucher(db: Session, odate, summary: str, entries: list, user_id: int):
    from app.core.voucher_service import create_voucher, post_voucher
    v = create_voucher(db, {
        "period": odate.strftime("%Y-%m"), "voucher_date": odate,
        "summary": summary, "entries": entries,
    }, creator_id=user_id)
    post_voucher(db, v.id)
    return v


def _to_dict(o: OutsourceOrder) -> dict:
    return {
        "id": o.id, "outsource_no": o.outsource_no,
        "order_id": o.order_id, "order_no": o.order_no,
        "customer_id": o.customer_id, "customer_name": o.customer_name,
        "work_order_id": o.work_order_id, "work_order_no": o.work_order_no,
        "supplier_id": o.supplier_id, "supplier_name": o.supplier_name,
        "process_name": o.process_name, "process_spec": o.process_spec,
        "qty": float(o.qty or 0), "unit": o.unit,
        "unit_price": float(o.unit_price or 0), "total_amount": float(o.total_amount or 0),
        "pay_method": o.pay_method, "pay_method_label": PAY_METHOD_LABEL.get(o.pay_method, o.pay_method or ""),
        "fund_account_id": o.fund_account_id, "fund_account_name": o.fund_account_name,
        "expected_delivery_date": o.expected_delivery_date.strftime("%Y-%m-%d") if o.expected_delivery_date else None,
        "status": o.status, "status_label": STATUS_LABEL.get(o.status, o.status or ""),
        "approval_instance_id": o.approval_instance_id,
        "finance_doc_id": o.finance_doc_id, "voucher_no": o.voucher_no,
        "remark": o.remark,
        "created_by": o.created_by,
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }


class OutsourceIn(BaseModel):
    order_id: int
    supplier_id: int
    process_name: str
    process_spec: Optional[str] = None
    qty: float
    unit: Optional[str] = "件"
    unit_price: float
    pay_method: Optional[str] = None  # CASH/TELEGRAPHIC/ACCEPTANCE
    fund_account_id: Optional[int] = None
    expected_delivery_date: Optional[str] = None
    work_order_id: Optional[int] = None
    remark: Optional[str] = None


def _gen_no(db: Session) -> str:
    cnt = db.query(OutsourceOrder).count()
    return f"OS-{bjt_now().strftime('%Y%m%d')}-{cnt + 1:04d}"


@router.post("")
def create(body: OutsourceIn,
          user: User = Depends(require_role("MANAGER", "OPERATION", "ADMIN")),
          db: Session = Depends(get_db)):
    """发起外协: 校验销售订单存在 → 状态 SUBMITTED 等待 GM 审批"""
    order = db.query(Order).get(body.order_id)
    if not order:
        raise HTTPException(400, "销售订单不存在,外协单必须关联有效销售订单")
    supplier = db.query(Supplier).get(body.supplier_id)
    if not supplier:
        raise HTTPException(400, "供应商不存在")
    if body.qty <= 0 or body.unit_price < 0:
        raise HTTPException(400, "数量/单价不合法")
    total = Decimal(str(body.qty)) * Decimal(str(body.unit_price))

    cust_name = ""
    if order.customer_id:
        from app.models.customer import Customer
        cust = db.query(Customer).get(order.customer_id)
        cust_name = cust.name if cust else ""

    fund_name = None
    if body.fund_account_id:
        fa = db.query(FundAccount).get(body.fund_account_id)
        fund_name = fa.name if fa else None

    edate = None
    if body.expected_delivery_date:
        try:
            edate = datetime.strptime(body.expected_delivery_date[:10], "%Y-%m-%d")
        except Exception:
            raise HTTPException(400, "交期日期格式错误")

    wo_no = None
    if body.work_order_id:
        from app.models.workshop import WorkOrder
        wo = db.query(WorkOrder).get(body.work_order_id)
        if wo:
            wo_no = wo.work_order_no if hasattr(wo, "work_order_no") else None

    o = OutsourceOrder(
        outsource_no=_gen_no(db),
        order_id=order.id, order_no=order.order_no,
        customer_id=order.customer_id, customer_name=cust_name,
        work_order_id=body.work_order_id, work_order_no=wo_no,
        supplier_id=supplier.id, supplier_name=supplier.name,
        process_name=body.process_name, process_spec=body.process_spec,
        qty=Decimal(str(body.qty)), unit=body.unit, unit_price=Decimal(str(body.unit_price)),
        total_amount=total,
        pay_method=body.pay_method,
        fund_account_id=body.fund_account_id, fund_account_name=fund_name,
        expected_delivery_date=edate,
        status="SUBMITTED", remark=body.remark, created_by=user.id,
    )
    db.add(o)
    db.flush()
    log_audit(db, user, "create", "outsource_order", o.id,
              after={"outsource_no": o.outsource_no, "order_no": order.order_no,
                     "supplier": supplier.name, "total": float(total)})
    db.commit()
    return Resp.ok({"id": o.id, "outsource_no": o.outsource_no})


@router.get("")
def list_(status: Optional[str] = None, supplier_id: Optional[int] = None,
          order_id: Optional[int] = None, page: int = 1, size: int = 20,
          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(OutsourceOrder)
    if status:
        q = q.filter(OutsourceOrder.status == status)
    if supplier_id:
        q = q.filter(OutsourceOrder.supplier_id == supplier_id)
    if order_id:
        q = q.filter(OutsourceOrder.order_id == order_id)
    total = q.count()
    rows = q.order_by(OutsourceOrder.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [_to_dict(o) for o in rows]}


@router.get("/{oid}")
def detail(oid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    o = db.query(OutsourceOrder).get(oid)
    if not o:
        raise HTTPException(404, "外协单不存在")
    return Resp.ok(_to_dict(o))


def _gen_ap_payable(db: Session, o: OutsourceOrder, user_id: int):
    """GM 审批通过: 生成应付单 + 凭证(借 5401 委外加工费 贷 2202 应付账款)"""
    now = bjt_now()
    amt = Decimal(str(o.total_amount or 0))
    # 1. 应付单
    last = db.query(FinanceDoc).filter(FinanceDoc.doc_type == "PAYABLE").order_by(FinanceDoc.id.desc()).first()
    seq = (last.id if last else 0) + 1
    order = db.query(Order).get(o.order_id)
    ap_doc = FinanceDoc(
        doc_no=f"AP-OS-{now.strftime('%Y%m%d')}-{seq:04d}",
        doc_type="PAYABLE", status="OPEN",
        related_type="OUTSOURCE", related_id=o.id,
        counterparty_type="SUPPLIER", counterparty_id=o.supplier_id,
        counterparty_name=o.supplier_name,
        amount=amt, settled_amount=0,
        account_date=now, source_event="outsource",
        company_id=order.company_id if order else None,
        remark=f"外协加工费:{o.outsource_no}({o.process_name})",
        extra={"outsource_no": o.outsource_no, "pay_method": o.pay_method},
    )
    db.add(ap_doc)
    db.flush()
    # 2. 凭证: 借 5401 委外加工费 贷 2202 应付账款
    acc_cost = _ledger(db, "5401", "委外加工费", "EXPENSE", "DEBIT")
    acc_ap = _ledger(db, "2202", "应付账款", "LIABILITY", "CREDIT")
    v = _post_voucher(db, now, f"外协加工费-{o.outsource_no}-{o.supplier_name}", [
        {"account_id": acc_cost.id, "summary": f"外协-{o.outsource_no}", "debit": amt, "credit": 0},
        {"account_id": acc_ap.id, "summary": f"外协-{o.outsource_no}", "debit": 0, "credit": amt},
    ], user_id)
    db.add(FinanceItem(finance_doc_id=ap_doc.id, account_id=acc_cost.id,
                       account_code=acc_cost.code, debit=amt, credit=0,
                       remark=f"外协凭证:{v.voucher_no}"))
    db.add(FinanceItem(finance_doc_id=ap_doc.id, account_id=acc_ap.id,
                       account_code=acc_ap.code, debit=0, credit=amt,
                       remark=f"外协凭证:{v.voucher_no}"))
    o.finance_doc_id = ap_doc.id
    o.voucher_no = v.voucher_no
    return ap_doc, v


@router.post("/{oid}/approve")
def approve(oid: int,
            user: User = Depends(require_role("GM", "ADMIN")),
            db: Session = Depends(get_db)):
    """GM 直审通过: 状态 APPROVED + 自动生成应付单 + 凭证"""
    o = db.query(OutsourceOrder).get(oid)
    if not o:
        raise HTTPException(404, "外协单不存在")
    if o.status != "SUBMITTED":
        raise HTTPException(400, f"状态{o.status}不可审批")
    o.status = "APPROVED"
    ap_doc, v = _gen_ap_payable(db, o, user.id)
    log_audit(db, user, "approve", "outsource_order", oid,
              before="SUBMITTED",
              after={"status": "APPROVED", "finance_doc_no": ap_doc.doc_no, "voucher_no": v.voucher_no})
    db.commit()
    return Resp.ok({"id": oid, "status": "APPROVED",
                    "finance_doc_no": ap_doc.doc_no, "voucher_no": v.voucher_no})


@router.post("/{oid}/reject")
def reject(oid: int, body: dict,
           user: User = Depends(require_role("GM", "ADMIN")),
           db: Session = Depends(get_db)):
    """GM 驳回"""
    o = db.query(OutsourceOrder).get(oid)
    if not o:
        raise HTTPException(404, "外协单不存在")
    if o.status != "SUBMITTED":
        raise HTTPException(400, f"状态{o.status}不可驳回")
    before = o.status
    o.status = "REJECTED"
    reason = (body or {}).get("reason", "")
    if reason:
        o.remark = (o.remark or "") + f"\n驳回原因: {reason}"
    log_audit(db, user, "reject", "outsource_order", oid, before=before, after="REJECTED")
    db.commit()
    return Resp.ok({"id": oid, "status": "REJECTED"})
