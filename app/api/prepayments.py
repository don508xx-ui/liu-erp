"""采购预付 - 关联采购单+选定出账账户, 自动生成资金流水+应付单(预付冲抵)+凭证"""
from typing import Optional
from datetime import datetime
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role
from app.api.approvals import bjt_now
from app.core.audit import log_audit
from app.schemas import Resp
from app.models.system import User
from app.models.fund import FundAccount, FundFlow
from app.models.purchase import Purchase, Supplier
from app.models.finance import FinanceDoc, Account
from app.models.prepayment import Prepayment

router = APIRouter(prefix="/api/prepayments", tags=["prepayments"])

PREPAY_STATUS = {"PAID": "已预付", "APPLIED": "已冲抵", "CANCELLED": "已作废"}


def _ledger(db: Session, code: str, name: str, type_: str, direction: str):
    acc = db.query(Account).filter(Account.code == code).first()
    if not acc:
        acc = Account(code=code, name=name, type=type_, direction=direction,
                      is_required=1, level=1, status="ACTIVE")
        db.add(acc)
        db.flush()
    return acc


def _fund_ledger_account(db: Session, fa) -> Account:
    """资金账户→记账科目: 现金→1001 / 银行→1002 / 承兑→1121"""
    m = {"CASH": ("1001", "库存现金"), "BANK": ("1002", "银行存款"), "ACCEPTANCE": ("1121", "应收票据")}
    code, name = m.get((fa.account_type or "BANK").upper(), ("1002", "银行存款"))
    return _ledger(db, code, name, "ASSET", "DEBIT")


def _post_voucher(db: Session, odate, summary: str, entries: list, user_id: int):
    from app.core.voucher_service import create_voucher, post_voucher
    v = create_voucher(db, {
        "period": odate.strftime("%Y-%m"), "voucher_date": odate,
        "summary": summary, "entries": entries,
    }, creator_id=user_id)
    post_voucher(db, v.id)
    return v


class PrepayIn(BaseModel):
    purchase_id: int
    amount: float
    fund_account_id: int
    pay_date: Optional[str] = None
    remark: Optional[str] = None


@router.post("")
def create_prepayment(body: PrepayIn, user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                      db: Session = Depends(get_db)):
    """采购预付: 校验采购单 → FundFlow OUT + 应付单(预付冲抵) + 凭证(借1123预付账款 贷1002/1001)"""
    po = db.query(Purchase).get(body.purchase_id)
    if not po:
        raise HTTPException(400, "采购单不存在")
    if body.amount <= 0:
        raise HTTPException(400, "预付金额必须大于0")
    fa = db.query(FundAccount).get(body.fund_account_id)
    if not fa:
        raise HTTPException(400, "出账账户不存在")
    sup = po.supplier
    sup_name = sup.name if sup else ""
    from datetime import datetime as _dt
    pdate = bjt_now()
    if body.pay_date:
        try:
            pdate = _dt.strptime(str(body.pay_date)[:10], "%Y-%m-%d")
        except Exception:
            pass
    seq = db.query(Prepayment).count() + 1
    prepay_no = f"PP-{pdate.strftime('%Y%m%d')}-{seq:03d}"
    pp = Prepayment(
        prepay_no=prepay_no, purchase_id=po.id, purchase_no=po.po_no,
        supplier_id=po.supplier_id, supplier_name=sup_name,
        amount=body.amount, fund_account_id=fa.id, fund_account_name=fa.name,
        pay_date=pdate, status="PAID", applied_amount=0,
        paid_by_user_id=user.id, remark=body.remark,
    )
    db.add(pp)
    db.flush()
    # 1. 资金流水 OUT
    db.add(FundFlow(fund_account_id=fa.id, direction="OUT", amount=body.amount,
                    expense_category="采购预付", counterparty=sup_name, occur_date=pdate,
                    summary=f"采购预付-{prepay_no}-{po.po_no}",
                    source_type="PREPAYMENT", source_id=pp.id))
    # 2. 应付单(预付冲抵) doc_type=PREPAYMENT
    doc_no = f"AP-PP-{pdate.strftime('%Y%m%d')}-{seq:04d}"
    doc = FinanceDoc(
        doc_no=doc_no, doc_type="PREPAYMENT", status="OPEN",
        related_type="PURCHASE", related_id=po.id,
        counterparty_type="SUPPLIER", counterparty_id=po.supplier_id,
        counterparty_name=sup_name, amount=body.amount, settled_amount=0,
        account_date=pdate, due_date=pdate, source_event="prepayment",
        remark=f"采购预付-{prepay_no}",
    )
    db.add(doc)
    db.flush()
    pp.finance_doc_id = doc.id
    # 3. 凭证: 借 1123预付账款 贷 1002银行存款/1001库存现金
    prepaid_acc = _ledger(db, "1123", "预付账款", "ASSET", "DEBIT")
    fund_acc = _fund_ledger_account(db, fa)
    v = _post_voucher(db, pdate, f"采购预付-{prepay_no}-{sup_name}", [
        {"account_id": prepaid_acc.id, "summary": f"预付-{prepay_no}", "debit": body.amount, "credit": 0,
         "aux_type": "SUPPLIER", "aux_id": po.supplier_id, "aux_name": sup_name},
        {"account_id": fund_acc.id, "summary": f"预付-{prepay_no}", "debit": 0, "credit": body.amount,
         "aux_type": "SUPPLIER", "aux_id": po.supplier_id, "aux_name": sup_name},
    ], user.id)
    pp.voucher_no = v.voucher_no
    log_audit(db, user, "create", "prepayment", pp.id,
              after={"prepay_no": prepay_no, "amount": body.amount, "doc_no": doc_no})
    db.commit()
    return Resp.ok({"id": pp.id, "prepay_no": prepay_no, "doc_no": doc_no, "voucher_no": v.voucher_no})


