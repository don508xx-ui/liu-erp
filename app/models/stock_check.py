from sqlalchemy import Column, Integer, String, DateTime, Numeric, Text, ForeignKey, JSON
from datetime import datetime
from app.core.db import Base

class StockCheck(Base):
    """月度盘点单 - 月底一次,周一总经理提醒"""
    __tablename__ = "stock_checks"
    id = Column(Integer, primary_key=True)
    check_no = Column(String(32), unique=True, index=True)  # SC-202608-001
    period = Column(String(16), nullable=False)  # 2026-08
    check_date = Column(DateTime, default=datetime.utcnow)
    status = Column(String(16), default="DRAFT")  # DRAFT/CHECKED/CLOSED
    operator_user_id = Column(Integer)
    operator_name = Column(String(64))
    items = Column(JSON)  # [{item_id, item_name, book_qty, actual_qty, diff_qty, remark}]
    total_diff_amount = Column(Numeric(14, 2), default=0)
    voucher_no = Column(String(32))
    remark = Column(Text)
    closed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
