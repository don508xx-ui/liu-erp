from sqlalchemy import Column, Integer, String, DateTime, Numeric, Text, ForeignKey
from datetime import datetime
from app.core.db import Base


class Prepayment(Base):
    """采购预付 - 关联采购单,选定出账账户"""
    __tablename__ = "prepayments"
    id = Column(Integer, primary_key=True)
    prepay_no = Column(String(32), unique=True, index=True)  # PP-20260822-001
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=False)
    purchase_no = Column(String(32))
    supplier_id = Column(Integer)
    supplier_name = Column(String(128))
    amount = Column(Numeric(14, 2), nullable=False)
    fund_account_id = Column(Integer, nullable=False)
    fund_account_name = Column(String(64))
    pay_date = Column(DateTime)
    status = Column(String(16), default="PAID")  # PAID/APPLIED/CANCELLED
    applied_amount = Column(Numeric(14, 2), default=0)  # 已冲抵金额
    finance_doc_id = Column(Integer)  # 关联应付单
    voucher_no = Column(String(32))
    paid_by_user_id = Column(Integer)
    remark = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
