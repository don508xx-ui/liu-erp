from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from decimal import Decimal
from app.core.db import get_db
from app.core.permissions import require_role
from app.core.audit import log_audit
from app.core import voucher_service
from app.models.system import User
from app.models.finance import Employee, PayrollRun, Account
from app.schemas import Resp

router = APIRouter(prefix="/api/payroll", tags=["payroll"])

BANK_CAP = 5000  # 公账代发封顶

# 7级超额累进税率（月）
TAX_BRACKETS = [
    (3000, 0.03, 0),
    (12000, 0.10, 210),
    (25000, 0.20, 1410),
    (35000, 0.25, 2660),
    (55000, 0.30, 4410),
    (80000, 0.35, 7160),
    (float('inf'), 0.45, 15160),
]


def _calc_tax(taxable: float) -> float:
    """个税计算: 应纳税所得额 = 应发 - 5000起征点 - 社保 - 公积金"""
    if taxable <= 0:
        return 0
    for limit, rate, deduct in TAX_BRACKETS:
        if taxable <= limit:
            return round(taxable * rate - deduct, 2)
    return 0


def _get_or_create_account(db: Session, code: str, name: str, acc_type: str, direction: str) -> Account:
    acc = db.query(Account).filter(Account.code == code).first()
    if acc:
        return acc
    acc = Account(code=code, name=name, type=acc_type, direction=direction,
                  is_required=1, level=1, status="ACTIVE")
    db.add(acc)
    db.flush()
    return acc


class PayrollItemIn(BaseModel):
    employee_id: int
    name: str
    department: str = "管理"
    position: str = ""
    base_salary: float = 0
    bonus: float = 0          # 绩效奖金
    allowance: float = 0      # 补贴
    overtime: float = 0       # 加班费
    deduction: float = 0      # 请假/其他扣款
    social_security: float = 0  # 社保个人
    housing_fund: float = 0    # 公积金个人


class PayrollSaveIn(BaseModel):
    period: str
    items: List[PayrollItemIn]


@router.get("/generate")
def generate_from_roster(period: str,
                         user: User = Depends(require_role("FINANCE", "ADMIN")),
                         db: Session = Depends(get_db)):
    """从花名册生成当月工资草稿 - 自动带出所有在职员工"""
    existing = db.query(PayrollRun).filter(PayrollRun.period == period).first()
    if existing:
        raise HTTPException(400, f"{period} 已有工资单，如需重做请先删除旧单")
    emps = db.query(Employee).filter(Employee.status == "ACTIVE").all()
    items = []
    for e in emps:
        items.append({
            "employee_id": e.id,
            "name": e.name,
            "department": e.department or "管理",
            "position": e.position or "",
            "base_salary": float(e.base_salary or 0),
            "bonus": 0,
            "allowance": 0,
            "overtime": 0,
            "deduction": 0,
            "social_security": float(e.social_security or 0),
            "housing_fund": float(e.housing_fund or 0),
        })
    return Resp.ok({"period": period, "items": items})


@router.get("/copy-last")
def copy_last_period(period: str,
                     user: User = Depends(require_role("FINANCE", "ADMIN")),
                     db: Session = Depends(get_db)):
    """复制上月工资数据作为当月草稿（变动项清零）"""
    # 找上一个月
    year, month = int(period[:4]), int(period[5:7])
    if month == 1:
        prev = f"{year-1}-12"
    else:
        prev = f"{year}-{month-1:02d}"
    last = db.query(PayrollRun).filter(PayrollRun.period == prev).order_by(PayrollRun.id.desc()).first()
    if not last:
        raise HTTPException(400, f"{prev} 无工资记录，无法复制，请从花名册生成")
    existing = db.query(PayrollRun).filter(PayrollRun.period == period).first()
    if existing:
        raise HTTPException(400, f"{period} 已有工资单")
    items = []
    for it in (last.items or []):
        items.append({
            "employee_id": it.get("employee_id"),
            "name": it.get("name"),
            "department": it.get("department", "管理"),
            "position": it.get("position", ""),
            "base_salary": it.get("base_salary", 0),
            "bonus": 0,           # 绩效每月清零重填
            "allowance": it.get("allowance", 0),  # 补贴通常固定
            "overtime": 0,        # 加班每月清零
            "deduction": 0,       # 扣款每月清零
            "social_security": it.get("social_security", 0),
            "housing_fund": it.get("housing_fund", 0),
        })
    # 补入上月没有的新员工
    existing_ids = {it["employee_id"] for it in items if it.get("employee_id")}
    if existing_ids:
        new_emps = db.query(Employee).filter(
            Employee.status == "ACTIVE", ~Employee.id.in_(existing_ids)
        ).all()
    else:
        new_emps = db.query(Employee).filter(Employee.status == "ACTIVE").all()
    for e in new_emps:
        items.append({
            "employee_id": e.id, "name": e.name, "department": e.department or "管理",
            "position": e.position or "",
            "base_salary": float(e.base_salary or 0), "bonus": 0, "allowance": 0,
            "overtime": 0, "deduction": 0,
            "social_security": float(e.social_security or 0),
            "housing_fund": float(e.housing_fund or 0),
        })
    return Resp.ok({"period": period, "items": items, "copied_from": prev})


