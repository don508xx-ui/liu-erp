from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, Numeric, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.db import Base


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    order_no = Column(String(32), unique=True, index=True)  # SO-20260724-001
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    status = Column(String(16), default="DRAFT")  # DRAFT/SUBMITTED/EFFECTIVE/PROCESSING/PENDING_DELIVERY/DELIVERED/CLOSED/CANCELLED
    total_amount = Column(Numeric(14, 2), default=0)
    prepayment_amount = Column(Numeric(14, 2), default=0)
    prepayment_ratio = Column(Numeric(5, 2), default=0)  # 预收比例 %
    signed_at = Column(DateTime)
    effective_at = Column(DateTime)
    closed_at = Column(DateTime)
    sales_user_id = Column(Integer)  # 订单owner(销售经手人)
    return_reason = Column(Text)
    return_count = Column(Integer, default=0)
    company_id = Column(Integer, ForeignKey("companies.id"))  # 开票主体(订单开始勾选)
    billing_type = Column(String(16))  # SPECIAL_VAT(专票)/NORMAL(普票)/CASH(现金) - 决定款项流向
    contract_id = Column(Integer, ForeignKey("contracts.id"))  # 关联合同
    opportunity_id = Column(Integer, ForeignKey("opportunities.id"))  # 商机转订单追溯
    delivery_status = Column(String(16), default="PENDING")  # PENDING/PENDING_DELIVERY/DELIVERED
    delivered_at = Column(DateTime)  # 发货时间
    remark = Column(Text)
    extra = Column(JSON)
    approval_instance_id = Column(Integer)  # 关联审批流实例ID
    created_at = Column(DateTime, default=datetime.utcnow)

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    customer = relationship("Customer")


class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    seq = Column(Integer, default=1)
    part_name = Column(String(128))  # 工件名称
    part_spec = Column(String(255))  # 规格
    price_type = Column(String(16))  # BY_PIECE/BY_AREA/BY_WEIGHT
    quantity = Column(Numeric(14, 3))  # 件/m²/kg
    unit = Column(String(16))  # 件/m²/kg
    unit_price = Column(Numeric(14, 2))
    amount = Column(Numeric(14, 2))
    material_mode = Column(String(16))  # CUSTOMER/SELF 客供料/自营料
    paint_spec = Column(String(255))  # 材料种类(原涂料规格)
    paint_item_id = Column(Integer, ForeignKey("inventory_items.id"))  # 精确关联物料
    craft_type = Column(String(64))  # 工艺类型: 超音速/等离子/氧乙炔火焰陶瓷棒/碳化钨防粘/碳纤维防粘
    material_thickness = Column(String(64))  # 材料厚度(自由填)
    process_requirement = Column(Text)  # 工艺要求
    extra = Column(JSON)

    order = relationship("Order", back_populates="items")
