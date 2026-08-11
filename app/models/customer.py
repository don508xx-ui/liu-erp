from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, ForeignKey
from datetime import datetime
from app.core.db import Base


class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    code = Column(String(32), unique=True, index=True)  # CUST-001
    name = Column(String(128), nullable=False)
    short_code = Column(String(32), index=True)  # 字母简称(销售用,防泄密)
    tax_no = Column(String(64))  # 税号
    address = Column(String(255))
    contact_name = Column(String(64))
    contact_phone = Column(String(32))
    industry = Column(String(64))  # 行业:汽配/家电/五金/化工
    settlement_cycle = Column(String(32))  # 月结30/60/90 / 款到发货
    bank_name = Column(String(128))
    bank_account = Column(String(64))
    default_company_id = Column(Integer, ForeignKey("companies.id"))  # 默认开票主体
    owner_user_id = Column(Integer, index=True)  # 客户归属销售(权限隔离)
    status = Column(String(16), default="ACTIVE")
    remark = Column(Text)
    extra = Column(JSON)  # 兜底扩展
    created_at = Column(DateTime, default=datetime.utcnow)
