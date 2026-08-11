from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, Boolean, Numeric, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.db import Base


class ReportTemplate(Base):
    __tablename__ = "report_templates"
    id = Column(Integer, primary_key=True)
    code = Column(String(64), unique=True)
    name = Column(String(128))
    type = Column(String(16))  # PRESET/CUSTOM
    data_source = Column(Text)  # SQL 或 agent_api endpoint
    config = Column(JSON)
    status = Column(String(16), default="ACTIVE")
    created_at = Column(DateTime, default=datetime.utcnow)


class AlertRule(Base):
    """预警规则 - 可配置阈值"""
    __tablename__ = "alert_rules"
    id = Column(Integer, primary_key=True)
    code = Column(String(64), unique=True)
    name = Column(String(128))
    metric = Column(String(64))  # GROSS_MARGIN/RECEIVABLE_AGING/STOCK_LOW/UTILIZATION_LOW
    condition = Column(JSON)  # {op:"<", value:0.15}
    channels = Column(JSON)  # ["FEISHU","EMAIL"]
    recipients = Column(JSON)  # [role_code/user_id]
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AlertLog(Base):
    __tablename__ = "alert_logs"
    id = Column(Integer, primary_key=True)
    rule_id = Column(Integer)
    rule_code = Column(String(64))
    metric_value = Column(String(64))
    message = Column(Text)
    sent_status = Column(String(16), default="PENDING")
    sent_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class KpiSnapshot(Base):
    """KPI快照 - 每日跑,供分析和Agent查询"""
    __tablename__ = "kpi_snapshots"
    id = Column(Integer, primary_key=True)
    snapshot_date = Column(String(16), index=True)  # 2026-07-24
    metrics = Column(JSON)  # {revenue,cost,gross_margin,receivables,payables,inventory_value...}
    created_at = Column(DateTime, default=datetime.utcnow)


class PaymentSchedule(Base):
    """回款节点 - 合同/订单分期回款,用于回款预期预警"""
    __tablename__ = "payment_schedules"
    id = Column(Integer, primary_key=True)
    schedule_no = Column(String(32), unique=True, index=True)  # PS-20260724-0001
    contract_id = Column(Integer, ForeignKey("contracts.id"))  # 关联合同(可空)
    order_id = Column(Integer, ForeignKey("orders.id"))  # 关联订单(可空)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    due_date = Column(DateTime, nullable=False)  # 应收日期
    expected_amount = Column(Numeric(14, 2), default=0)  # 应收金额
    actual_amount = Column(Numeric(14, 2), default=0)  # 实收金额
    status = Column(String(16), default="UPCOMING")  # UPCOMING/DUE/OVERDUE/PAID
    stage = Column(String(64))  # 阶段描述:预付款/进度款/尾款/质保金
    reminded_at = Column(DateTime)  # 已提醒时间
    remark = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    contract = relationship("Contract", back_populates="payment_schedules")
