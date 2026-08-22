from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, Numeric, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.db import Base


class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True)
    code = Column(String(32), unique=True, index=True)
    name = Column(String(128), nullable=False)
    contact = Column(String(64))
    phone = Column(String(32))
    bank_name = Column(String(128))
    bank_account = Column(String(64))
    status = Column(String(16), default="ACTIVE")
    remark = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class PurchaseRequest(Base):
    """采购申请"""
    __tablename__ = "purchase_requests"
    id = Column(Integer, primary_key=True)
    req_no = Column(String(32), unique=True, index=True)
    requester_user_id = Column(Integer)
    items = Column(JSON)  # [{item_id,name,spec,qty,unit,est_price,est_amount}]
    total_amount = Column(Numeric(14, 2), default=0)
    status = Column(String(16), default="DRAFT")  # DRAFT/SUBMITTED/APPROVED/REJECTED/CONVERTED
    approval_instance_id = Column(Integer)
    reason = Column(Text)
    extra = Column(JSON)  # 画布动态表单全字段(reason/supplier/expected_date/total_amount/remark等),零硬编码
    created_at = Column(DateTime, default=datetime.utcnow)


class Purchase(Base):
    """采购单 PO"""
    __tablename__ = "purchases"
    id = Column(Integer, primary_key=True)
    po_no = Column(String(32), unique=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    request_id = Column(Integer)
    status = Column(String(16), default="DRAFT")  # DRAFT/ORDERED/RECEIVED/CLOSED
    total_amount = Column(Numeric(14, 2), default=0)
    finance_doc_id = Column(Integer)  # 自动生成应付
    ordered_at = Column(DateTime)
    received_at = Column(DateTime)
    remark = Column(Text)
    extra = Column(JSON)  # 画布动态表单全字段(零硬编码)
    created_at = Column(DateTime, default=datetime.utcnow)

    supplier = relationship("Supplier")
    items = relationship("PurchaseItem", back_populates="purchase", cascade="all, delete-orphan")


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    id = Column(Integer, primary_key=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("inventory_items.id"))
    item_name = Column(String(128))
    spec = Column(String(255))
    qty = Column(Numeric(14, 3))
    unit = Column(String(16))
    unit_price = Column(Numeric(14, 2))
    amount = Column(Numeric(14, 2))
    received_qty = Column(Numeric(14, 3), default=0)

    purchase = relationship("Purchase", back_populates="items")
