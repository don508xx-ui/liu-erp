"""借款申请API - 备用金/周转金,财务支付时自动生成FundFlow OUT+凭证"""
from typing import Optional
from datetime import datetime as _dt
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role, get_user_role_code
from app.core.audit import log_audit
from app.models.system import User
from app.models.loan import LoanRequest
from app.models.fund import FundAccount, FundFlow
from app.api.approvals import bjt_now, start_flow
from app.schemas import Resp

router = APIRouter(prefix="/api/loans", tags=["loans"])

LOAN_STATUS = {
    "SUBMITTED": "审批中", "APPROVED": "待支付", "REJECTED": "已驳回",
    "PAID": "已支付", "CLEARED": "已核销",
}
LOAN_TYPE = {"PETTY_CASH": "备用金", "TURN_OVER": "周转金"}


def _seq(db: Session) -> str:
    obj = db.query(LoanRequest).order_by(LoanRequest.id.desc()).first()
    n = (obj.id if obj else 0) + 1
    return f"LN-{bjt_now().strftime('%Y%m%d')}-{n:04d}"


def _ledger(db: Session, code: str, name: str, type_: str, direction: str):
    from app.models.finance import Account
    acc = db.query(Account).filter(Account.code == code).first()
    if not acc:
        acc = Account(code=code, name=name, type=type_, direction=direction,
                      is_required=1, level=1, status="ACTIVE")
        db.add(acc); db.flush()
    return acc


def _post_voucher(db: Session, odate, summary: str, entries: list, user_id: int):
    from app.core.voucher_service import create_voucher, post_voucher
    v = create_voucher(db, {
        "period": odate.strftime("%Y-%m"), "voucher_date": odate,
        "summary": summary, "entries": entries,
    }, creator_id=user_id)
    post_voucher(db, v.id)
    return v


def _to_dict(db: Session, ln: LoanRequest) -> dict:
    applicant = db.query(User).filter(User.id == ln.applicant_user_id).first()
    paid_by = db.query(User).filter(User.id == ln.paid_by_user_id).first() if ln.paid_by_user_id else None
    return {
        "id": ln.id, "loan_no": ln.loan_no,
        "applicant_user_id": ln.applicant_user_id,
        "applicant_name": ln.applicant_name or (applicant.name if applicant else ""),
        "department": ln.department,
        "loan_type": ln.loan_type,
        "loan_type_label": LOAN_TYPE.get(ln.loan_type, ln.loan_type or ""),
        "amount": float(ln.amount or 0),
        "fund_account_id": ln.fund_account_id,
        "fund_account_name": ln.fund_account_name,
        "purpose": ln.purpose,
        "expected_return_date": ln.expected_return_date.strftime("%Y-%m-%d") if ln.expected_return_date else None,
        "status": ln.status,
        "status_label": LOAN_STATUS.get(ln.status, ln.status or ""),
        "approval_instance_id": ln.approval_instance_id,
        "paid_at": ln.paid_at.isoformat() if ln.paid_at else None,
        "paid_by_name": paid_by.name if paid_by else "",
        "settled_at": ln.settled_at.isoformat() if ln.settled_at else None,
        "finance_doc_id": ln.finance_doc_id,
        "remark": ln.remark,
        "created_at": ln.created_at.isoformat() if ln.created_at else None,
    }


class LoanIn(BaseModel):
    loan_type: str  # PETTY_CASH / TURN_OVER
    amount: float
    fund_account_id: Optional[int] = None
    purpose: Optional[str] = None
    expected_return_date: Optional[str] = None
    department: Optional[str] = None
    remark: Optional[str] = None


@router.post("")
def create(body: LoanIn,
           user: User = Depends(require_role("SALES", "OPERATION", "FINANCE", "MANAGER",
                                              "DEPARTMENT_HEAD", "GM", "ADMIN")),
           db: Session = Depends(get_db)):
    """员工发起借款申请 → 自动发审批(无审批流则置 APPROVED 待支付)"""
    if body.amount <= 0:
        raise HTTPException(400, "借款金额必须大于0")
    if body.loan_type not in LOAN_TYPE:
        raise HTTPException(400, "借款类型必须为 PETTY_CASH/TURN_OVER")
    fund_acc = None
    if body.fund_account_id:
        fund_acc = db.query(FundAccount).get(body.fund_account_id)
        if not fund_acc:
            raise HTTPException(400, "所选借款账户不存在")
    exp_date = None
    if body.expected_return_date:
        try:
            exp_date = _dt.strptime(body.expected_return_date[:10], "%Y-%m-%d")
        except Exception:
            raise HTTPException(400, "预计还款日格式错误")
    ln = LoanRequest(
        loan_no=_seq(db), applicant_user_id=user.id, applicant_name=user.name,
        department=body.department, loan_type=body.loan_type, amount=body.amount,
        fund_account_id=body.fund_account_id,
        fund_account_name=fund_acc.name if fund_acc else None,
        purpose=body.purpose, expected_return_date=exp_date,
        status="SUBMITTED", remark=body.remark,
    )
    db.add(ln); db.flush()
    # 启动审批流 - 无审批流直接置 APPROVED 待支付
    inst = start_flow(db, "LOAN", ln.id, user, allow_reopen=False)
    if inst:
        ln.approval_instance_id = inst.id
    else:
        ln.status = "APPROVED"
    log_audit(db, user, "create", "loan_request", ln.id,
              after={"loan_no": ln.loan_no, "amount": float(body.amount)})
    db.commit()
    return Resp.ok({"id": ln.id, "loan_no": ln.loan_no, "status": ln.status})


