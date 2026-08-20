from sqlalchemy import Column, Integer, String, DateTime, Numeric, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.db import Base


class Voucher(Base):
    """标准凭证头 (Voucher Header)"""
    __tablename__ = "vouchers"
    __table_args__ = (
        UniqueConstraint("period", "voucher_no", name="uq_voucher_period_no"),
    )
    id = Column(Integer, primary_key=True)
    period = Column(String(16), nullable=False, index=True)  # 会计期间: 2026-08
    voucher_no = Column(String(32), nullable=False, index=True)  # 凭证号: 记-0001
    voucher_date = Column(DateTime, nullable=False, index=True)
    summary = Column(Text)
    attachment_count = Column(Integer, default=0)
    status = Column(String(16), default="DRAFT")  # DRAFT/POSTED/REVERSED
    is_adjusting = Column(Integer, default=0)  # 是否调整分录
    reverse_of_id = Column(Integer)  # 红冲凭证ID
    created_by = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    posted_at = Column(DateTime)
    
    entries = relationship("VoucherEntry", back_populates="voucher", cascade="all, delete-orphan")


class VoucherEntry(Base):
    """凭证分录行 (Voucher Entry)"""
    __tablename__ = "voucher_entries"
    id = Column(Integer, primary_key=True)
    voucher_id = Column(Integer, ForeignKey("vouchers.id"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("accounts_chart.id"), nullable=False, index=True)
    account_code = Column(String(32), nullable=False)
    account_name = Column(String(128))
    summary = Column(String(255))
    debit = Column(Numeric(14, 2), default=0)
    credit = Column(Numeric(14, 2), default=0)
    
    # 辅助核算
    aux_type = Column(String(32))  # CUSTOMER/SUPPLIER/EMPLOYEE/DEPARTMENT/PROJECT
    aux_id = Column(Integer)
    aux_name = Column(String(128))
    
    voucher = relationship("Voucher", back_populates="entries")


class AccountingPeriod(Base):
    """会计期间 (Accounting Period)"""
    __tablename__ = "accounting_periods"
    id = Column(Integer, primary_key=True)
    period = Column(String(16), unique=True, nullable=False, index=True)  # 2026-08
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    status = Column(String(16), default="OPEN")  # OPEN/CLOSED
    closed_at = Column(DateTime)
    closed_by = Column(Integer)


class AccountBalance(Base):
    """科目余额表 (Account Balance) - 性能优化关键"""
    __tablename__ = "account_balances"
    id = Column(Integer, primary_key=True)
    period = Column(String(16), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("accounts_chart.id"), nullable=False, index=True)
    
    opening_debit = Column(Numeric(14, 2), default=0)  # 期初借方
    opening_credit = Column(Numeric(14, 2), default=0)  # 期初贷方
    
    debit_amount = Column(Numeric(14, 2), default=0)    # 本期借方发生额
    credit_amount = Column(Numeric(14, 2), default=0)  # 本期贷方发生额
    
    closing_debit = Column(Numeric(14, 2), default=0)  # 期末借方
    closing_credit = Column(Numeric(14, 2), default=0)  # 期末贷方
    
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
