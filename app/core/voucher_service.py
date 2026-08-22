from datetime import datetime
from typing import List, Optional
import logging
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.voucher import Voucher, VoucherEntry, AccountBalance, AccountingPeriod
from app.models.finance import Account, FinanceDoc
from app.core.db import SessionLocal

logger = logging.getLogger(__name__)


# 会计科目映射表 - 业务单据类型到会计科目的映射
ACCOUNT_MAP = {
    'RECEIPT': {  # 收款单
        'bank_account': '1002',  # 银行存款 (借方)
        'account_receivable': '1122',  # 应收账款 (贷方)
    },
    'PAYMENT': {  # 付款单
        'bank_account': '1002',  # 银行存款 (贷方)
        'accounts_payable': '2202',  # 应付账款 (借方)
    },
    'PAYROLL': {  # 工资
        'bank_account': '1002',  # 银行存款 (贷方)
        'payable_salary': '2207',  # 应付职工薪酬 (借方)
    },
}


def auto_create_voucher_from_doc(db: Session, finance_doc_id: int) -> Optional[Voucher]:
    """根据业务单据自动生成凭证"""
    doc = db.query(FinanceDoc).filter_by(id=finance_doc_id).first()
    if not doc:
        return None
    
    # 如果该单据已生成过凭证, 不重复生成
    # (实际业务中通过关联字段判断, 这里简化处理)
    
    doc_type = doc.doc_type
    account_map = ACCOUNT_MAP.get(doc_type)
    
    if not account_map:
        # 应收/应付单据本身不直接生成凭证, 由收付款动作触发
        return None
    
    amount = float(doc.amount or 0)
    if amount <= 0:
        return None
    
    # 获取对应科目
    if doc_type == 'RECEIPT':
        debit_account_code = account_map['bank_account']
        credit_account_code = account_map['account_receivable']
    elif doc_type == 'PAYMENT':
        debit_account_code = account_map['accounts_payable']
        credit_account_code = account_map['bank_account']
    elif doc_type == 'PAYROLL':
        debit_account_code = account_map['payable_salary']
        credit_account_code = account_map['bank_account']
    else:
        return None
    
    debit_account = db.query(Account).filter_by(code=debit_account_code).first()
    credit_account = db.query(Account).filter_by(code=credit_account_code).first()
    
    if not debit_account or not credit_account:
        # 如果默认科目不存在, 不生成自动凭证, 交由人工处理
        logger.warning(f"自动制单失败: 科目不存在 ({debit_account_code}/{credit_account_code})")
        return None
    
    # 构建凭证数据
    entries = [
        {
            'account_id': debit_account.id,
            'summary': f"自动生成-{doc.doc_no}",
            'debit': round(amount, 2),
            'credit': 0,
            'aux_type': doc.counterparty_type,
            'aux_id': doc.counterparty_id,
            'aux_name': doc.counterparty_name
        },
        {
            'account_id': credit_account.id,
            'summary': f"自动生成-{doc.doc_no}",
            'debit': 0,
            'credit': round(amount, 2),
            'aux_type': doc.counterparty_type,
            'aux_id': doc.counterparty_id,
            'aux_name': doc.counterparty_name
        }
    ]
    
    voucher_data = {
        'period': doc.account_date.strftime('%Y-%m') if doc.account_date else datetime.utcnow().strftime('%Y-%m'),
        'voucher_date': doc.account_date or datetime.utcnow(),
        'summary': f"自动生成-{doc.doc_type}-{doc.doc_no}",
        'entries': entries
    }
    
    # 创建并过账凭证
    voucher = create_voucher(db, voucher_data)
    post_voucher(db, voucher.id)
    
    return voucher


def get_or_create_period(db: Session, period_str: str) -> AccountingPeriod:
    """获取或创建会计期间"""
    period = db.query(AccountingPeriod).filter_by(period=period_str).first()
    if not period:
        year, month = period_str.split('-')
        start_date = datetime(int(year), int(month), 1)
        # 计算月末
        if int(month) == 12:
            next_month = datetime(int(year) + 1, 1, 1)
        else:
            next_month = datetime(int(year), int(month) + 1, 1)
        end_date = datetime(next_month.year, next_month.month, 1, 23, 59, 59)
        period = AccountingPeriod(
            period=period_str,
            start_date=start_date,
            end_date=end_date,
            status='OPEN'
        )
        db.add(period)
        db.commit()
    return period


