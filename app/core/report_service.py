from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from app.models.finance import Account
from app.models.voucher import AccountBalance, Voucher, VoucherEntry


def generate_profit_statement(db: Session, period: str) -> dict:
    """生成利润表 (Income Statement)"""
    
    # 获取所有损益类科目 (REVENUE, EXPENSE)
    revenue_accounts = db.query(Account).filter(Account.type == 'REVENUE', Account.status == 'ACTIVE').all()
    expense_accounts = db.query(Account).filter(Account.type == 'EXPENSE', Account.status == 'ACTIVE').all()
    
    # 计算收入
    total_revenue = 0
    revenue_details = []
    for acc in revenue_accounts:
        balance = db.query(AccountBalance).filter_by(
            period=period, account_id=acc.id
        ).first()
        if balance:
            # 收入类科目通常贷方表示增加
            amount = float(balance.credit_amount or 0) - float(balance.debit_amount or 0)
            if amount > 0:  # 只显示有贷方发生额的
                revenue_details.append({
                    'account_code': acc.code,
                    'account_name': acc.name,
                    'amount': round(amount, 2)
                })
                total_revenue += amount
    
    # 计算成本和费用
    total_expense = 0
    expense_details = []
    
    # 定义利润表常用的费用分类
    expense_categories = {
        'COGS': {'label': '主营业务成本', 'keywords': ['成本', 'COGS', 'cost']},
        'SELLING': {'label': '销售费用', 'keywords': ['销售', '营销', 'market']},
        'ADMIN': {'label': '管理费用', 'keywords': ['管理', '办公', 'admin']},
        'FINANCE': {'label': '财务费用', 'keywords': ['财务费用', '利息', 'finance']},
        'OTHER': {'label': '其他费用', 'keywords': []},
    }
    
    for acc in expense_accounts:
        balance = db.query(AccountBalance).filter_by(
            period=period, account_id=acc.id
        ).first()
        if balance:
            # 费用类科目通常借方表示增加
            amount = float(balance.debit_amount or 0) - float(balance.credit_amount or 0)
            if amount > 0:
                # 分类
                category = 'OTHER'
                name_lower = acc.name.lower()
                for cat, info in expense_categories.items():
                    if cat == 'OTHER':
                        continue
                    if any(kw.lower() in name_lower for kw in info['keywords']):
                        category = cat
                        break
                
                expense_details.append({
                    'account_code': acc.code,
                    'account_name': acc.name,
                    'category': category,
                    'amount': round(amount, 2)
                })
                total_expense += amount
    
    # 分类汇总
    category_totals = {}
    for item in expense_details:
        cat = item['category']
        category_totals[cat] = category_totals.get(cat, 0) + item['amount']
    
    total_cogs = category_totals.get('COGS', 0)
    total_selling = category_totals.get('SELLING', 0)
    total_admin = category_totals.get('ADMIN', 0)
    total_finance = category_totals.get('FINANCE', 0)
    total_other = category_totals.get('OTHER', 0)
    
    gross_profit = total_revenue - total_cogs
    operating_profit = gross_profit - total_selling - total_admin - total_finance - total_other
    net_profit = operating_profit  # 简化处理, 不考虑税费
    
    return {
        'period': period,
        'total_revenue': round(total_revenue, 2),
        'total_cogs': round(total_cogs, 2),
        'gross_profit': round(gross_profit, 2),
        'total_selling_expense': round(total_selling, 2),
        'total_admin_expense': round(total_admin, 2),
        'total_finance_expense': round(total_finance, 2),
        'total_other_expense': round(total_other, 0),
        'operating_profit': round(operating_profit, 2),
        'net_profit': round(net_profit, 2),
        'revenue_details': revenue_details,
        'expense_details': expense_details,
        'category_totals': {k: round(v, 2) for k, v in category_totals.items()}
    }


