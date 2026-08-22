"""承兑汇票台账 - 收票/背书/贴现/到期托收(素人化: 每个动作自动生成流水+凭证)"""
from typing import Optional
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role
from app.schemas import Resp
from app.core.audit import log_audit
from app.models.system import User
from app.models.fund import AcceptanceBill, FundAccount, FundFlow

BJT = timezone(timedelta(hours=8))
def bjt_now():
    return datetime.now(BJT).replace(tzinfo=None)

router = APIRouter(prefix="/api/acceptances", tags=["acceptances"])

BILL_STATUS = {"HOLDING": "持有中", "ENDORSED": "已背书", "DISCOUNTED": "已贴现", "SETTLED": "已托收"}


def _acceptance_account(db: Session) -> FundAccount:
    acc = db.query(FundAccount).filter(FundAccount.account_type == "ACCEPTANCE").first()
    if not acc:
        raise HTTPException(400, "未找到承兑汇票资金账户")
    return acc


def _bank_account(db: Session, fund_account_id: Optional[int]) -> FundAccount:
    if fund_account_id:
        acc = db.query(FundAccount).get(fund_account_id)
        if acc:
            return acc
    acc = db.query(FundAccount).filter(FundAccount.account_type == "BANK").first()
    if not acc:
        raise HTTPException(400, "未找到银行公账账户")
    return acc


def _ledger(db: Session, code: str, name: str, type_: str, direction: str):
    from app.models.finance import Account
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


class BillIn(BaseModel):
    bill_no: str
    amount: float
    drawer: Optional[str] = None       # 从谁家收的(客户)
    receive_date: Optional[str] = None
    issue_date: Optional[str] = None
    due_date: str                      # 到期日(必填)
    remark: Optional[str] = None


@router.get("")
def list_bills(status: Optional[str] = None, page: int = 1, size: int = 20,
               user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
               db: Session = Depends(get_db)):
    from datetime import timedelta
    q = db.query(AcceptanceBill)
    if status:
        q = q.filter(AcceptanceBill.status == status)
    total = q.count()
    rows = q.order_by(AcceptanceBill.due_date).offset((page - 1) * size).limit(size).all()
    now = bjt_now()
    items = []
    for b in rows:
        days = (b.due_date - now).days if b.due_date else None
        items.append({
            "id": b.id, "bill_no": b.bill_no, "amount": float(b.amount or 0),
            "drawer": b.drawer, "receive_date": b.receive_date.strftime("%Y-%m-%d") if b.receive_date else None,
            "due_date": b.due_date.strftime("%Y-%m-%d") if b.due_date else None,
            "status": b.status, "status_label": BILL_STATUS.get(b.status, b.status),
            "days_to_due": days, "endorse_to": b.endorse_to, "discount_fee": float(b.discount_fee or 0),
            "remark": b.remark,
        })
    # 预警统计(持有中: 30天内到期/已逾期)
    holding = db.query(AcceptanceBill).filter(AcceptanceBill.status == "HOLDING").all()
    due_soon = sum(1 for b in holding if b.due_date and 0 <= (b.due_date - now).days <= 30)
    overdue = sum(1 for b in holding if b.due_date and (b.due_date - now).days < 0)
    holding_amt = sum(float(b.amount or 0) for b in holding)
    return Resp.ok({"items": items, "total": total,
                    "alert": {"holding_count": len(holding), "holding_amt": round(holding_amt, 2),
                              "due_soon": due_soon, "overdue": overdue}})


@router.post("")
def create_bill(body: BillIn, user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                db: Session = Depends(get_db)):
    """收票登记: 承兑账户流入 + 凭证(借 应收票据 贷 应收账款)"""
    from datetime import datetime as _dt
    if db.query(AcceptanceBill).filter(AcceptanceBill.bill_no == body.bill_no).first():
        raise HTTPException(400, f"票号 {body.bill_no} 已登记过")
    if body.amount <= 0:
        raise HTTPException(400, "金额必须大于0")
    try:
        due = _dt.strptime(body.due_date[:10], "%Y-%m-%d")
    except Exception:
        raise HTTPException(400, "到期日格式错误")
    def _pd(s):
        if not s:
            return None
        try:
            return _dt.strptime(str(s)[:10], "%Y-%m-%d")
        except Exception:
            return None
    now = bjt_now()
    bill = AcceptanceBill(bill_no=body.bill_no, amount=body.amount, drawer=body.drawer,
                          receive_date=_pd(body.receive_date) or now, issue_date=_pd(body.issue_date),
                          due_date=due, status="HOLDING", remark=body.remark, created_by=user.id)
    db.add(bill)
    db.flush()
    acc = _acceptance_account(db)
    db.add(FundFlow(fund_account_id=acc.id, direction="IN", amount=body.amount,
                    counterparty=body.drawer or "客户", occur_date=bill.receive_date,
                    summary=f"收承兑汇票-{body.bill_no}", source_type="ACCEPTANCE_IN", source_id=bill.id))
    nb = _ledger(db, "1121", "应收票据", "ASSET", "DEBIT")
    ar = _ledger(db, "1122", "应收账款", "ASSET", "DEBIT")
    v = _post_voucher(db, bill.receive_date, f"收承兑汇票-{body.bill_no}-{body.drawer or ''}", [
        {"account_id": nb.id, "summary": f"收票-{body.bill_no}", "debit": body.amount, "credit": 0},
        {"account_id": ar.id, "summary": f"收票-{body.bill_no}", "debit": 0, "credit": body.amount},
    ], user.id)
    log_audit(db, user, "create", "acceptance_bill", bill.id,
              after={"bill_no": body.bill_no, "amount": body.amount})
    db.commit()
    return Resp.ok({"id": bill.id, "voucher_no": v.voucher_no})


