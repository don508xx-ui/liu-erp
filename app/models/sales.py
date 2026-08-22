"""销售域扩展模型 V2 - 双公司主体/合同/商机/来货登记/送货单/调价申请"""
from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, Numeric, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.db import Base


class Company(Base):
    """开票主体 - 一般纳税人/小规模"""
    __tablename__ = "companies"
    id = Column(Integer, primary_key=True)
    code = Column(String(32), unique=True, index=True)  # GENERAL/SMALL
    name = Column(String(128), nullable=False)  # 公司全称
    short_name = Column(String(64))  # 简称
    tax_type = Column(String(16), nullable=False)  # GENERAL(一般纳税人)/SMALL(小规模)
    tax_no = Column(String(64))  # 税号
    bank_name = Column(String(128))
    bank_account = Column(String(64))
    address = Column(String(255))
    phone = Column(String(32))
    status = Column(String(16), default="ACTIVE")
    remark = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class Contract(Base):
    """合同管理"""
    __tablename__ = "contracts"
    id = Column(Integer, primary_key=True)
    contract_no = Column(String(32), unique=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    company_id = Column(Integer, ForeignKey("companies.id"))  # 开票主体
    amount = Column(Numeric(14, 2), default=0)
    signed_date = Column(DateTime)
    effective_date = Column(DateTime)
    expire_date = Column(DateTime)
    attachment_url = Column(String(255))  # 附件路径
    status = Column(String(16), default="DRAFT")  # DRAFT/EFFECTIVE/CLOSED/CANCELLED
    owner_user_id = Column(Integer)  # 责任销售
    payment_terms = Column(String(255))  # 付款条款描述
    remark = Column(Text)
    extra = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)

    customer = relationship("Customer")
    company = relationship("Company")
    payment_schedules = relationship("PaymentSchedule", back_populates="contract", cascade="all, delete-orphan")


class Opportunity(Base):
    """商机管理"""
    __tablename__ = "opportunities"
    id = Column(Integer, primary_key=True)
    oppo_no = Column(String(32), unique=True, index=True)  # OPP-20260724-0001
    customer_id = Column(Integer, ForeignKey("customers.id"))  # 转化后关联客户,转为客户前为空
    customer_name = Column(String(128))  # 客户公司名称(线索)
    contact_person = Column(String(64))
    contact_phone = Column(String(32))
    company_address = Column(String(255))
    industry = Column(String(64))  # 行业
    title = Column(String(255))  # 商机标题
    expected_amount = Column(Numeric(14, 2), default=0)
    stage = Column(String(16), default="LEAD")  # LEAD/FOLLOW/QUOTE/WON/LOST
    expected_close_date = Column(DateTime)
    delivery_date = Column(DateTime)  # 交期(客户要求交付日期)
    source = Column(String(64))  # 来源:老客户转介/展会/主动开发
    owner_user_id = Column(Integer)  # 负责销售
    won_order_id = Column(Integer)  # 赢单后转出的订单ID
    loss_reason = Column(Text)
    remark = Column(Text)
    extra = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    customer = relationship("Customer")


class SampleRequest(Base):
    """打样申请 - 客户打样申请"""
    __tablename__ = "sample_requests"
    id = Column(Integer, primary_key=True)
    log_no = Column(String(32), unique=True, index=True)  # DY-20260821-0001
    order_id = Column(Integer, ForeignKey("orders.id"))
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    # 客户信息
    contact_person = Column(String(64))
    contact_phone = Column(String(32))
    email = Column(String(128))
    company_address = Column(String(255))
    # 打样信息
    sample_reason = Column(String(64))  # 产品开发/客户需求/样品展示/设计变更/其他
    sample_reason_other = Column(String(128))
    part_name = Column(String(128))  # 工件名称
    material = Column(String(128))  # 工件材质
    size_desc = Column(String(255))  # 工件尺寸(可附图纸)
    qty = Column(Numeric(14, 3))  # 所需数量
    sample_provided_by = Column(String(16))  # 客供/外购
    expected_date = Column(DateTime)  # 期望完成时间
    # 涂层技术要求
    coating_tech_req = Column(Text)
    spray_process = Column(String(64))  # 粉末燃烧喷涂/超音速火焰喷涂/电弧喷涂/其他
    spray_process_other = Column(String(128))
    coating_material = Column(String(128))  # 涂层材料
    coating_thickness = Column(String(64))  # 涂层厚度要求
    hardness_req = Column(String(128))
    bond_strength_req = Column(String(128))
    surface_roughness_req = Column(String(128))
    other_performance_req = Column(String(255))  # 耐磨损/耐腐蚀/耐高温/绝缘/导电等
    # 图纸与技术资料
    drawings = Column(String(128))  # 产品结构图纸/技术参数说明/其他
    drawings_other = Column(String(128))
    # 费用确认
    is_charged = Column(String(8))  # 是/否
    estimated_cost = Column(Numeric(14, 2))
    cost_remark = Column(String(255))
    # 审批流程(存储审批意见)
    approval_opinions = Column(JSON)  # [{"role":"业务员","opinion":"","sign":""},...]
    # 状态
    status = Column(String(16), default="DRAFT")  # DRAFT/PENDING/APPROVED/REJECTED
    created_by_user_id = Column(Integer)
    approval_instance_id = Column(Integer)
    remark = Column(Text)
    extra = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)

    customer = relationship("Customer")