def get_next_voucher_no(db: Session, period: str) -> str:
    """获取下期凭证号"""
    prefix = "记"
    last_voucher = db.query(Voucher).filter(
        Voucher.period == period
    ).order_by(Voucher.voucher_no.desc()).first()
    
    if not last_voucher:
        return f"{prefix}-0001"
    
    # 解析当前最大编号
    current_max = int(last_voucher.voucher_no.split('-')[1])
    return f"{prefix}-{str(current_max + 1).zfill(4)}"


def validate_balance(entries: List[dict]) -> tuple:
    """校验借贷平衡"""
    total_debit = sum(float(e.get('debit', 0)) for e in entries)
    total_credit = sum(float(e.get('credit', 0)) for e in entries)
    
    # 处理精度
    total_debit = round(total_debit, 2)
    total_credit = round(total_credit, 2)
    
    if abs(total_debit - total_credit) > 0.01:
        return False, f"借贷不平衡, 借方合计: {total_debit}, 贷方合计: {total_credit}"
    
    if total_debit == 0:
        return False, "凭证金额不能为0"
    
    return True, total_debit


def create_voucher(db: Session, data: dict, creator_id: Optional[int] = None) -> Voucher:
    """创建凭证 (草稿状态)"""
    entries = data.get('entries', [])
    
    # 校验借贷平衡
    is_balanced, result = validate_balance(entries)
    if not is_balanced:
        raise ValueError(result)
    
    period = data.get('period', '')
    voucher_date = data.get('voucher_date', datetime.utcnow())
    
    # 如果未指定期间, 从日期推断
    if not period:
        if isinstance(voucher_date, str):
            voucher_date = datetime.fromisoformat(voucher_date)
        period = voucher_date.strftime('%Y-%m')
    
    # 确保期间存在
    period_obj = get_or_create_period(db, period)
    # F3: 会计期间已关闭则禁止录入凭证
    if period_obj.status == 'CLOSED':
        raise ValueError(f"会计期间 {period} 已关闭, 不能录入凭证")
    
    voucher = create_voucher_with_retry(db, entries, period, voucher_date, data, creator_id)
    return voucher


def create_voucher_with_retry(db, entries, period, voucher_date, data, creator_id):
    """创建凭证, 凭证号冲突时自动重试 (并发安全)"""
    from sqlalchemy.exc import IntegrityError
    for attempt in range(5):
        try:
            voucher_no = get_next_voucher_no(db, period)
            voucher = Voucher(
                period=period,
                voucher_no=voucher_no,
                voucher_date=voucher_date,
                summary=data.get('summary', ''),
                status='DRAFT',
                created_by=creator_id,
                is_adjusting=data.get('is_adjusting', 0)
            )
            db.add(voucher)
            db.flush()  # 获取ID, 若凭证号冲突抛IntegrityError
            for entry_data in entries:
                account_id = entry_data.get('account_id')
                account = db.query(Account).filter_by(id=account_id).first()
                if not account:
                    raise ValueError(f"科目ID {account_id} 不存在")
                entry = VoucherEntry(
                    voucher_id=voucher.id,
                    account_id=account.id,
                    account_code=account.code,
                    account_name=account.name,
                    summary=entry_data.get('summary', ''),
                    debit=round(float(entry_data.get('debit', 0)), 2),
                    credit=round(float(entry_data.get('credit', 0)), 2),
                    aux_type=entry_data.get('aux_type'),
                    aux_id=entry_data.get('aux_id'),
                    aux_name=entry_data.get('aux_name')
                )
                db.add(entry)
            db.commit()
            return voucher
        except IntegrityError:
            db.rollback()  # 凭证号冲突 (并发), 重试生成新号
            if attempt >= 4:
                raise ValueError("凭证号生成冲突, 请重试")
    raise ValueError("凭证号生成冲突, 请重试")