@router.post("/save")
def save_payroll(body: PayrollSaveIn,
                 user: User = Depends(require_role("FINANCE", "ADMIN")),
                 db: Session = Depends(get_db)):
    """保存工资草稿 - 前端表格编辑后提交，后端自动算个税、实发、公账/现金拆分"""
    period = body.period
    existing = db.query(PayrollRun).filter(PayrollRun.period == period).first()

    # 自动计算每个员工的个税和实发
    calc_items = []
    total_gross = 0
    total_bank = 0
    total_cash = 0
    total_tax = 0
    total_ss = 0
    total_hf = 0
    for it in body.items:
        gross = round(it.base_salary + it.bonus + it.allowance + it.overtime - it.deduction, 2)
        if gross < 0:
            gross = 0
        # 应纳税所得额 = 应发 - 5000 - 社保 - 公积金
        taxable = round(gross - 5000 - it.social_security - it.housing_fund, 2)
        tax = _calc_tax(taxable)
        net = round(gross - it.social_security - it.housing_fund - tax, 2)
        # 5000公账封顶，剩余现金
        bank_amt = round(min(net, BANK_CAP), 2)
        cash_amt = round(net - bank_amt, 2)
        total_gross += gross
        total_bank += bank_amt
        total_cash += cash_amt
        total_tax += tax
        total_ss += it.social_security
        total_hf += it.housing_fund
        calc_items.append({
            "employee_id": it.employee_id,
            "name": it.name,
            "department": it.department,
            "position": it.position,
            "base_salary": it.base_salary,
            "bonus": it.bonus,
            "allowance": it.allowance,
            "overtime": it.overtime,
            "deduction": it.deduction,
            "gross": gross,
            "social_security": it.social_security,
            "housing_fund": it.housing_fund,
            "tax": tax,
            "net": net,
            "bank_amount": bank_amt,
            "cash_amount": cash_amt,
        })

    total_gross = round(total_gross, 2)
    total_bank = round(total_bank, 2)
    total_cash = round(total_cash, 2)
    total_tax = round(total_tax, 2)
    total_ss = round(total_ss, 2)
    total_hf = round(total_hf, 2)

    if existing:
        existing.items = calc_items
        existing.total_amount = total_gross
        db.flush()
        pid = existing.id
        run_no = existing.run_no
    else:
        seq = db.query(PayrollRun).count() + 1
        pr = PayrollRun(
            run_no=f"PR-{period}-{seq:02d}",
            period=period,
            total_amount=total_gross,
            status="DRAFT",
            items=calc_items,
        )
        db.add(pr)
        db.flush()
        pid = pr.id
        run_no = pr.run_no

    log_audit(db, user, "save", "payroll_run", pid, after={"period": period, "total": total_gross})
    db.commit()
    return Resp.ok({
        "id": pid, "run_no": run_no, "period": period,
        "summary": {
            "headcount": len(calc_items),
            "gross_total": total_gross,
            "ss_total": total_ss,
            "hf_total": total_hf,
            "tax_total": total_tax,
            "net_total": round(total_bank + total_cash, 2),
            "bank_total": total_bank,
            "cash_total": total_cash,
        },
        "items": calc_items,
    })


@router.get("")
def list_payroll(user: User = Depends(require_role("FINANCE", "ADMIN", "GM")),
                 db: Session = Depends(get_db)):
    rows = db.query(PayrollRun).order_by(PayrollRun.id.desc()).all()
    return Resp.ok([{
        "id": r.id, "run_no": r.run_no, "period": r.period,
        "total_amount": float(r.total_amount or 0), "status": r.status,
        "item_count": len(r.items or []),
        "voucher_id": r.voucher_id,
        "pay_voucher_id": r.pay_voucher_id,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else None,
    } for r in rows])


