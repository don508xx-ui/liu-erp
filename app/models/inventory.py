from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, Numeric, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.db import Base


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    id = Column(Integer, primary_key=True)
    code = Column(String(32), unique=True, index=True)  # MAT-001
    name = Column(String(128), nullable=False)
    spec = Column(String(255))
    unit = Column(String(16))  # kg/m²/桶/件
    category = Column(String(32))  # RAW_MATERIAL/PAINT_POWDER/CONSUMABLE/FINISHED_GOOD
    stock_qty = Column(Numeric(14, 3), default=0)
    safety_qty = Column(Numeric(14, 3), default=0)
    unit_cost = Column(Numeric(14, 4), default=0)
    location = Column(String(64))  # 库位
    status = Column(String(16), default="ACTIVE")
    extra = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)


class InventoryTxn(Base):
    """库存流水 - 所有出入库/退库/调整统一在此"""
    __tablename__ = "inventory_txns"
    id = Column(Integer, primary_key=True)
    txn_no = Column(String(32), unique=True, index=True)  # TXN-...
    txn_type = Column(String(16), nullable=False)  # IN/OUT/RETURN/ADJUST
    item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False)
    quantity = Column(Numeric(14, 3))  # 正数
    unit_cost = Column(Numeric(14, 4))
    amount = Column(Numeric(14, 2))
    work_order_id = Column(Integer)  # 领料/退料强制挂工单
    order_id = Column(Integer)  # 成品入出库挂订单
    ref_doc_type = Column(String(32))  # REQUISITION/COMPLETION/PURCHASE/SHIPMENT/MANUAL
    ref_doc_id = Column(Integer)
    warehouse = Column(String(32))
    operator_user_id = Column(Integer)
    occurred_at = Column(DateTime, default=datetime.utcnow)
    remark = Column(Text)

    item = relationship("InventoryItem")


class CustomerConsignLog(Base):
    """客供料台账 - 不进库存账,只记收发耗用"""
    __tablename__ = "customer_consign_log"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    work_order_id = Column(Integer, ForeignKey("work_orders.id"))
    customer_id = Column(Integer, ForeignKey("customers.id"))
    part_name = Column(String(128))
    part_spec = Column(String(255))
    received_qty = Column(Numeric(14, 3))  # 收料数
    consumed_qty = Column(Numeric(14, 3), default=0)  # 消耗数
    returned_qty = Column(Numeric(14, 3), default=0)  # 退回客户数
    received_at = Column(DateTime)
    returned_at = Column(DateTime)
    status = Column(String(16), default="RECEIVED")  # RECEIVED/CONSUMED/RETURNED
    remark = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class MaterialRequisition(Base):
    """领料单 - 系统按BOM自动生成"""
    __tablename__ = "material_requisitions"
    id = Column(Integer, primary_key=True)
    req_no = Column(String(32), unique=True, index=True)  # REQ-...
    work_order_id = Column(Integer, ForeignKey("work_orders.id"), nullable=False)
    status = Column(String(16), default="PENDING")  # PENDING/CONFIRMED/REJECTED
    items = Column(JSON)  # [{item_id, item_name, qty, unit, theoretical_qty}]
    warehouse_keeper_user_id = Column(Integer)
    confirmed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    remark = Column(Text)
