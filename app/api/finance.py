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
from app.models.system import User
from app.models.finance import FinanceDoc, WorkOrderCost, Account, PayrollRun
from app.models.order import Order
from app.models.workshop import WorkOrder, Completion
from app.api.approvals import bjt_now
from app.schemas import Resp

router = APIRouter(prefix="/api/finance", tags=["finance"])


class ReceiptIn(BaseModel):
    order_id: int
    amount: float
    company_id: Optional[int] = None  # 收款主体(双公司分流)
    remark: Optional[str] = None


# 收款登记(财务手工录入,触发receipt.created核销应收)
@router.post("/receipts")
def create_receipt(body: ReceiptIn, user: User = Depends(require_role("FINANCE", "ADMIN")),
                   db: Session = Depends(get_db)):
    o = db.query(Order).get(body.order_id)
    if not o:
        raise HTTPException(400, "订单不存在")
    # 校验收款金额不超过应收余额
    ar = db.query(FinanceDoc).filter(
        FinanceDoc.related_type == "ORDER",
        FinanceDoc.related_id == o.id,
        FinanceDoc.doc_type == "RECEIVABLE",
    ).first()
    if ar:
        remaining = float(ar.amount or 0) - float(ar.settled_amount or 0)
        if body.amount > remaining:
            raise HTTPException(400, f"收款金额{body.amount}超过应收余额{remaining}")
    # 单号生成: 用自增ID避免并发冲突
    max_doc = db.query(FinanceDoc).filter(FinanceDoc.doc_type == "RECEIPT").order_by(FinanceDoc.id.desc()).first()
    seq = (max_doc.id if max_doc else 0) + 1
    # 公司主体: 优先用传入的company_id, 否则用订单的company_id
    cid = body.company_id or o.company_id
    rc = FinanceDoc(
        doc_no=f"RC-{bjt_now().strftime('%Y%m%d')}-{seq:04d}",
        doc_type="RECEIPT", status="SETTLED",
        related_type="ORDER", related_id=o.id,
        counterparty_type="CUSTOMER", counterparty_id=o.customer_id,
        amount=body.amount, settled_amount=body.amount,
        account_date=bjt_now(), source_event="manual",
        company_id=cid,
        remark=body.remark,
    )
    db.add(rc)
    db.flush()
    log_audit(db, user, "create", "finance_doc", rc.id, after={"doc_no": rc.doc_no})
    db.flush()
    emit(db, "receipt.created", "finance_doc", rc.id, {"doc_no": rc.doc_no}, user)
    db.commit()
    return Resp.ok({"id": rc.id, "doc_no": rc.doc_no})


@router.get("/docs")
def list_docs(doc_type: Optional[str] = None, status: Optional[str] = None,
              page: int = 1, size: int = 20,
              user: User = Depends(require_role("FINANCE", "ADMIN", "GM")), db: Session = Depends(get_db)):
    q = db.query(FinanceDoc)
    if doc_type:
        q = q.filter(FinanceDoc.doc_type == doc_type)
    if status:
        q = q.filter(FinanceDoc.status == status)
    total = q.count()
    rows = q.order_by(FinanceDoc.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [{
        "id": d.id, "doc_no": d.doc_no, "doc_type": d.doc_type, "status": d.status,
        "related_type": d.related_type, "related_id": d.related_id,
        "counterparty_name": d.counterparty_name,
        "amount": float(d.amount or 0), "settled_amount": float(d.settled_amount or 0),
        "account_date": d.account_date.isoformat() if d.account_date else None,
        "due_date": d.due_date.isoformat() if d.due_date else None,
        "source_event": d.source_event,
    } for d in rows]}


@router.get("/accounts")
def accounts(user: User = Depends(require_role("FINANCE", "ADMIN")), db: Session = Depends(get_db)):
    rows = db.query(Account).filter(Account.status == "ACTIVE").order_by(Account.code).all()
    return {"code": 0, "data": [{
        "id": a.id, "code": a.code, "name": a.name, "type": a.type,
        "direction": a.direction, "is_required": a.is_required, "level": a.level,
    } for a in rows]}


class AccountIn(BaseModel):
    code: str
    name: str
    type: str
    direction: str = "DEBIT"
    parent_code: Optional[str] = None
    is_required: int = 0
    level: int = 1