@router.get("/{pid}")
def get_payroll(pid: int,
                user: User = Depends(require_role("FINANCE", "ADMIN", "GM")),
                db: Session = Depends(get_db)):
    pr = db.query(PayrollRun).get(pid)
    if not pr:
        raise HTTPException(404, "工资单不存在")
    items = pr.items or []
    return Resp.ok({
        "id": pr.id, "run_no": pr.run_no, "period": pr.period,
        "total_amount": float(pr.total_amount or 0), "status": pr.status,
        "voucher_id": pr.voucher_id,
        "pay_voucher_id": pr.pay_voucher_id,
        "items": items,
        "summary": {
            "headcount": len(items),
            "gross_total": round(sum(it.get("gross", 0) for it in items), 2),
            "ss_total": round(sum(it.get("social_security", 0) for it in items), 2),
            "hf_total": round(sum(it.get("housing_fund", 0) for it in items), 2),
            "tax_total": round(sum(it.get("tax", 0) for it in items), 2),
            "net_total": round(sum(it.get("net", 0) for it in items), 2),
            "bank_total": round(sum(it.get("bank_amount", 0) for it in items), 2),
            "cash_total": round(sum(it.get("cash_amount", 0) for it in items), 2),
        },
    })


@router.delete("/{pid}")
def delete_payroll(pid: int,
                   user: User = Depends(require_role("FINANCE", "ADMIN")),
                   db: Session = Depends(get_db)):
    pr = db.query(PayrollRun).get(pid)
    if not pr:
        raise HTTPException(404, "工资单不存在")
    if pr.status != "DRAFT":
        raise HTTPException(400, "已确认的工资单不可删除")
    db.delete(pr)
    db.commit()
    return Resp.ok()


@router.post("/{pid}/confirm")
def confirm_payroll(pid: int,
                    user: User = Depends(require_role("FINANCE", "ADMIN")),
                    db: Session = Depends(get_db)):
    """确认工资单（锁定数据，准备生成凭证）"""
    pr = db.query(PayrollRun).get(pid)
    if not pr:
        raise HTTPException(404, "工资单不存在")
    if pr.status != "DRAFT":
        raise HTTPException(400, f"状态{pr.status}不可确认")
    pr.status = "CONFIRMED"
    pr.confirmed_at = datetime.utcnow()
    log_audit(db, user, "state_change", "payroll_run", pid, before="DRAFT", after="CONFIRMED")
    db.commit()
    return Resp.ok({"id": pid, "status": "CONFIRMED"})


@router.post("/{pid}/accrue")
def accrue_voucher(pid: int,
                   user: User = Depends(require_role("FINANCE", "ADMIN")),
                   db: Session = Depends(get_db)):
    """生成计提凭证: 借 各部门费用-工资 贷 应付职工薪酬"""
    pr = db.query(PayrollRun).get(pid)
    if not pr:
        raise HTTPException(404, "工资单不存在")
    if pr.status not in ("CONFIRMED", "DRAFT"):
        raise HTTPException(400, f"状态{pr.status}不可计提")
    if pr.voucher_id:
        raise HTTPException(400, "已生成计提凭证，请勿重复操作")

    items = pr.items or []
    if not items:
        raise HTTPException(400, "工资单无数据")

    # 按部门汇总
    dept_amount = {}
    for it in items:
        dept = it.get("department", "管理")
        dept_amount[dept] = dept_amount.get(dept, 0) + it.get("gross", 0)

    # 科目映射: 部门 → 费用科目
    dept_account_map = {
        "管理": ("6602", "管理费用-工资", "EXPENSE", "DEBIT"),
        "销售": ("6601", "销售费用-工资", "EXPENSE", "DEBIT"),
        "生产": ("5001", "生产成本-工资", "EXPENSE", "DEBIT"),
    }
    pay_acc = _get_or_create_account(db, "2207", "应付职工薪酬-工资", "LIABILITY", "CREDIT")

    entries = []
    for dept, amt in dept_amount.items():
        code, name, atype, adir = dept_account_map.get(dept, dept_account_map["管理"])
        acc = _get_or_create_account(db, code, name, atype, adir)
        entries.append({
            "account_id": acc.id, "summary": f"{pr.period} 工资计提",
            "debit": round(amt, 2), "credit": 0,
        })
    total_gross = round(sum(dept_amount.values()), 2)
    entries.append({
        "account_id": pay_acc.id, "summary": f"{pr.period} 工资计提",
        "debit": 0, "credit": total_gross,
    })

    accrual_date = datetime(int(pr.period[:4]), int(pr.period[5:7]), 28)
    v = voucher_service.create_voucher(db, {
        "period": pr.period, "voucher_date": accrual_date,
        "summary": f"{pr.period} 工资计提({len(items)}人, ¥{total_gross:,.2f})",
        "entries": entries,
    }, creator_id=user.id)
    voucher_service.post_voucher(db, v.id)

    pr.voucher_id = v.id
    if pr.status == "DRAFT":
        pr.status = "CONFIRMED"
        pr.confirmed_at = datetime.utcnow()
    db.commit()
    return Resp.ok({"voucher_id": v.id, "voucher_no": v.voucher_no, "amount": total_gross})


