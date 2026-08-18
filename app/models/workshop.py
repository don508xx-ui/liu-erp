from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, Numeric, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.db import Base


class WorkOrder(Base):
    """加工单 - 按客户订单+规格+工艺+交期"""
    __tablename__ = "work_orders"
    id = Column(Integer, primary_key=True)
    work_order_no = Column(String(32), unique=True, index=True)  # WO-20260724-001
    order_id = Column(Integer, ForeignKey("orders.id"))
    order_item_id = Column(Integer, ForeignKey("order_items.id"))
    customer_id = Column(Integer, ForeignKey("customers.id"))
    customer_name = Column(String(128))  # 客户名称(冗余,方便查询)
    product_spec = Column(String(256))  # 规格: Φ85-A*8.7 轮
    process = Column(String(64))  # 工艺: 镜面喷漆/加厚喷漆0.3MM/喷瓷
    batch_no = Column(String(64), index=True)  # 批次号(追溯)
    workshop = Column(String(32))  # A车间/B车间
    status = Column(String(16), default="CREATED")  # CREATED/RELEASED/PROCESSING/COMPLETED/CONFIRMED
    plan_qty = Column(Numeric(14, 3))
    actual_qty = Column(Numeric(14, 3))  # 完工数量
    plan_finish_date = Column(DateTime)
    delivery_date = Column(DateTime)  # 发货日期(最终交期)
    released_at = Column(DateTime)
    completed_at = Column(DateTime)
    confirmed_at = Column(DateTime)
    work_manager_user_id = Column(Integer)  # 厂长
    operator_user_id = Column(Integer)  # 运营
    # 委外(整单委外,工序委外下一步)
    outsource_supplier_id = Column(Integer)
    outsource_cost = Column(Numeric(14, 2), default=0)
    rework_count = Column(Integer, default=0)
    remark = Column(Text)
    extra = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("Order")
    completions = relationship("Completion", back_populates="work_order")


class WorkProcess(Base):
    """工序表 - 本期预留结构,不录入数据,下一步启用"""
    __tablename__ = "work_processes"
    id = Column(Integer, primary_key=True)
    work_order_id = Column(Integer, ForeignKey("work_orders.id"), nullable=False)
    seq = Column(Integer)  # 顺序
    name = Column(String(64))  # 前处理/喷涂/固化/检验
    mode = Column(String(16))  # SELF/OUTSOURCE
    outsource_supplier_id = Column(Integer)
    outsource_cost = Column(Numeric(14, 2), default=0)
    status = Column(String(16), default="PENDING")  # PENDING/PROCESSING/DONE
    labor_hours = Column(Numeric(10, 2))
    labor_cost = Column(Numeric(14, 2), default=0)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)


class Completion(Base):
    """完工单"""
    __tablename__ = "completions"
    id = Column(Integer, primary_key=True)
    completion_no = Column(String(32), unique=True, index=True)  # CP-20260724-001
    work_order_id = Column(Integer, ForeignKey("work_orders.id"), nullable=False)
    status = Column(String(16), default="DRAFT")  # DRAFT/CONFIRMED/REJECTED
    approval_instance_id = Column(Integer)  # 关联审批流实例
    finished_qty = Column(Numeric(14, 3))  # 完工数
    qualified_qty = Column(Numeric(14, 3))  # 合格数
    rework_qty = Column(Numeric(14, 3), default=0)  # 返工数
    scrap_qty = Column(Numeric(14, 3), default=0)  # 废品数
    labor_hours = Column(Numeric(10, 2))  # 总工时(厂长填)
    labor_cost = Column(Numeric(14, 2), default=0)  # 人工成本
    overhead_cost = Column(Numeric(14, 2), default=0)  # 制造费用
    total_cost = Column(Numeric(14, 2), default=0)  # 系统汇总(SUM work_order_costs)
    operator_user_id = Column(Integer)  # 厂长
    confirmed_by_user_id = Column(Integer)  # 运营助理
    confirmed_at = Column(DateTime)
    remark = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    work_order = relationship("WorkOrder", back_populates="completions")
    items = relationship("CompletionItem", back_populates="completion", cascade="all, delete-orphan")


class CompletionItem(Base):
    """完工单明细 - 涂料/粉末实际用量与利用率"""
    __tablename__ = "completion_items"
    id = Column(Integer, primary_key=True)
    completion_id = Column(Integer, ForeignKey("completions.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("inventory_items.id"))
    item_name = Column(String(128))
    theoretical_qty = Column(Numeric(14, 3))  # 理论用量(BOM)
    actual_qty = Column(Numeric(14, 3))  # 实际用量
    return_qty = Column(Numeric(14, 3), default=0)  # 退料量
    utilization_rate = Column(Numeric(6, 2))  # 利用率% theoretical/actual*100
    unit_cost = Column(Numeric(14, 4))
    cost_amount = Column(Numeric(14, 2))

    completion = relationship("Completion", back_populates="items")
