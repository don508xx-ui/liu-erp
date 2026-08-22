from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.core.db import get_db
from app.core.auth import get_current_user
from app.models.system import User
from app.models.voucher import Voucher, VoucherEntry, AccountBalance, AccountingPeriod
from app.schemas import Resp
from app.core import voucher_service
from app.core import report_service

router = APIRouter(prefix="/api/vouchers", tags=["凭证管理"])


class VoucherEntryCreate(BaseModel):
    account_id: int
    summary: str = ''
    debit: float = 0
    credit: float = 0
    aux_type: Optional[str] = None
    aux_id: Optional[int] = None
    aux_name: Optional[str] = None


class VoucherCreate(BaseModel):
    period: str = ''
    voucher_date: Optional[str] = None
    summary: str = ''
    is_adjusting: int = 0
    entries: List[VoucherEntryCreate]


class VoucherResponse(BaseModel):
    id: int
    period: str
    voucher_no: str
    voucher_date: datetime
    summary: str
    status: str
    total_amount: float


@router.post("", response_model=VoucherResponse)
def create_voucher(data: VoucherCreate, db=Depends(get_db), user: User = Depends(get_current_user)):
    """创建凭证"""
    try:
        voucher = voucher_service.create_voucher(db, data.dict(), user.id)
        
        # 计算金额
        total = sum(float(e.debit) for e in voucher.entries)
        
        return VoucherResponse(
            id=voucher.id,
            period=voucher.period,
            voucher_no=voucher.voucher_no,
            voucher_date=voucher.voucher_date,
            summary=voucher.summary,
            status=voucher.status,
            total_amount=round(total, 2)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
def list_vouchers(
    db=Depends(get_db),
    user: User = Depends(get_current_user),
    period: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20
):
    """获取凭证列表"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    
    filters = {
        'period': period,
        'status': status,
        'voucher_no': keyword,
        'page': page,
        'page_size': page_size
    }
    
    items, total_count = voucher_service.get_voucher_list(db, filters)
    
    result = []
    for v in items:
        vtotal = sum(float(e.debit) for e in v.entries)
        result.append({
            'id': v.id,
            'period': v.period,
            'voucher_no': v.voucher_no,
            'voucher_date': v.voucher_date.isoformat(),
            'summary': v.summary,
            'status': v.status,
            'total_amount': round(vtotal, 2),
            'entry_count': len(v.entries),
            'reviewed': bool(v.reviewed_by),
        })
    
    return Resp.ok({
        'items': result,
        'total': total_count,
        'page': page,
        'page_size': page_size
    })


# ============ 期间封账(素人化一键操作) ============
# 注意: 必须注册在 /{voucher_id} 之前, 否则 "periods" 被路径参数抢走

# ============ 期初建账(一次性操作, 藏在科目页按钮里) ============

@router.get("/opening-balance")
def get_opening_balance(period: str, db=Depends(get_db), user: User = Depends(get_current_user)):
    """查询某期间各科目期初数"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    from app.models.finance import Account
    from app.models.voucher import AccountBalance, Voucher
    accs = db.query(Account).filter(Account.status == 'ACTIVE').order_by(Account.code).all()
    bals = {b.account_id: b for b in db.query(AccountBalance).filter_by(period=period).all()}
    has_vouchers = db.query(Voucher).filter(Voucher.period == period, Voucher.status == 'POSTED').count()
    items = []
    for a in accs:
        b = bals.get(a.id)
        if b:
            opening = float(b.opening_debit or 0) if a.direction == 'DEBIT' else float(b.opening_credit or 0)
        else:
            opening = 0
        items.append({
            'account_id': a.id, 'code': a.code, 'name': a.name,
            'type': a.type, 'direction': a.direction,
            'opening': round(opening, 2),
        })
    return {'items': items, 'has_vouchers': has_vouchers > 0}


@router.post("/opening-balance")
def save_opening_balance(payload: dict, db=Depends(get_db), user: User = Depends(get_current_user)):
    """录入期初: 试算平衡校验(借合计=贷合计) → 写入余额表; 该期已过账凭证则拒绝"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    from app.models.finance import Account
    from app.models.voucher import AccountBalance, Voucher
    period = (payload.get('period') or '').strip()
    items = payload.get('items') or []
    if not period:
        raise HTTPException(status_code=400, detail="缺少建账期间")
    if db.query(Voucher).filter(Voucher.period == period, Voucher.status == 'POSTED').count():
        raise HTTPException(status_code=400, detail=f"{period} 已有过账凭证, 期初不能再改(如需调整请走凭证)")
    accs = {a.id: a for a in db.query(Account).all()}
    tot_d, tot_c = 0.0, 0.0
    rows = []
    for it in items:
        amt = round(float(it.get('opening') or 0), 2)
        if abs(amt) < 0.005:
            continue
        acc = accs.get(it.get('account_id'))
        if not acc:
            continue
        # 借方科目期初记借, 贷方科目期初记贷
        if acc.direction == 'DEBIT':
            tot_d += amt
        else:
            tot_c += amt
        rows.append((acc, amt))
    if abs(tot_d - tot_c) > 0.01:
        raise HTTPException(status_code=400,
                            detail=f"试算不平衡: 借方合计 {round(tot_d,2)} ≠ 贷方合计 {round(tot_c,2)}, 请检查后再保存")
    voucher_service.get_or_create_period(db, period)
    for acc, amt in rows:
        bal = db.query(AccountBalance).filter_by(account_id=acc.id, period=period).first()
        if not bal:
            bal = AccountBalance(account_id=acc.id, period=period)
            db.add(bal)
            db.flush()
        d = float(bal.debit_amount or 0)
        c = float(bal.credit_amount or 0)
        # 与 voucher_service 余额惯例一致: 借方科目记 opening_debit, 贷方科目记 opening_credit
        if acc.direction == 'DEBIT':
            bal.opening_debit = amt
            bal.opening_credit = 0
            bal.closing_debit = amt + d - c
            bal.closing_credit = 0
        else:
            bal.opening_credit = amt
            bal.opening_debit = 0
            bal.closing_credit = amt + c - d
            bal.closing_debit = 0
        bal.updated_at = datetime.now()
    db.commit()
    return {'message': f'{period} 期初建账完成, 共录入 {len(rows)} 个科目, 借贷平衡 {round(tot_d,2)}'}


@router.get("/periods")
def list_periods(db=Depends(get_db), user: User = Depends(get_current_user)):
    """会计期间列表(含封账状态); 访问时惰性触发自动封账, 财务零感知"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    from app.models.voucher import AccountingPeriod
    auto_results = voucher_service.auto_close_due_periods(db)
    items = db.query(AccountingPeriod).order_by(AccountingPeriod.period.desc()).all()
    return {'items': [{'period': p.period, 'status': p.status,
                       'closed_at': p.closed_at.isoformat() if p.closed_at else None} for p in items],
            'auto_closed': [r for r in auto_results if r.get('closed')],
            'pending_close': [r for r in auto_results if not r.get('closed')]}


@router.get("/{voucher_id}")
def get_voucher(voucher_id: int, db=Depends(get_db), user: User = Depends(get_current_user)):
    """获取凭证详情"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    
    voucher = voucher_service.get_voucher_detail(db, voucher_id)
    if not voucher:
        raise HTTPException(status_code=404, detail="凭证不存在")
    
    total = sum(float(e.debit) for e in voucher.entries)
    
    return {
        'id': voucher.id,
        'period': voucher.period,
        'voucher_no': voucher.voucher_no,
        'voucher_date': voucher.voucher_date.isoformat(),
        'summary': voucher.summary,
        'status': voucher.status,
        'total_amount': round(total, 2),
        'is_adjusting': voucher.is_adjusting,
        'entries': [
            {
                'id': e.id,
                'account_id': e.account_id,
                'account_code': e.account_code,
                'account_name': e.account_name,
                'summary': e.summary,
                'debit': float(e.debit),
                'credit': float(e.credit),
                'aux_type': e.aux_type,
                'aux_id': e.aux_id,
                'aux_name': e.aux_name
            }
            for e in voucher.entries
        ]
    }


@router.post("/{voucher_id}/post")
def post_voucher(voucher_id: int, db=Depends(get_db), user: User = Depends(get_current_user)):
    """审核/过账凭证"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    
    try:
        voucher = voucher_service.post_voucher(db, voucher_id)
        return {'message': f'凭证 {voucher.voucher_no} 已过账'}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{voucher_id}/reverse")
def reverse_voucher(voucher_id: int, reason: str = '', db=Depends(get_db), user: User = Depends(get_current_user)):
    """红冲凭证"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    
    try:
        reverse_v = voucher_service.reverse_voucher(db, voucher_id, reason)
        return {'message': f'已创建红冲凭证 {reverse_v.voucher_no}'}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{voucher_id}/review")
def review_voucher(voucher_id: int, db=Depends(get_db), user: User = Depends(get_current_user)):
    """复核盖章(素人化: 制单即生效, 复核=事后确认)"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    try:
        v = voucher_service.review_voucher(db, voucher_id, user.id)
        return {'message': f'凭证 {v.voucher_no} 已复核', 'reviewed': True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============ 期间封账操作 ============

@router.post("/periods/{period}/close")
def close_period(period: str, db=Depends(get_db), user: User = Depends(get_current_user)):
    """一键月末封账: 校验→自动结转损益→锁期"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    try:
        r = voucher_service.close_period(db, period, user.id)
        msg = f'{period} 已封账'
        if r.get('profit_voucher'):
            msg += f', 已自动结转损益({r["profit_voucher"]})'
        return {'message': msg, **r}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/periods/{period}/reopen")
def reopen_period(period: str, db=Depends(get_db), user: User = Depends(get_current_user)):
    """解封期间(自动红冲结转凭证)"""
    if user.role.code not in ['ADMIN', 'GM']:
        raise HTTPException(status_code=403, detail="仅管理员/总经理可解封")
    try:
        r = voucher_service.reopen_period(db, period, user.id)
        return {'message': f'{period} 已解封', **r}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============ 月结检查 + 计提凭证(0 token, 纯SQL) ============

def _get_or_create_account(db, code: str, name: str, type_: str, direction: str):
    """获取或创建科目(与acceptances._ledger逻辑一致)"""
    from app.models.finance import Account
    acc = db.query(Account).filter(Account.code == code).first()
    if not acc:
        acc = Account(code=code, name=name, type=type_, direction=direction,
                      is_required=1, level=1, status="ACTIVE")
        db.add(acc); db.flush()
    return acc


@router.get("/month-close/check")
def month_close_check(period: str, db=Depends(get_db), user: User = Depends(get_current_user)):
    """月结体检: 逐项扫描未完成事项, 0 token, 纯SQL。前端打开凭证页时惰性调用。"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    from app.models.finance import FinanceDoc
    from app.models.expense import ExpenseClaim
    from app.models.fund import AcceptanceBill
    from datetime import timedelta

    now = datetime.utcnow()
    items = []

    # 1. 未过账凭证
    drafts = db.query(Voucher).filter(Voucher.period == period, Voucher.status == 'DRAFT').all()
    if drafts:
        items.append({"key": "unposted_vouchers", "level": "danger",
                      "title": f"{len(drafts)} 张草稿凭证未过账",
                      "detail": ", ".join(d.voucher_no for d in drafts[:5]),
                      "action": "post_all", "count": len(drafts)})

    # 2. 承兑到期预警
    holding = db.query(AcceptanceBill).filter(AcceptanceBill.status == "HOLDING").all()
    due_soon = [b for b in holding if b.due_date and 0 <= (b.due_date - now).days <= 30]
    overdue = [b for b in holding if b.due_date and (b.due_date - now).days < 0]
    if due_soon or overdue:
        amt = sum(float(b.amount or 0) for b in due_soon + overdue)
        parts = []
        if overdue: parts.append(f"已逾期 {len(overdue)} 张")
        if due_soon: parts.append(f"30天内到期 {len(due_soon)} 张")
        items.append({"key": "acceptance_due", "level": "warning",
                      "title": f"承兑到期预警: {' / '.join(parts)}",
                      "detail": f"合计 ¥{amt:,.2f}", "action": None, "count": len(due_soon)+len(overdue)})

    # 3. 工资未计提
    from app.models.finance import PayrollRun
    payrolls = db.query(PayrollRun).filter(PayrollRun.period == period).all()
    un_accrued = [p for p in payrolls if p.status in ('DRAFT', 'CONFIRMED')]
    if un_accrued:
        amt = sum(float(p.total_amount or 0) for p in un_accrued)
        items.append({"key": "payroll_unaccrued", "level": "warning",
                      "title": f"{len(un_accrued)} 笔工资未计提({period})",
                      "detail": f"合计 ¥{amt:,.2f}", "action": "accrue_payroll", "count": len(un_accrued)})

    # 4. 应收逾期
    ars = db.query(FinanceDoc).filter(
        FinanceDoc.doc_type == "RECEIVABLE",
        FinanceDoc.status != "SETTLED",
        FinanceDoc.status != "CANCELLED",
    ).all()
    ar_overdue = [a for a in ars if a.due_date and a.due_date < now]
    if ar_overdue:
        amt = sum(float(a.amount or 0) - float(a.settled_amount or 0) for a in ar_overdue)
        items.append({"key": "ar_overdue", "level": "danger",
                      "title": f"{len(ar_overdue)} 笔应收已逾期",
                      "detail": f"未收金额 ¥{amt:,.2f}", "action": None, "count": len(ar_overdue)})

    # 5. 报销待审
    pending_exp = db.query(ExpenseClaim).filter(ExpenseClaim.status == "SUBMITTED").all()
    if pending_exp:
        amt = sum(float(e.amount or 0) for e in pending_exp)
        items.append({"key": "expense_pending", "level": "info",
                      "title": f"{len(pending_exp)} 笔报销待审批",
                      "detail": f"合计 ¥{amt:,.2f}", "action": None, "count": len(pending_exp)})

    # 6. 期间是否已封账
    period_obj = db.query(AccountingPeriod).filter_by(period=period).first()
    is_closed = period_obj and period_obj.status == "CLOSED"

    danger_count = sum(1 for i in items if i["level"] == "danger")
    warning_count = sum(1 for i in items if i["level"] == "warning")
    return {
        "period": period,
        "items": items,
        "is_closed": is_closed,
        "all_clear": len(items) == 0 and not is_closed,
        "badge": danger_count + warning_count,
    }


@router.post("/month-close/accrue-payroll")
def accrue_payroll(period: str, db=Depends(get_db), user: User = Depends(get_current_user)):
    """工资计提: 汇总当期已确认工资 → 自动生成计提凭证(借 管理费用-工资 贷 应付职工薪酬)"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    from app.models.finance import PayrollRun
    payrolls = db.query(PayrollRun).filter(PayrollRun.period == period).all()
    un_accrued = [p for p in payrolls if p.status in ('DRAFT', 'CONFIRMED')]
    if not un_accrued:
        raise HTTPException(400, f"{period} 无需计提的工资记录")
    total = round(sum(float(p.total_amount or 0) for p in un_accrued), 2)
    if total <= 0:
        raise HTTPException(400, "计提金额为0")
    # 生成计提凭证: 借 6601管理费用-工资 贷 2207应付职工薪酬
    exp_acc = _get_or_create_account(db, "6601", "管理费用-工资", "EXPENSE", "DEBIT")
    pay_acc = _get_or_create_account(db, "2207", "应付职工薪酬", "LIABILITY", "CREDIT")
    accrual_date = datetime(int(period[:4]), int(period[5:7]), min(28, 28))
    v = voucher_service.create_voucher(db, {
        "period": period, "voucher_date": accrual_date,
        "summary": f"{period} 工资计提({len(un_accrued)}笔, ¥{total:,.2f})",
        "entries": [
            {"account_id": exp_acc.id, "summary": f"{period}工资计提", "debit": total, "credit": 0},
            {"account_id": pay_acc.id, "summary": f"{period}工资计提", "debit": 0, "credit": total},
        ],
    }, creator_id=user.id)
    voucher_service.post_voucher(db, v.id)
    # 标记工资已计提
    for p in un_accrued:
        if p.status == 'DRAFT':
            p.status = 'CONFIRMED'
            p.confirmed_at = datetime.utcnow()
    db.commit()
    return {"voucher_no": v.voucher_no, "amount": total, "count": len(un_accrued)}


@router.post("/month-close/post-all-drafts")
def post_all_drafts(period: str, db=Depends(get_db), user: User = Depends(get_current_user)):
    """批量过账该期间所有草稿凭证"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    drafts = db.query(Voucher).filter(Voucher.period == period, Voucher.status == 'DRAFT').all()
    if not drafts:
        raise HTTPException(400, f"{period} 无草稿凭证")
    posted, failed = [], []
    for d in drafts:
        try:
            voucher_service.post_voucher(db, d.id)
            posted.append(d.voucher_no)
        except Exception as e:
            failed.append(f"{d.voucher_no}: {e}")
    db.commit()
    return {"posted": posted, "failed": failed}


# ============ 报表接口 ============

@router.get("/reports/profit")
def get_profit_statement(
    period: str,
    db=Depends(get_db),
    user: User = Depends(get_current_user)
):
    """获取利润表"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    return Resp.ok(report_service.generate_profit_statement(db, period))


@router.get("/reports/trial-balance")
def get_trial_balance(
    period: str,
    db=Depends(get_db),
    user: User = Depends(get_current_user)
):
    """获取试算平衡表"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    return Resp.ok(report_service.generate_trial_balance(db, period))


@router.get("/reports/balance-sheet")
def get_balance_sheet(
    period: str,
    db=Depends(get_db),
    user: User = Depends(get_current_user)
):
    """获取资产负债表"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    return Resp.ok(report_service.generate_balance_sheet(db, period))


# ============ 自动制单接口 ============

@router.get("/reports/account-detail")
def get_account_detail(
    period: str,
    account_code: str,
    db=Depends(get_db),
    user: User = Depends(get_current_user)
):
    """科目明细钻取：某期间某科目的凭证分录列表"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    rows = db.query(VoucherEntry).join(Voucher).filter(
        VoucherEntry.account_code == account_code,
        Voucher.period == period,
        Voucher.status == 'POSTED'
    ).order_by(Voucher.voucher_date).all()
    out = []
    for e in rows:
        v = e.voucher
        out.append({
            'voucher_id': v.id,
            'voucher_no': v.voucher_no,
            'voucher_date': v.voucher_date.strftime('%Y-%m-%d') if v.voucher_date else '',
            'summary': e.summary or v.summary or '',
            'debit': float(e.debit or 0),
            'credit': float(e.credit or 0),
            'aux_name': e.aux_name or '',
        })
    return Resp.ok(out)


@router.post("/auto-from-doc/{doc_id}")
def auto_create_voucher(
    doc_id: int,
    db=Depends(get_db),
    user: User = Depends(get_current_user)
):
    """根据业务单据自动生成凭证"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    
    try:
        voucher = voucher_service.auto_create_voucher_from_doc(db, doc_id)
        if voucher:
            return {
                'message': f'已自动生成凭证 {voucher.voucher_no}',
                'voucher_id': voucher.id
            }
        else:
            return {'message': '该单据无需自动生成凭证或生成失败'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