def post_voucher(db: Session, voucher_id: int) -> Voucher:
    """审核/过账凭证 - 更新科目余额"""
    voucher = db.query(Voucher).filter_by(id=voucher_id).first()
    if not voucher:
        raise ValueError("凭证不存在")
    if voucher.status == 'POSTED':
        raise ValueError("凭证已过账, 不可重复操作")
    if voucher.status == 'REVERSED':
        raise ValueError("凭证已冲销")
    # 期间已封账则禁止过账
    period_obj = db.query(AccountingPeriod).filter_by(period=voucher.period).first()
    if period_obj and period_obj.status == 'CLOSED':
        raise ValueError(f"会计期间 {voucher.period} 已封账, 不能过账凭证")
    
    # 更新凭证状态
    voucher.status = 'POSTED'
    voucher.posted_at = datetime.utcnow()
    
    # 更新科目余额
    for entry in voucher.entries:
        balance = db.query(AccountBalance).filter_by(
            period=voucher.period,
            account_id=entry.account_id
        ).first()
        
        if not balance:
            # 创建余额记录
            balance = AccountBalance(
                period=voucher.period,
                account_id=entry.account_id,
                opening_debit=0,
                opening_credit=0,
                debit_amount=0,
                credit_amount=0,
                closing_debit=0,
                closing_credit=0
            )
            db.add(balance)
            db.flush()
        
        # 累加发生额
        balance.debit_amount += entry.debit
        balance.credit_amount += entry.credit
    
    # 更新期末余额 (简化计算, 实际应考虑期初)
    _recalculate_balance(db, voucher.period)
    
    db.commit()
    return voucher


def reverse_voucher(db: Session, voucher_id: int, reason: str = '') -> Voucher:
    """红冲凭证"""
    voucher = db.query(Voucher).filter_by(id=voucher_id).first()
    if not voucher:
        raise ValueError("凭证不存在")
    if voucher.status != 'POSTED':
        raise ValueError("只有已过账的凭证才能红冲")
    
    # 创建红字冲销凭证
    reverse_entries = []
    for entry in voucher.entries:
        reverse_entries.append({
            'account_id': entry.account_id,
            'summary': f"冲销-{entry.summary}",
            'debit': float(entry.credit),  # 借贷互换
            'credit': float(entry.debit),
            'aux_type': entry.aux_type,
            'aux_id': entry.aux_id,
            'aux_name': entry.aux_name
        })
    
    reverse_data = {
        'period': voucher.period,
        'voucher_date': datetime.utcnow(),
        'summary': f"红冲-{voucher.voucher_no} ({reason})",
        'entries': reverse_entries
    }
    
    # 创建冲销凭证
    reverse_voucher = create_voucher(db, reverse_data)
    reverse_voucher.status = 'REVERSED'
    reverse_voucher.reverse_of_id = voucher.id
    
    # 同时过账冲销凭证
    post_voucher(db, reverse_voucher.id)
    
    # 将原凭证标记为已冲销
    voucher.status = 'REVERSED'
    
    db.commit()
    return reverse_voucher


def _recalculate_balance(db: Session, period: str):
    """重算指定期间的所有科目余额"""
    # 简化: 重新计算所有科目的发生额和余额
    balances = db.query(AccountBalance).filter_by(period=period).all()
    
    for balance in balances:
        account = db.query(Account).filter_by(id=balance.account_id).first()
        if not account:
            continue
        
        # 计算本期所有分录的借贷合计
        entries = db.query(VoucherEntry).join(Voucher).filter(
            Voucher.period == period,
            Voucher.status == 'POSTED',
            VoucherEntry.account_id == balance.account_id
        ).all()
        
        total_debit = sum(float(e.debit) for e in entries)
        total_credit = sum(float(e.credit) for e in entries)
        
        balance.debit_amount = total_debit
        balance.credit_amount = total_credit
        
        # 简化期末余额计算
        direction = account.direction  # DEBIT or CREDIT
        if direction == 'DEBIT':
            balance.closing_debit = float(balance.opening_debit or 0) + total_debit - total_credit
            balance.closing_credit = 0
        else:
            balance.closing_credit = float(balance.opening_credit or 0) + total_credit - total_debit
            balance.closing_debit = 0