@router.post("/{pid}/pay")
def pay_voucher(pid: int,
                user: User = Depends(require_role("FINANCE", "ADMIN")),
                db: Session = Depends(get_db)):
    """生成发放凭证: 借 应付职工薪酬 贷 银行存款(公账部分) + 库存现金(现金部分) + 应交税费-个税 + 其他应付款-社保/公积金"""
    pr = db.query(PayrollRun).get(pid)
    if not pr:
        raise HTTPException(404, "工资单不存在")
    if not pr.voucher_id:
        raise HTTPException(400, "请先生成计提凭证")
    if pr.pay_voucher_id:
        raise HTTPException(400, "已生成发放凭证，请勿重复操作")

    items = pr.items or []
    total_gross = round(sum(it.get("gross", 0) for it in items), 2)
    total_bank = round(sum(it.get("bank_amount", 0) for it in items), 2)
    total_cash = round(sum(it.get("cash_amount", 0) for it in items), 2)
    total_tax = round(sum(it.get("tax", 0) for it in items), 2)
    total_ss = round(sum(it.get("social_security", 0) for it in items), 2)
    total_hf = round(sum(it.get("housing_fund", 0) for it in items), 2)

    # 所需科目
    pay_acc = _get_or_create_account(db, "2207", "应付职工薪酬-工资", "LIABILITY", "CREDIT")
    bank_acc = _get_or_create_account(db, "1002", "银行存款", "ASSET", "DEBIT")
    cash_acc = _get_or_create_account(db, "1001", "库存现金", "ASSET", "DEBIT")
    tax_acc = _get_or_create_account(db, "222102", "应交税费-个人所得税", "LIABILITY", "CREDIT")
    ss_acc = _get_or_create_account(db, "224101", "其他应付款-社保个人", "LIABILITY", "CREDIT")
    hf_acc = _get_or_create_account(db, "224102", "其他应付款-公积金个人", "LIABILITY", "CREDIT")

    # 确保父科目2241存在
    _get_or_create_account(db, "2241", "其他应付款", "LIABILITY", "CREDIT")

    entries = [
        {"account_id": pay_acc.id, "summary": f"{pr.period} 工资发放", "debit": total_gross, "credit": 0},
    ]
    if total_bank > 0:
        entries.append({"account_id": bank_acc.id, "summary": f"{pr.period} 公账代发", "debit": 0, "credit": total_bank})
    if total_cash > 0:
        entries.append({"account_id": cash_acc.id, "summary": f"{pr.period} 现金发放", "debit": 0, "credit": total_cash})
    if total_tax > 0:
        entries.append({"account_id": tax_acc.id, "summary": f"{pr.period} 代扣个税", "debit": 0, "credit": total_tax})
    if total_ss > 0:
        entries.append({"account_id": ss_acc.id, "summary": f"{pr.period} 代扣社保", "debit": 0, "credit": total_ss})
    if total_hf > 0:
        entries.append({"account_id": hf_acc.id, "summary": f"{pr.period} 代扣公积金", "debit": 0, "credit": total_hf})

    pay_date = datetime(int(pr.period[:4]), int(pr.period[5:7]), 15)  # 默认15号发
    # 如果是当月可能还没到，用今天（简化）
    now = datetime.now()
    if now.year == int(pr.period[:4]) and now.month == int(pr.period[5:7]):
        pay_date = now

    v = voucher_service.create_voucher(db, {
        "period": pr.period, "voucher_date": pay_date,
        "summary": f"{pr.period} 工资发放(银行{total_bank:,.2f}+现金{total_cash:,.2f})",
        "entries": entries,
    }, creator_id=user.id)
    voucher_service.post_voucher(db, v.id)

    pr.pay_voucher_id = v.id
    pr.status = "PAID"
    pr.paid_at = datetime.utcnow()
    db.commit()
    return Resp.ok({
        "voucher_id": v.id, "voucher_no": v.voucher_no,
        "bank_amount": total_bank, "cash_amount": total_cash,
        "tax": total_tax, "social_security": total_ss, "housing_fund": total_hf,
    })
