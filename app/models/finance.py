from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, Numeric, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.db import Base


class Account(Base):
    """科目表 - 财务必填科目 is_required"""
    __tablename__ = "accounts_chart"
    id = Column(Integer, primary_key=True)
    code = Column(String(32), unique=True, index=True)  # 1001/1122/5001...
    name = Column(String(128))
    parent_code = Column(String(32))
    type = Column(String(16))  # ASSET/LIABILITY/REVENUE/EXPENSE/EQUITY
    direction = Column(String(8))  # DEBIT/CREDIT
    is_required = Column(Integer, default=0)  # 1=必填
    level = Column(Integer, default=1)
    status = Column(String(16), default="ACTIVE")


class FinanceDoc(Base):
    """财务单据 - 应收/应付/收款/付款/工资,由事件钩子自动生成"""
    __tablename__ = "finance_docs"
    id = Column(Integer, primary_key=True)
    doc_no = Column(String(32), unique=True, index=True)  # AR-.../AP-.../RC-.../PY-...
    doc_type = Column(String(16), nullable=False)  # RECEIVABLE/PAYABLE/RECEIPT/PAYMENT/PAYROLL
    status = Column(String(16), default="DRAFT")  # DRAFT/OPEN/SETTLED/CANCELLED
    related_type = Column(String(16))  # ORDER/WORK_ORDER/PURCHASE/PAYROLL/PETTY
    related_id = Column(Integer)
    counterparty_type = Column(String(16))  # CUSTOMER/SUPPLIER/EMPLOYEE
    counterparty_id = Column(Integer)
    counterparty_name = Column(String(128))
    amount = Column(Numeric(14, 2), default=0)
    settled_amount = Column(Numeric(14, 2), default=0)
    company_id = Column(Integer)  # 开票主体(双公司分流)
    billing_type = Column(String(16))  # SPECIAL_VAT/NORMAL/CASH
    adjusted_amount = Column(Numeric(14, 2))  # 调价后金额(实收调整追溯)
    account_date = Column(DateTime)
    due_date = Column(DateTime)
    settled_at = Column(DateTime)
    source_event = Column(String(64))  # 触发事件类型
    remark = Column(Text)
    extra = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)

    items = relationship("FinanceItem", back_populates="doc", cascade="all, delete-orphan")


class FinanceItem(Base):
    __tablename__ = "finance_items"
    id = Column(Integer, primary_key=True)
    finance_doc_id = Column(Integer, ForeignKey("finance_docs.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts_chart.id"))
    account_code = Column(String(32))
    debit = Column(Numeric(14, 2), default=0)
    credit = Column(Numeric(14, 2), default=0)
    remark = Column(String(255))

    doc = relationship("FinanceDoc", back_populates="items")


class WorkOrderCost(Base):
    """工单成本归集中心 - 联动核心,业务发生瞬间写入"""
    __tablename__ = "work_order_costs"
    id = Column(Integer, primary_key=True)
    work_order_id = Column(Integer, ForeignKey("work_orders.id"), nullable=False, index=True)
    cost_type = Column(String(16), nullable=False)  # MATERIAL/LABOR/OVERHEAD/OUTSOURCE/REWORK
    amount = Column(Numeric(14, 2), nullable=False)
    source_doc_type = Column(String(32))  # REQUISITION/COMPLETION/OUTSOURCE...
    source_doc_id = Column(Integer)
    occurred_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    remark = Column(String(255))


class PayrollRun(Base):
    """工资发放 - 期间费用"""
    __tablename__ = "payroll_runs"
    id = Column(Integer, primary_key=True)
    run_no = Column(String(32), unique=True, index=True)  # PR-202607
    period = Column(String(16))  # 2026-07
    total_amount = Column(Numeric(14, 2), default=0)
    status = Column(String(16), default="DRAFT")  # DRAFT/CONFIRMED/PAID
    finance_doc_id = Column(Integer)  # 关联付款单
    voucher_id = Column(Integer)  # 关联计提凭证
    pay_voucher_id = Column(Integer)  # 关联发放凭证
    items = Column(JSON)  # [{employee_id,name,department,base_salary,bonus,allowance,overtime,deduction,social_security,housing_fund,gross,tax,net,bank_amount,cash_amount}]
    confirmed_at = Column(DateTime)
    paid_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class Employee(Base):
    """员工花名册 - 一次建档每月复用（HR库）"""
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False)
    gender = Column(String(8), default='男')
    position = Column(String(64))
    department = Column(String(32), default='管理')  # 管理/销售/生产
    base_salary = Column(Numeric(14, 2), default=0)
    social_security = Column(Numeric(14, 2), default=0)
    housing_fund = Column(Numeric(14, 2), default=0)
    status = Column(String(16), default='ACTIVE')  # ACTIVE/RESIGNED
    id_number = Column(String(32))  # 身份证号（银行代发必需）
    phone = Column(String(20))  # 手机号
    bank_name = Column(String(64))  # 开户银行（如：工商银行）
    bank_branch = Column(String(128))  # 开户行支行（如：东莞长安支行）
    bank_account = Column(String(64))  # 银行账号
    certificates = Column(String(255))  # 持证情况
    hire_date = Column(DateTime)
    remark = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
