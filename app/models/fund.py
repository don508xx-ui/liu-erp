"""资金台账 - 支持财务看板(周/月/季/半年/自定义)资金收支分析"""
from sqlalchemy import Column, Integer, String, DateTime, Numeric, Text
from datetime import datetime
from app.core.db import Base


class FundAccount(Base):
    """资金账户(银行公账/承兑/现金等)"""
    __tablename__ = "fund_accounts"
    id = Column(Integer, primary_key=True)
    code = Column(String(32), unique=True, index=True)  # ACCEPTANCE/JX-SUNDRY/CA-CASH/...
    name = Column(String(64), nullable=False)  # 机械公账/加工厂公账/承兑汇票/库存现金
    company_id = Column(Integer)  # 关联开票主体
    opening_balance = Column(Numeric(14, 2), default=0)  # 系统启用时点初始余额
    account_type = Column(String(16))  # BANK/ACCEPTANCE/CASH
    enabled = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)


class FundFlow(Base):
    """资金流水 - 每笔收入/支出,资金看板的原子数据"""
    __tablename__ = "fund_flows"
    id = Column(Integer, primary_key=True)
    fund_account_id = Column(Integer, index=True, nullable=False)
    direction = Column(String(8), nullable=False)  # IN/OUT
    amount = Column(Numeric(14, 2), nullable=False, default=0)
    expense_category = Column(String(32))  # 支出类别: 工资/融资成本/气体/耗材/食堂/佣金/提成/其他
    counterparty = Column(String(128))  # 客户/供应商/员工
    occur_date = Column(DateTime, index=True)  # 记账日期(资金时间轴)
    summary = Column(Text)
    source_type = Column(String(16))  # RECEIPT/PAYMENT/EXPENSE/ACCEPTANCE_DISCOUNT...
    source_id = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)


class AcceptanceBill(Base):
    """承兑汇票台账 - 制造业命脉: 收票/背书/贴现/到期托收全生命周期"""
    __tablename__ = "acceptance_bills"
    id = Column(Integer, primary_key=True)
    bill_no = Column(String(64), unique=True, index=True, nullable=False)  # 票号
    amount = Column(Numeric(14, 2), nullable=False)  # 票面金额
    drawer = Column(String(128))  # 出票人/前手(从谁手里收的)
    receive_date = Column(DateTime)  # 收票日期
    issue_date = Column(DateTime)  # 出票日期
    due_date = Column(DateTime, index=True, nullable=False)  # 到期日
    status = Column(String(16), default="HOLDING", index=True)  # HOLDING/ENDORSED/DISCOUNTED/SETTLED
    # 背书
    endorse_to = Column(String(128))  # 背书给谁家(供应商)
    endorse_date = Column(DateTime)
    # 贴现
    discount_date = Column(DateTime)
    discount_fee = Column(Numeric(14, 2))  # 贴息
    # 到期托收
    settle_date = Column(DateTime)
    remark = Column(Text)
    created_by = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)