class EndorseIn(BaseModel):
    endorse_to: str                    # 背书给谁(供应商)
    endorse_date: Optional[str] = None
    payable_doc_id: Optional[int] = None  # 可选: 抵付哪张应付单


@router.post("/{bill_id}/endorse")
def endorse_bill(bill_id: int, body: EndorseIn,
                 user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                 db: Session = Depends(get_db)):
    """背书转让: 承兑流出 + 凭证(借 应付账款 贷 应收票据), 可选联动核减应付单"""
    from datetime import datetime as _dt
    bill = db.query(AcceptanceBill).get(bill_id)
    if not bill or bill.status != "HOLDING":
        raise HTTPException(400, "票据不存在或不在持有状态")
    if not body.endorse_to:
        raise HTTPException(400, "请填写背书去向(供应商)")
    edate = bjt_now()
    if body.endorse_date:
        try:
            edate = _dt.strptime(body.endorse_date[:10], "%Y-%m-%d")
        except Exception:
            pass
    amt = float(bill.amount or 0)
    bill.status = "ENDORSED"
    bill.endorse_to = body.endorse_to
    bill.endorse_date = edate
    acc = _acceptance_account(db)
    db.add(FundFlow(fund_account_id=acc.id, direction="OUT", amount=amt,
                    counterparty=body.endorse_to, occur_date=edate,
                    summary=f"背书转让-{bill.bill_no}→{body.endorse_to}",
                    source_type="ACCEPTANCE_ENDORSE", source_id=bill.id))
    # 可选: 联动核减应付单
    settled_doc_no = None
    if body.payable_doc_id:
        from app.models.finance import FinanceDoc
        doc = db.query(FinanceDoc).get(body.payable_doc_id)
        if doc and doc.doc_type == "PAYABLE":
            remaining = float(doc.amount or 0) - float(doc.settled_amount or 0)
            use = min(amt, remaining)
            doc.settled_amount = float(doc.settled_amount or 0) + use
            if doc.settled_amount >= float(doc.amount or 0) - 0.005:
                doc.status = "SETTLED"
                doc.settled_at = edate
            settled_doc_no = doc.doc_no
    nb = _ledger(db, "1121", "应收票据", "ASSET", "DEBIT")
    ap = _ledger(db, "2202", "应付账款", "LIABILITY", "CREDIT")
    v = _post_voucher(db, edate, f"承兑背书-{bill.bill_no}→{body.endorse_to}", [
        {"account_id": ap.id, "summary": f"背书-{bill.bill_no}", "debit": amt, "credit": 0},
        {"account_id": nb.id, "summary": f"背书-{bill.bill_no}", "debit": 0, "credit": amt},
    ], user.id)
    log_audit(db, user, "endorse", "acceptance_bill", bill.id,
              after={"bill_no": bill.bill_no, "to": body.endorse_to, "settled_doc": settled_doc_no})
    db.commit()
    return Resp.ok({"voucher_no": v.voucher_no, "settled_doc": settled_doc_no})


class DiscountIn(BaseModel):
    received_amount: float             # 实收金额(贴息=票面-实收, 自动算)
    fund_account_id: Optional[int] = None  # 贴入哪个公账
    discount_date: Optional[str] = None


