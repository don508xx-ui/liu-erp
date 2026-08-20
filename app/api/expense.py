"""费用报销API - 蛋哥报账流程"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role, get_user_role_code
from app.core.audit import log_audit
from app.core.event_bus import emit
from app.models.system import User
from app.models.expense import ExpenseClaim
from app.api.approvals import bjt_now
from app.schemas import Resp

router = APIRouter(prefix="/api/expenses", tags=["expense"])


class ExpenseItemIn(BaseModel):
    date: str
    category: str  # 交通/餐饮/办公用品/差旅/其他
    amount: float
    remark: Optional[str] = None


class ExpenseIn(BaseModel):
    claim_type: str  # TRAVEL/MEAL/OFFICE/TRANSPORT/OTHER
    company_id: Optional[int] = None
    items: List[ExpenseItemIn]
    description: Optional[str] = None
    remark: Optional[str] = None


def _seq(db):
    obj = db.query(ExpenseClaim).order_by(ExpenseClaim.id.desc()).first()
    n = (obj.id if obj else 0) + 1
    return f"EC-{bjt_now().strftime('%Y%m%d')}-{n:04d}"


@router.post("")
def create(body: ExpenseIn, user: User = Depends(require_role("SALES", "OPERATION", "FINANCE", "ADMIN")),
          db: Session = Depends(get_db)):
    """员工提交报销申请"""
    total = sum(it.amount for it in body.items)
    ec = ExpenseClaim(
        claim_no=_seq(db), applicant_user_id=user.id,
        claim_type=body.claim_type, company_id=body.company_id,
        amount=total, status="DRAFT",
        items=[it.model_dump() for it in body.items],
        description=body.description, remark=body.remark,
    )
    db.add(ec)
    db.flush()
    log_audit(db, user, "create", "expense_claim", ec.id, after={"no": ec.claim_no, "amount": float(total)})
    db.commit()
    return Resp.ok({"id": ec.id, "claim_no": ec.claim_no, "amount": float(total)})


@router.post("/{eid}/submit")
def submit(eid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """提交报销申请(申请人提交)"""
    ec = db.query(ExpenseClaim).get(eid)
    if not ec:
        raise HTTPException(404, "报销单不存在")
    if ec.applicant_user_id != user.id:
        raise HTTPException(403, "仅申请人可提交")
    if ec.status not in ("DRAFT", "REJECTED"):
        raise HTTPException(400, f"状态{ec.status}不可提交")
    ec.status = "SUBMITTED"
    log_audit(db, user, "state_change", "expense_claim", eid, before="DRAFT", after="SUBMITTED")
    from app.api.approvals import start_flow
    inst = start_flow(db, "EXPENSE", eid, user)
    if inst:
        ec.approval_instance_id = inst.id
    db.commit()
    return Resp.ok({"id": eid, "status": "SUBMITTED"})


@router.post("/{eid}/approve")
def approve(eid: int, user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
           db: Session = Depends(get_db)):
    """审批报销单 - 金额>5000需GM终审,≤5000财务可终审"""
    ec = db.query(ExpenseClaim).get(eid)
    if not ec:
        raise HTTPException(404, "报销单不存在")
    if ec.status not in ("SUBMITTED", "APPROVED"):
        raise HTTPException(400, f"状态{ec.status}不可审批")

    role_code = get_user_role_code(user, db)
    amount = float(ec.amount or 0)

    # GM/ADMIN可以直接终审(从SUBMITTED或APPROVED到PAID)
    if role_code in ("GM", "ADMIN"):
        ec.status = "PAID"
        ec.approved_by_user_id = user.id
        ec.approved_at = bjt_now()
        log_audit(db, user, "approve", "expense_claim", eid, before="SUBMITTED/APPROVED", after="PAID")
        db.flush()
        emit(db, "expense.paid", "expense_claim", eid,
             {"claim_no": ec.claim_no, "amount": amount, "applicant_id": ec.applicant_user_id}, user)
        db.commit()
        return Resp.ok({"id": eid, "status": "PAID"})

    # 财务审批: SUBMITTED状态
    if ec.status == "SUBMITTED":
        if amount > 5000:
            # 大额报销(>5000): 财务只能初审,需GM终审
            ec.status = "APPROVED"
            log_audit(db, user, "approve", "expense_claim", eid, before="SUBMITTED", after="APPROVED")
            db.commit()
            return Resp.ok({"id": eid, "status": "APPROVED"})
        else:
            # 小额报销(≤5000): 财务可直接终审
            ec.status = "PAID"
            ec.approved_by_user_id = user.id
            ec.approved_at = bjt_now()
            log_audit(db, user, "approve", "expense_claim", eid, before="SUBMITTED", after="PAID")
            db.flush()
            emit(db, "expense.paid", "expense_claim", eid,
                 {"claim_no": ec.claim_no, "amount": amount, "applicant_id": ec.applicant_user_id}, user)
            db.commit()
            return Resp.ok({"id": eid, "status": "PAID"})

    # 财务尝试终审APPROVED状态(无权限,需GM)
    raise HTTPException(403, f"金额{amount}元需总经理终审")


@router.post("/{eid}/reject")
def reject(eid: int, body: dict, user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
          db: Session = Depends(get_db)):
    """驳回报销单"""
    ec = db.query(ExpenseClaim).get(eid)
    if not ec:
        raise HTTPException(404, "报销单不存在")
    if ec.status not in ("SUBMITTED", "APPROVED"):
        raise HTTPException(400, f"状态{ec.status}不可驳回")
    before = ec.status
    ec.status = "REJECTED"
    ec.remark = (ec.remark or "") + f"\n驳回原因: {body.get('reason', '')}"
    log_audit(db, user, "reject", "expense_claim", eid, before=before, after="REJECTED")
    db.commit()
    return Resp.ok({"id": eid, "status": "REJECTED"})


@router.get("")
def list_(applicant_id: Optional[int] = None, status: Optional[str] = None,
         page: int = 1, size: int = 20,
         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """查询报销单 - 非豁免角色只看自己的，applicant_id不可越权"""
    q = db.query(ExpenseClaim)
    role_code = get_user_role_code(user, db)
    EXEMPT = ("FINANCE", "ADMIN", "GM")
    if role_code not in EXEMPT:
        # 非豁免角色强制只看自己，忽略applicant_id参数防越权
        q = q.filter(ExpenseClaim.applicant_user_id == user.id)
    elif applicant_id:
        q = q.filter(ExpenseClaim.applicant_user_id == applicant_id)
    if status:
        q = q.filter(ExpenseClaim.status == status)
    total = q.count()
    rows = q.order_by(ExpenseClaim.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [_to_dict(db, e) for e in rows]}


@router.get("/{eid}")
def get(eid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ec = db.query(ExpenseClaim).get(eid)
    if not ec:
        raise HTTPException(404, "报销单不存在")
    role_code = get_user_role_code(user, db)
    if role_code not in ("FINANCE", "ADMIN", "GM") and ec.applicant_user_id != user.id:
        raise HTTPException(403, "无权查看他人报销单")
    return Resp.ok(_to_dict(db, ec, with_items=True))


def _to_dict(db, ec: ExpenseClaim, with_items=False) -> dict:
    applicant = db.query(User).filter(User.id == ec.applicant_user_id).first()
    approver = db.query(User).filter(User.id == ec.approved_by_user_id).first() if ec.approved_by_user_id else None
    d = {
        "id": ec.id, "claim_no": ec.claim_no,
        "applicant_user_id": ec.applicant_user_id,
        "applicant_name": applicant.name if applicant else "",
        "claim_type": ec.claim_type, "amount": float(ec.amount or 0),
        "status": ec.status, "company_id": ec.company_id,
        "approved_by_name": approver.name if approver else "",
        "approved_at": ec.approved_at.isoformat() if ec.approved_at else None,
        "description": ec.description, "remark": ec.remark,
        "created_at": ec.created_at.isoformat() if ec.created_at else None,
    }
    if with_items:
        d["items"] = ec.items
    return d