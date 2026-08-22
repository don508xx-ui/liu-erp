from sqlalchemy import Column, Integer, String, DateTime, Numeric, Text, ForeignKey, JSON
from datetime import datetime
from app.core.db import Base


class OutsourceOrder(Base):
    """外协单 - 关联销售订单,总经理直审"""
    __tablename__ = "outsource_orders"
    id = Column(Integer, primary_key=True)
    outsource_no = Column(String(32), unique=True, index=True)  # OS-20260822-001
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)  # 必须关联销售订单
    order_no = Column(String(32))
    customer_id = Column(Integer)
    customer_name = Column(String(128))
    work_order_id = Column(Integer)  # 可选关联加工单
    work_order_no = Column(String(32))
    supplier_id = Column(Integer, nullable=False)
    supplier_name = Column(String(128))
    process_name = Column(String(128), nullable=False)  # 外协工序名
    process_spec = Column(Text)  # 工艺要求
    qty = Column(Numeric(14, 3))
    unit = Column(String(16))
    unit_price = Column(Numeric(14, 2))
    total_amount = Column(Numeric(14, 2))
    pay_method = Column(String(16))  # CASH/TELEGRAPHIC/ACCEPTANCE
    fund_account_id = Column(Integer)  # 支付账户
    fund_account_name = Column(String(64))
    expected_delivery_date = Column(DateTime)  # 交期
    status = Column(String(16), default="SUBMITTED")  # SUBMITTED/APPROVED/REJECTED/PAID
    approval_instance_id = Column(Integer)
    finance_doc_id = Column(Integer)  # 关联应付单
    voucher_no = Column(String(32))
    remark = Column(Text)
    created_by = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
