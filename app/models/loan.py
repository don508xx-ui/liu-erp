from sqlalchemy import Column, Integer, String, DateTime, Numeric, Text
from datetime import datetime
from app.core.db import Base


class LoanRequest(Base):
    """借款申请 - 备用金/周转金,选定出账账户"""
    __tablename__ = "loan_requests"
    id = Column(Integer, primary_key=True)
    loan_no = Column(String(32), unique=True, index=True)  # LN-20260822-001
    applicant_user_id = Column(Integer, nullable=False)  # 申请人
    applicant_name = Column(String(64))
    department = Column(String(32))
    loan_type = Column(String(16))  # PETTY_CASH 备用金 / TURN_OVER 周转金
    amount = Column(Numeric(14, 2), nullable=False)
    fund_account_id = Column(Integer)  # 借款账户(从哪个资金账户出账)
    fund_account_name = Column(String(64))
    purpose = Column(Text)  # 借款用途
    expected_return_date = Column(DateTime)  # 预计还款日
    status = Column(String(16), default="SUBMITTED")  # SUBMITTED/APPROVED/REJECTED/PAID/CLEARED
    approval_instance_id = Column(Integer)
    paid_at = Column(DateTime)
    paid_by_user_id = Column(Integer)
    settled_at = Column(DateTime)  # 归还核销日
    finance_doc_id = Column(Integer)  # 关联资金流水/凭证
    remark = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
