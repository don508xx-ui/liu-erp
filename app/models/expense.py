"""费用报销模块 - 员工报销申请→财务审核→总经理审批→付款"""
from sqlalchemy import Column, Integer, String, DateTime, Numeric, Text, JSON
from datetime import datetime
from app.core.db import Base


class ExpenseClaim(Base):
    """费用报销单"""
    __tablename__ = "expense_claims"
    id = Column(Integer, primary_key=True)
    claim_no = Column(String(32), unique=True, index=True)  # EC-20260801-0001
    applicant_user_id = Column(Integer, nullable=False)  # 申请人
    claim_type = Column(String(32))  # TRAVEL/MEAL/OFFICE/TRANSPORT/OTHER
    amount = Column(Numeric(14, 2), default=0)
    description = Column(Text)
    status = Column(String(16), default="DRAFT")  # DRAFT/SUBMITTED/APPROVED/REJECTED/PAID
    approval_instance_id = Column(Integer)  # 关联审批流实例
    company_id = Column(Integer)  # 开票主体
    approved_by_user_id = Column(Integer)
    approved_at = Column(DateTime)
    finance_doc_id = Column(Integer)  # 关联付款单
    items = Column(JSON)  # [{date,category,amount,remark}]
    remark = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)