def get_voucher_list(db: Session, filters: dict) -> tuple:
    """获取凭证列表"""
    query = db.query(Voucher)
    
    if filters.get('period'):
        query = query.filter(Voucher.period == filters['period'])
    if filters.get('status'):
        query = query.filter(Voucher.status == filters['status'])
    if filters.get('voucher_no'):
        query = query.filter(Voucher.voucher_no.like(f"%{filters['voucher_no']}%"))
    
    total = query.count()
    page = filters.get('page', 1)
    page_size = filters.get('page_size', 20)
    
    items = query.order_by(Voucher.voucher_no.desc()).offset((page - 1) * page_size).limit(page_size).all()
    
    return items, total


def get_voucher_detail(db: Session, voucher_id: int) -> Optional[Voucher]:
    """获取凭证详情"""
    return db.query(Voucher).filter_by(id=voucher_id).first()


def review_voucher(db: Session, voucher_id: int, user_id: int) -> Voucher:
    """复核盖章(素人化: 不打断制单流程, 事后复核确认)"""
    voucher = db.query(Voucher).filter_by(id=voucher_id).first()
    if not voucher:
        raise ValueError("凭证不存在")
    if voucher.status != 'POSTED':
        raise ValueError("只有已过账的凭证才能复核")
    if voucher.reviewed_by:
        raise ValueError("该凭证已复核")
    voucher.reviewed_by = user_id
    voucher.reviewed_at = datetime.utcnow()
    db.commit()
    return voucher


def close_period(db: Session, period_str: str, user_id: int) -> dict:
    """一键月末封账: 校验→自动结转损益→锁期"""
    period = db.query(AccountingPeriod).filter_by(period=period_str).first()
    if not period:
        raise ValueError(f"期间 {period_str} 不存在")
    if period.status == 'CLOSED':
        raise ValueError(f"期间 {period_str} 已封账")

    # 1. 草稿凭证未处理则提示(素人友好: 不自动过账, 防止误生效)
    draft_cnt = db.query(Voucher).filter_by(period=period_str, status='DRAFT').count()
    if draft_cnt:
        raise ValueError(f"还有 {draft_cnt} 张草稿凭证未记账, 请先记账或删除后再封账")

    # 2. 试算平衡校验
    bals = db.query(AccountBalance).filter_by(period=period_str).all()
    tot_d = round(sum(float(b.debit_amount or 0) for b in bals), 2)
    tot_c = round(sum(float(b.credit_amount or 0) for b in bals), 2)
    if abs(tot_d - tot_c) > 0.01:
        raise ValueError(f"试算不平衡(借{tot_d} ≠ 贷{tot_c}), 请检查凭证后再封账")

    # 3. 自动结转损益(已结转过则跳过): 收入/费用类科目净额 → 4103本年利润
    already = db.query(Voucher).filter(
        Voucher.period == period_str, Voucher.summary.like('%月末自动结转损益%'),
        Voucher.status == 'POSTED').first()
    profit_voucher_no = None
    if not already:
        accounts = {a.id: a for a in db.query(Account).all()}
        rev_net, exp_net = 0.0, 0.0
        for b in bals:
            acc = accounts.get(b.account_id)
            if not acc:
                continue
            net = float(b.credit_amount or 0) - float(b.debit_amount or 0)  # 贷正借负
            if acc.type == 'REVENUE':
                rev_net += net
            elif acc.type == 'EXPENSE':
                exp_net -= net  # 费用借方净额 → 正数
        profit = round(rev_net - exp_net, 2)
        acc_4103 = db.query(Account).filter_by(code='4103').first()
        if acc_4103 and abs(profit) > 0.005:
            entries = []
            # 结转收入: 借 各收入科目 贷 本年利润
            for b in bals:
                acc = accounts.get(b.account_id)
                if acc and acc.type == 'REVENUE':
                    net = round(float(b.credit_amount or 0) - float(b.debit_amount or 0), 2)
                    if abs(net) > 0.005:
                        entries.append({'account_id': acc.id, 'summary': '结转本期收入', 'debit': net, 'credit': 0})
            # 结转费用: 借 本年利润 贷 各费用科目
            for b in bals:
                acc = accounts.get(b.account_id)
                if acc and acc.type == 'EXPENSE':
                    net = round(float(b.debit_amount or 0) - float(b.credit_amount or 0), 2)
                    if abs(net) > 0.005:
                        entries.append({'account_id': acc.id, 'summary': '结转本期费用', 'debit': 0, 'credit': net})
            pl_debit = round(rev_net, 2)
            pl_credit = round(exp_net, 2)
            if pl_debit >= pl_credit:
                entries.append({'account_id': acc_4103.id, 'summary': '结转本期损益', 'debit': 0, 'credit': round(pl_debit - pl_credit, 2)})
            else:
                entries.append({'account_id': acc_4103.id, 'summary': '结转本期损益', 'debit': round(pl_credit - pl_debit, 2), 'credit': 0})
            v = create_voucher(db, {
                'period': period_str, 'voucher_date': period.end_date or datetime.utcnow(),
                'summary': '月末自动结转损益(封账生成)', 'entries': entries,
            }, creator_id=user_id)
            post_voucher(db, v.id)
            profit_voucher_no = v.voucher_no
    # 4. 锁期
    period.status = 'CLOSED'
    period.closed_at = datetime.utcnow()
    period.closed_by = user_id
    db.commit()
    return {'period': period_str, 'profit_voucher': profit_voucher_no}