def generate_trial_balance(db: Session, period: str) -> dict:
    """生成试算平衡表 (Trial Balance)"""
    
    # 获取所有活跃科目
    accounts = db.query(Account).filter_by(status='ACTIVE').order_by(Account.code).all()
    
    balances = []
    total_opening_debit = 0
    total_opening_credit = 0
    total_debit = 0
    total_credit = 0
    total_closing_debit = 0
    total_closing_credit = 0
    
    for acc in accounts:
        balance = db.query(AccountBalance).filter_by(
            period=period, account_id=acc.id
        ).first()
        
        if balance:
            op_debit = float(balance.opening_debit or 0)
            op_credit = float(balance.opening_credit or 0)
            debit = float(balance.debit_amount or 0)
            credit = float(balance.credit_amount or 0)
            cl_debit = float(balance.closing_debit or 0)
            cl_credit = float(balance.closing_credit or 0)
        else:
            op_debit = op_credit = debit = credit = cl_debit = cl_credit = 0
        
        if op_debit or op_credit or debit or credit or cl_debit or cl_credit:
            balances.append({
                'account_code': acc.code,
                'account_name': acc.name,
                'account_type': acc.type,
                'opening_debit': round(op_debit, 2),
                'opening_credit': round(op_credit, 2),
                'debit_amount': round(debit, 2),
                'credit_amount': round(credit, 2),
                'closing_debit': round(cl_debit, 2),
                'closing_credit': round(cl_credit, 2)
            })
            
            total_opening_debit += op_debit
            total_opening_credit += op_credit
            total_debit += debit
            total_credit += credit
            total_closing_debit += cl_debit
            total_closing_credit += cl_credit
    
    return {
        'period': period,
        'balances': balances,
        'totals': {
            'opening_debit': round(total_opening_debit, 2),
            'opening_credit': round(total_opening_credit, 2),
            'debit_amount': round(total_debit, 2),
            'credit_amount': round(total_credit, 2),
            'closing_debit': round(total_closing_debit, 2),
            'closing_credit': round(total_closing_credit, 2)
        },
        'is_balanced': (
            abs(total_opening_debit - total_opening_credit) < 0.01 and
            abs(total_debit - total_credit) < 0.01 and
            abs(total_closing_debit - total_closing_credit) < 0.01
        )
    }


def generate_balance_sheet(db: Session, period: str) -> dict:
    """生成资产负债表 (简化版)"""
    
    # 获取资产和负债/权益类科目
    asset_accounts = db.query(Account).filter(Account.type == 'ASSET', Account.status == 'ACTIVE').all()
    liability_accounts = db.query(Account).filter(Account.type == 'LIABILITY', Account.status == 'ACTIVE').all()
    equity_accounts = db.query(Account).filter(Account.type == 'EQUITY', Account.status == 'ACTIVE').all()
    
    # 计算资产
    total_assets = 0
    asset_details = []
    for acc in asset_accounts:
        balance = db.query(AccountBalance).filter_by(
            period=period, account_id=acc.id
        ).first()
        if balance:
            amount = float(balance.closing_debit or 0) - float(balance.closing_credit or 0)
            if amount != 0:
                asset_details.append({
                    'account_code': acc.code,
                    'account_name': acc.name,
                    'amount': round(amount, 2)
                })
                total_assets += amount
    
    # 计算负债
    total_liabilities = 0
    liability_details = []
    for acc in liability_accounts:
        balance = db.query(AccountBalance).filter_by(
            period=period, account_id=acc.id
        ).first()
        if balance:
            amount = float(balance.closing_credit or 0) - float(balance.closing_debit or 0)
            if amount != 0:
                liability_details.append({
                    'account_code': acc.code,
                    'account_name': acc.name,
                    'amount': round(amount, 2)
                })
                total_liabilities += amount
    
    # 计算权益
    total_equity = 0
    equity_details = []
    for acc in equity_accounts:
        balance = db.query(AccountBalance).filter_by(
            period=period, account_id=acc.id
        ).first()
        if balance:
            amount = float(balance.closing_credit or 0) - float(balance.closing_debit or 0)
            if amount != 0:
                equity_details.append({
                    'account_code': acc.code,
                    'account_name': acc.name,
                    'amount': round(amount, 2)
                })
                total_equity += amount
    
    # 净利润: 已通过期末结转计入权益科目余额(4103/4104), 若未结转则补入权益
    profit = generate_profit_statement(db, period)
    net_profit = profit['net_profit']
    has_carried = any(d['account_code'] in ('4103', '4104') for d in equity_details)
    if not has_carried:
        total_equity += net_profit

    return {
        'period': period,
        'total_assets': round(total_assets, 2),
        'total_liabilities': round(total_liabilities, 2),
        'total_equity': round(total_equity, 2),
        'net_profit': net_profit,
        'asset_details': asset_details,
        'liability_details': liability_details,
        'equity_details': equity_details,
        'is_balanced': abs(total_assets - total_liabilities - total_equity) < 0.01
    }
