from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role
from app.core.audit import log_audit
from app.models.system import User
from app.models.finance import Employee
from app.schemas import Resp

router = APIRouter(prefix="/api/employees", tags=["employees"])


class EmployeeIn(BaseModel):
    name: str
    gender: str = "男"
    position: str = ""
    department: str = "管理"
    base_salary: float = 0
    social_security: float = 0
    housing_fund: float = 0
    id_number: str = ""
    phone: str = ""
    bank_name: str = ""
    bank_branch: str = ""
    bank_account: str = ""
    certificates: str = ""
    hire_date: Optional[str] = None
    remark: str = ""


@router.get("")
def list_employees(status: Optional[str] = None,
                   user: User = Depends(require_role("FINANCE", "ADMIN", "GM", "HR", "OPERATION")),
                   db: Session = Depends(get_db)):
    q = db.query(Employee)
    if status:
        q = q.filter(Employee.status == status)
    else:
        q = q.filter(Employee.status == "ACTIVE")
    rows = q.order_by(Employee.department, Employee.name).all()
    return Resp.ok([{
        "id": e.id, "name": e.name, "gender": e.gender or "男",
        "position": e.position, "department": e.department,
        "base_salary": float(e.base_salary or 0),
        "social_security": float(e.social_security or 0),
        "housing_fund": float(e.housing_fund or 0),
        "status": e.status,
        "id_number": e.id_number or "", "phone": e.phone or "",
        "bank_name": e.bank_name or "", "bank_branch": e.bank_branch or "", "bank_account": e.bank_account or "",
        "certificates": e.certificates or "",
        "hire_date": e.hire_date.strftime("%Y-%m-%d") if e.hire_date else None,
        "remark": e.remark or "",
    } for e in rows])


@router.post("")
def create_employee(body: EmployeeIn,
                    user: User = Depends(require_role("FINANCE", "ADMIN", "HR")),
                    db: Session = Depends(get_db)):
    if not body.name.strip():
        raise HTTPException(400, "请输入姓名")
    e = Employee(
        name=body.name.strip(), gender=body.gender, position=body.position, department=body.department,
        base_salary=body.base_salary, social_security=body.social_security,
        housing_fund=body.housing_fund, id_number=body.id_number, phone=body.phone,
        bank_name=body.bank_name, bank_branch=body.bank_branch, bank_account=body.bank_account,
        certificates=body.certificates, remark=body.remark,
    )
    if body.hire_date:
        e.hire_date = datetime.strptime(body.hire_date, "%Y-%m-%d")
    db.add(e)
    log_audit(db, user, "create", "employee", None, after=body.model_dump())
    db.commit()
    return Resp.ok({"id": e.id})


@router.put("/{eid}")
def update_employee(eid: int, body: EmployeeIn,
                    user: User = Depends(require_role("FINANCE", "ADMIN", "HR")),
                    db: Session = Depends(get_db)):
    e = db.query(Employee).get(eid)
    if not e:
        raise HTTPException(404, "员工不存在")
    for k, v in body.model_dump().items():
        if k == "hire_date":
            setattr(e, k, datetime.strptime(v, "%Y-%m-%d") if v else None)
        else:
            setattr(e, k, v)
    log_audit(db, user, "update", "employee", eid, after=body.model_dump())
    db.commit()
    return Resp.ok()


@router.delete("/{eid}")
def delete_employee(eid: int,
                    user: User = Depends(require_role("FINANCE", "ADMIN")),
                    db: Session = Depends(get_db)):
    e = db.query(Employee).get(eid)
    if not e:
        raise HTTPException(404, "员工不存在")
    e.status = "RESIGNED"
    log_audit(db, user, "state_change", "employee", eid, before="ACTIVE", after="RESIGNED")
    db.commit()
    return Resp.ok()