@router.get("")
def list_prepayments(supplier_id: Optional[int] = None, purchase_id: Optional[int] = None,
                     status: Optional[str] = None, page: int = 1, size: int = 20,
                     user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                     db: Session = Depends(get_db)):
    q = db.query(Prepayment)
    if supplier_id:
        q = q.filter(Prepayment.supplier_id == supplier_id)
    if purchase_id:
        q = q.filter(Prepayment.purchase_id == purchase_id)
    if status:
        q = q.filter(Prepayment.status == status)
    total = q.count()
    rows = q.order_by(Prepayment.id.desc()).offset((page - 1) * size).limit(size).all()
    items = [{
        "id": p.id, "prepay_no": p.prepay_no, "purchase_id": p.purchase_id, "purchase_no": p.purchase_no,
        "supplier_id": p.supplier_id, "supplier_name": p.supplier_name,
        "amount": float(p.amount or 0), "fund_account_id": p.fund_account_id, "fund_account_name": p.fund_account_name,
        "pay_date": p.pay_date.strftime("%Y-%m-%d") if p.pay_date else None,
        "status": p.status, "status_label": PREPAY_STATUS.get(p.status, p.status),
        "applied_amount": float(p.applied_amount or 0), "finance_doc_id": p.finance_doc_id,
        "voucher_no": p.voucher_no, "remark": p.remark,
    } for p in rows]
    return Resp.ok({"items": items, "total": total})


@router.get("/{pid}")
def get_prepayment(pid: int, user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                   db: Session = Depends(get_db)):
    p = db.query(Prepayment).get(pid)
    if not p:
        raise HTTPException(404, "预付单不存在")
    return Resp.ok({
        "id": p.id, "prepay_no": p.prepay_no, "purchase_id": p.purchase_id, "purchase_no": p.purchase_no,
        "supplier_id": p.supplier_id, "supplier_name": p.supplier_name,
        "amount": float(p.amount or 0), "fund_account_id": p.fund_account_id, "fund_account_name": p.fund_account_name,
        "pay_date": p.pay_date.strftime("%Y-%m-%d") if p.pay_date else None,
        "status": p.status, "status_label": PREPAY_STATUS.get(p.status, p.status),
        "applied_amount": float(p.applied_amount or 0), "finance_doc_id": p.finance_doc_id,
        "voucher_no": p.voucher_no, "remark": p.remark,
        "created_at": p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else None,
    })