# 旧名兼容: ReceivingLog 仍用于采购收货流程(旧receiving_logs表)
class ReceivingLog(Base):
    __tablename__ = "receiving_logs"
    __table_args__ = {"extend_existing": True}
    id = Column(Integer, primary_key=True)
    log_no = Column(String(32), unique=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    received_at = Column(DateTime, default=datetime.utcnow)
    received_by_user_id = Column(Integer)
    part_name = Column(String(128))
    part_spec = Column(String(255))
    qty = Column(Numeric(14, 3))
    unit = Column(String(16))
    process_requirement = Column(String(255))
    status = Column(String(16), default="RECEIVED")
    approval_instance_id = Column(Integer)
    remark = Column(Text)
    extra = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    customer = relationship("Customer")


class DeliveryNote(Base):
    """送货单 - 三联单(白存根/红客户/黄回单)"""
    __tablename__ = "delivery_notes"
    id = Column(Integer, primary_key=True)
    delivery_no = Column(String(32), unique=True, index=True)  # DN-20260724-0001
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    company_id = Column(Integer, ForeignKey("companies.id"))  # 开票主体(从订单带)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    status = Column(String(16), default="PENDING")  # PENDING/SHIPPED
    shipped_at = Column(DateTime)
    shipped_by_user_id = Column(Integer)  # 发货确认人(销售)
    total_qty = Column(Numeric(14, 3), default=0)
    total_amount = Column(Numeric(14, 2), default=0)
    delivery_address = Column(String(255))
    remark = Column(Text)
    extra = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)

    items = relationship("DeliveryNoteItem", back_populates="note", cascade="all, delete-orphan")
    order = relationship("Order")
    company = relationship("Company")
    customer = relationship("Customer")


class DeliveryNoteItem(Base):
    """送货单明细"""
    __tablename__ = "delivery_note_items"
    id = Column(Integer, primary_key=True)
    delivery_note_id = Column(Integer, ForeignKey("delivery_notes.id"), nullable=False)
    order_item_id = Column(Integer)  # 关联订单明细
    part_name = Column(String(128))
    part_spec = Column(String(255))
    qty = Column(Numeric(14, 3))
    unit = Column(String(16))
    unit_price = Column(Numeric(14, 2))
    amount = Column(Numeric(14, 2))
    remark = Column(String(255))

    note = relationship("DeliveryNote", back_populates="items")


class SalesAdjustment(Base):
    """实收调价申请 - 销售发起,GM必审"""
    __tablename__ = "sales_adjustments"
    id = Column(Integer, primary_key=True)
    adj_no = Column(String(32), unique=True, index=True)  # ADJ-20260724-0001
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    original_amount = Column(Numeric(14, 2), nullable=False)  # 原应收
    adjusted_amount = Column(Numeric(14, 2), nullable=False)  # 调整后实收
    diff_amount = Column(Numeric(14, 2))  # 差额
    reason = Column(Text, nullable=False)  # 调价原因
    status = Column(String(16), default="PENDING")  # PENDING/APPROVED/REJECTED
    initiator_user_id = Column(Integer, nullable=False)  # 发起销售
    approval_instance_id = Column(Integer)  # 关联审批流实例
    approved_at = Column(DateTime)
    approved_by_user_id = Column(Integer)
    remark = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("Order")


class ReturnRequest(Base):
    """退货申请 - 财务发起,对应已产生销售单,审批通过冲减应收"""
    __tablename__ = "return_requests"
    id = Column(Integer, primary_key=True)
    rt_no = Column(String(32), unique=True, index=True)  # RT-20260821-0001
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    qty = Column(Numeric(14, 3), default=0)
    amount = Column(Numeric(14, 2), default=0)  # 退货金额(冲减应收)
    reason = Column(Text, nullable=False)
    status = Column(String(16), default="PENDING")  # PENDING/APPROVED/REJECTED
    initiator_user_id = Column(Integer, nullable=False)
    approval_instance_id = Column(Integer)
    approved_at = Column(DateTime)
    remark = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("Order")
    customer = relationship("Customer")


class ReworkRequest(Base):
    """返工申请 - 财务发起,对应已产生销售单,审批通过计返工成本"""
    __tablename__ = "rework_requests"
    id = Column(Integer, primary_key=True)
    rw_no = Column(String(32), unique=True, index=True)  # RW-20260821-0001
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    qty = Column(Numeric(14, 3), default=0)
    amount = Column(Numeric(14, 2), default=0)  # 返工成本
    reason = Column(Text, nullable=False)
    status = Column(String(16), default="PENDING")  # PENDING/APPROVED/REJECTED
    initiator_user_id = Column(Integer, nullable=False)
    approval_instance_id = Column(Integer)
    approved_at = Column(DateTime)
    remark = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("Order")
    customer = relationship("Customer")