@router.get("")
def list_(status: Optional[str] = None, page: int = 1, size: int = 20,
          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """借款申请列表 - 非豁免角色只看自己的"""
    q = db.query(LoanRequest)
    role_code = get_user_role_code(user, db)
    if role_code not in ("FINANCE", "ADMIN", "GM"):
        q = q.filter(LoanRequest.applicant_user_id == user.id)
    if status:
        q = q.filter(LoanRequest.status == status)
    total = q.count()
    rows = q.order_by(LoanRequest.id.desc()).offset((page - 1) * size).limit(size).all()
    return Resp.ok({"items": [_to_dict(db, r) for r in rows], "total": total})


@router.get("/{lid}")
def detail(lid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ln = db.query(LoanRequest).get(lid)
    if not ln:
        raise HTTPException(404, "借款申请不存在")
    role_code = get_user_role_code(user, db)
    if role_code not in ("FINANCE", "ADMIN", "GM") and ln.applicant_user_id != user.id:
        raise HTTPException(403, "无权查看他人借款申请")
    return Resp.ok(_to_dict(db, ln))


@router.post("/{lid}/pay")
def pay(lid: int, user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
        db: Session = Depends(get_db)):
    """财务支付 - FundFlow OUT + 凭证(借 1221其他应收款-员工 贷 银行存款/库存现金)"""
    ln = db.query(LoanRequest).get(lid)
    if not ln:
        raise HTTPException(404, "借款申请不存在")
    if ln.status != "APPROVED":
        raise HTTPException(400, f"状态{ln.status}不可支付,需先审批通过")
    if not ln.fund_account_id:
        raise HTTPException(400, "该借款申请未选择借款账户,无法支付")
    acc = db.query(FundAccount).get(ln.fund_account_id)
    if not acc:
        raise HTTPException(400, "所选借款账户不存在")
    amt = float(ln.amount or 0)
    now = bjt_now()
    ln.status = "PAID"; ln.paid_at = now; ln.paid_by_user_id = user.id
    db.add(FundFlow(fund_account_id=acc.id, direction="OUT", amount=amt,
                    expense_category="其他", counterparty=ln.applicant_name or "员工",
                    occur_date=now,
                    summary=f"员工借款-{ln.loan_no}-{ln.applicant_name or ''}",
                    source_type="LOAN_PAY", source_id=ln.id))
    # 凭证: 借 1221其他应收款-员工 贷 银行存款(1002)/库存现金(1001)
    ar_emp = _ledger(db, "1221", "其他应收款-员工", "ASSET", "DEBIT")
    credit_code, credit_name = ("1001", "库存现金") if acc.account_type == "CASH" else ("1002", "银行存款")
    credit_acc = _ledger(db, credit_code, credit_name, "ASSET", "DEBIT")
    v = _post_voucher(db, now, f"员工借款-{ln.loan_no}-{ln.applicant_name or ''}", [
        {"account_id": ar_emp.id, "summary": f"借款-{ln.loan_no}", "debit": amt, "credit": 0},
        {"account_id": credit_acc.id, "summary": f"借款-{ln.loan_no}", "debit": 0, "credit": amt},
    ], user.id)
    ln.finance_doc_id = v.id
    log_audit(db, user, "pay", "loan_request", ln.id,
              before="APPROVED", after="PAID")
    db.commit()
    return Resp.ok({"id": ln.id, "status": "PAID", "voucher_no": v.voucher_no})


@router.post("/{lid}/clear")
def clear(lid: int, user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
          db: Session = Depends(get_db)):
    """归还核销 - 状态置 CLEARED"""
    ln = db.query(LoanRequest).get(lid)
    if not ln:
        raise HTTPException(404, "借款申请不存在")
    if ln.status != "PAID":
        raise HTTPException(400, f"状态{ln.status}不可核销,需先支付")
    before = ln.status
    ln.status = "CLEARED"; ln.settled_at = bjt_now()
    log_audit(db, user, "clear", "loan_request", ln.id, before=before, after="CLEARED")
    db.commit()
    return Resp.ok({"id": ln.id, "status": "CLEARED"})