@router.post("/{bill_id}/discount")
def discount_bill(bill_id: int, body: DiscountIn,
                  user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                  db: Session = Depends(get_db)):
    """贴现: 承兑流出票面 + 银行流入实收 + 贴息进财务费用, 一张凭证全自动"""
    from datetime import datetime as _dt
    bill = db.query(AcceptanceBill).get(bill_id)
    if not bill or bill.status != "HOLDING":
        raise HTTPException(400, "票据不存在或不在持有状态")
    amt = float(bill.amount or 0)
    if body.received_amount <= 0 or body.received_amount > amt:
        raise HTTPException(400, f"实收金额须在 0~{amt} 之间")
    fee = round(amt - body.received_amount, 2)
    ddate = bjt_now()
    if body.discount_date:
        try:
            ddate = _dt.strptime(body.discount_date[:10], "%Y-%m-%d")
        except Exception:
            pass
    bill.status = "DISCOUNTED"
    bill.discount_date = ddate
    bill.discount_fee = fee
    acc = _acceptance_account(db)
    bank = _bank_account(db, body.fund_account_id)
    db.add(FundFlow(fund_account_id=acc.id, direction="OUT", amount=amt,
                    counterparty=bill.drawer or "银行", occur_date=ddate,
                    summary=f"贴现出票-{bill.bill_no}", source_type="ACCEPTANCE_DISCOUNT", source_id=bill.id))
    db.add(FundFlow(fund_account_id=bank.id, direction="IN", amount=body.received_amount,
                    counterparty=bill.drawer or "银行", occur_date=ddate,
                    summary=f"贴现实收-{bill.bill_no}", source_type="ACCEPTANCE_DISCOUNT", source_id=bill.id))
    if fee > 0.005:
        db.add(FundFlow(fund_account_id=bank.id, direction="OUT", amount=fee,
                        expense_category="融资成本", counterparty="贴现银行", occur_date=ddate,
                        summary=f"贴现贴息-{bill.bill_no}", source_type="ACCEPTANCE_DISCOUNT", source_id=bill.id))
    nb = _ledger(db, "1121", "应收票据", "ASSET", "DEBIT")
    bank_acc = _ledger(db, "1002", "银行存款", "ASSET", "DEBIT")
    entries = [
        {"account_id": bank_acc.id, "summary": f"贴现-{bill.bill_no}", "debit": body.received_amount, "credit": 0},
    ]
    if fee > 0.005:
        fee_acc = _ledger(db, "6603", "财务费用", "EXPENSE", "DEBIT")
        entries.append({"account_id": fee_acc.id, "summary": f"贴现贴息-{bill.bill_no}", "debit": fee, "credit": 0})
    entries.append({"account_id": nb.id, "summary": f"贴现-{bill.bill_no}", "debit": 0, "credit": amt})
    v = _post_voucher(db, ddate, f"承兑贴现-{bill.bill_no}", entries, user.id)
    log_audit(db, user, "discount", "acceptance_bill", bill.id,
              after={"bill_no": bill.bill_no, "received": body.received_amount, "fee": fee})
    db.commit()
    return Resp.ok({"voucher_no": v.voucher_no, "fee": fee})


@router.post("/{bill_id}/settle")
def settle_bill(bill_id: int, fund_account_id: Optional[int] = None,
                user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                db: Session = Depends(get_db)):
    """到期托收: 承兑流出 + 银行全额流入, 一键完成"""
    bill = db.query(AcceptanceBill).get(bill_id)
    if not bill or bill.status != "HOLDING":
        raise HTTPException(400, "票据不存在或不在持有状态")
    amt = float(bill.amount or 0)
    now = bjt_now()
    bill.status = "SETTLED"
    bill.settle_date = now
    acc = _acceptance_account(db)
    bank = _bank_account(db, fund_account_id)
    db.add(FundFlow(fund_account_id=acc.id, direction="OUT", amount=amt,
                    counterparty=bill.drawer or "承兑银行", occur_date=now,
                    summary=f"到期托收-{bill.bill_no}", source_type="ACCEPTANCE_SETTLE", source_id=bill.id))
    db.add(FundFlow(fund_account_id=bank.id, direction="IN", amount=amt,
                    counterparty=bill.drawer or "承兑银行", occur_date=now,
                    summary=f"托收到账-{bill.bill_no}", source_type="ACCEPTANCE_SETTLE", source_id=bill.id))
    nb = _ledger(db, "1121", "应收票据", "ASSET", "DEBIT")
    bank_acc = _ledger(db, "1002", "银行存款", "ASSET", "DEBIT")
    v = _post_voucher(db, now, f"承兑到期托收-{bill.bill_no}", [
        {"account_id": bank_acc.id, "summary": f"托收-{bill.bill_no}", "debit": amt, "credit": 0},
        {"account_id": nb.id, "summary": f"托收-{bill.bill_no}", "debit": 0, "credit": amt},
    ], user.id)
    log_audit(db, user, "settle", "acceptance_bill", bill.id, after={"bill_no": bill.bill_no})
    db.commit()
    return Resp.ok({"voucher_no": v.voucher_no})
