from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role
from app.core.audit import log_audit
from app.core.event_bus import emit
from app.models.system import User
from app.models.finance import PayrollRun
from app.schemas import Resp

router = APIRouter(prefix="/api/payroll", tags=["payroll"])


class PayrollItemIn(BaseModel):
    employee_id: int
    name: str
    position: str
    amount: float


class PayrollIn(BaseModel):
    period: str  # 2026-07
    items: List[PayrollItemIn]


@router.post("")
def create(body: PayrollIn, user: User = Depends(require_role("FINANCE", "ADMIN")),
           db: Session = Depends(get_db)):
    total = sum(it.amount for it in body.items)
    seq = db.query(PayrollRun).count() + 1
    pr = PayrollRun(
        run_no=f"PR-{body.period}-{seq:02d}",
        period=body.period, total_amount=total, status="DRAFT",
        items=[it.model_dump() for it in body.items],
    )
    db.add(pr)
    db.flush()
    log_audit(db, user, "create", "payroll_run", pr.id, after={"run_no": pr.run_no})
    db.commit()
    return Resp.ok({"id": pr.id, "run_no": pr.run_no, "total": float(total)})


@router.post("/{pid}/confirm")
def confirm(pid: int, user: User = Depends(require_role("FINANCE", "ADMIN")),
            db: Session = Depends(get_db)):
    pr = db.query(PayrollRun).get(pid)
    if not pr:
        raise HTTPException(404, "工资单不存在")
    if pr.status != "DRAFT":
        raise HTTPException(400, f"状态{pr.status}不可确认")
    log_audit(db, user, "state_change", "payroll_run", pid, before=pr.status, after="CONFIRMED")
    db.flush()
    emit(db, "payroll.confirmed", "payroll_run", pid, {"run_no": pr.run_no}, user)
    db.commit()
    return Resp.ok({"id": pid, "status": pr.status, "finance_doc_id": pr.finance_doc_id})


@router.get("")
def list_(user: User = Depends(require_role("FINANCE", "ADMIN", "GM")), db: Session = Depends(get_db)):
    rows = db.query(PayrollRun).order_by(PayrollRun.id.desc()).all()
    return {"code": 0, "data": [{
        "id": r.id, "run_no": r.run_no, "period": r.period,
        "total_amount": float(r.total_amount or 0), "status": r.status,
        "finance_doc_id": r.finance_doc_id,
        "item_count": len(r.items or []),
    } for r in rows]}