def reopen_period(db: Session, period_str: str, user_id: int) -> dict:
    """解封: 红冲结转凭证 + 期间重新打开(仅允许逐月倒序解封)"""
    period = db.query(AccountingPeriod).filter_by(period=period_str).first()
    if not period or period.status != 'CLOSED':
        raise ValueError(f"期间 {period_str} 未封账")
    # 存在更晚的已封账期间时, 必须先解封那个(保持结转链条可逆)
    later = db.query(AccountingPeriod).filter(
        AccountingPeriod.period > period_str, AccountingPeriod.status == 'CLOSED').first()
    if later:
        raise ValueError(f"请先解封更晚的期间 {later.period}")
    # 先解锁(否则红冲凭证无法写入本期), 再红冲封账时生成的结转凭证
    period.status = 'OPEN'
    period.closed_at = None
    period.closed_by = None
    db.flush()
    cv = db.query(Voucher).filter(
        Voucher.period == period_str, Voucher.summary.like('%月末自动结转损益%'),
        Voucher.status == 'POSTED').first()
    if cv:
        reverse_voucher(db, cv.id, reason='解封期间自动红冲结转')
    db.commit()
    return {'period': period_str, 'reopened': True}


# 封账宽限天数: 次月10号自动封上月(小厂报税15号前, 留足补单时间)
AUTO_CLOSE_GRACE_DAYS = 10


def auto_close_due_periods(db: Session) -> list:
    """惰性自动封账: 到期(月末+宽限期)且达标的 OPEN 期间自动封账。
    财务零感知——封不了(草稿/不平衡)的保持开放, 由前端温和提示原因。
    返回: [{'period':..., 'closed':bool, 'reason':...}]"""
    now = datetime.now()
    results = []
    opens = db.query(AccountingPeriod).filter_by(status='OPEN').all()
    for p in opens:
        if not p.end_date:
            continue
        due = p.end_date.timestamp() + AUTO_CLOSE_GRACE_DAYS * 86400
        if now.timestamp() <= due:
            continue
        try:
            r = close_period(db, p.period, user_id=None)
            results.append({'period': p.period, 'closed': True, 'profit_voucher': r.get('profit_voucher')})
        except ValueError as e:
            results.append({'period': p.period, 'closed': False, 'reason': str(e)})
    return results