@router.post("/accounts")
def create_account(body: AccountIn, user: User = Depends(require_role("FINANCE", "ADMIN")),
                    db: Session = Depends(get_db)):
    exists = db.query(Account).filter(Account.code == body.code).first()
    if exists:
        raise HTTPException(400, "科目编码已存在")
    acc = Account(
        code=body.code, name=body.name, type=body.type,
        direction=body.direction, parent_code=body.parent_code,
        is_required=body.is_required, level=body.level, status="ACTIVE"
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    log_audit(db, user, "create", "account", acc.id, after={"code": acc.code, "name": acc.name})
    return {"code": 0, "data": {"id": acc.id, "code": acc.code, "name": acc.name}}


@router.put("/accounts/{account_id}")
def update_account(account_id: int, body: AccountIn,
                   user: User = Depends(require_role("FINANCE", "ADMIN")),
                   db: Session = Depends(get_db)):
    acc = db.query(Account).filter(Account.id == account_id).first()
    if not acc:
        raise HTTPException(404, "科目不存在")
    old = {"code": acc.code, "name": acc.name}
    acc.name = body.name
    acc.type = body.type
    acc.direction = body.direction
    acc.parent_code = body.parent_code
    acc.is_required = body.is_required
    acc.level = body.level
    log_audit(db, user, "update", "account", account_id, before=old,
              after={"code": acc.code, "name": acc.name})
    db.commit()
    return {"code": 0}


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int,
                   user: User = Depends(require_role("FINANCE", "ADMIN")),
                   db: Session = Depends(get_db)):
    acc = db.query(Account).filter(Account.id == account_id).first()
    if not acc:
        raise HTTPException(404, "科目不存在")
    if acc.is_required:
        raise HTTPException(400, "必填科目不能删除")
    # F4: 检查科目是否已有余额/被凭证引用, 有则禁止删除(置INACTIVE)
    from app.models.voucher import AccountBalance, VoucherEntry
    has_balance = db.query(AccountBalance).filter(AccountBalance.account_id == account_id).first()
    if has_balance and (has_balance.debit_amount or has_balance.credit_amount
                        or has_balance.opening_debit or has_balance.opening_credit
                        or has_balance.closing_debit or has_balance.closing_credit):
        raise HTTPException(400, "该科目存在余额, 不能删除")
    has_ref = db.query(VoucherEntry).filter(VoucherEntry.account_id == account_id).first()
    if has_ref:
        raise HTTPException(400, "该科目已被凭证引用, 不能删除")
    acc.status = "INACTIVE"
    log_audit(db, user, "delete", "account", account_id,
              before={"code": acc.code, "name": acc.name})
    db.commit()
    return {"code": 0}


@router.get("/accounts/{account_id}")
def get_account(account_id: int,
                user: User = Depends(require_role("FINANCE", "ADMIN")),
                db: Session = Depends(get_db)):
    acc = db.query(Account).filter(Account.id == account_id).first()
    if not acc:
        raise HTTPException(404, "科目不存在")
    return {"code": 0, "data": {
        "id": acc.id, "code": acc.code, "name": acc.name, "type": acc.type,
        "direction": acc.direction, "parent_code": acc.parent_code,
        "is_required": acc.is_required, "level": acc.level, "status": acc.status
    }}


# 工单成本明细
@router.get("/work-order-costs/{wid}")
def wo_costs(wid: int, user: User = Depends(require_role("FINANCE", "MANAGER", "ADMIN")), db: Session = Depends(get_db)):
    rows = db.query(WorkOrderCost).filter(WorkOrderCost.work_order_id == wid).all()
    by_type = {}
    total = 0
    for c in rows:
        by_type[c.cost_type] = by_type.get(c.cost_type, 0) + float(c.amount or 0)
        total += float(c.amount or 0)
    return {"code": 0, "data": {
        "work_order_id": wid, "total_cost": total, "breakdown": by_type,
        "details": [{"cost_type": c.cost_type, "amount": float(c.amount or 0),
                     "source_doc_type": c.source_doc_type, "occurred_at": c.occurred_at.isoformat() if c.occurred_at else None}
                    for c in rows],
    }}


# 订单利润分析
@router.get("/profit/order/{oid}")
def order_profit(oid: int, user: User = Depends(require_role("FINANCE", "ADMIN", "GM")), db: Session = Depends(get_db)):
    o = db.query(Order).get(oid)
    if not o:
        raise HTTPException(404, "订单不存在")
    wo_ids = [w.id for w in db.query(WorkOrder).filter(WorkOrder.order_id == oid).all()]
    total_cost = 0
    breakdown = {}
    if wo_ids:
        costs = db.query(WorkOrderCost).filter(WorkOrderCost.work_order_id.in_(wo_ids)).all()
        for c in costs:
            breakdown[c.cost_type] = breakdown.get(c.cost_type, 0) + float(c.amount or 0)
            total_cost += float(c.amount or 0)
    revenue = float(o.total_amount or 0)
    profit = revenue - total_cost
    margin = round(profit / revenue * 100, 2) if revenue else 0
    return {"code": 0, "data": {
        "order_id": oid, "order_no": o.order_no, "revenue": revenue,
        "cost": total_cost, "cost_breakdown": breakdown,
        "profit": profit, "gross_margin_pct": margin,
    }}


# 应收账龄
@router.get("/receivables/aging")
def ar_aging(user: User = Depends(require_role("FINANCE", "ADMIN", "GM")), db: Session = Depends(get_db)):
    rows = db.query(FinanceDoc).filter(
        FinanceDoc.doc_type == "RECEIVABLE",
        FinanceDoc.status.in_(["OPEN", "DRAFT"])
    ).all()
    now = bjt_now()
    aging = {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
    for r in rows:
        remaining = float(r.amount or 0) - float(r.settled_amount or 0)
        if remaining <= 0:
            continue
        days = (now - (r.account_date or now)).days
        if days <= 30:
            aging["0-30"] += remaining
        elif days <= 60:
            aging["31-60"] += remaining
        elif days <= 90:
            aging["61-90"] += remaining
        else:
            aging["90+"] += remaining
    return {"code": 0, "data": aging}
