from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, ForeignKey
from datetime import datetime
from app.core.db import Base


class FlowDefinition(Base):
    """审批流定义 - 表驱动,运营后台可配"""
    __tablename__ = "flow_definitions"
    id = Column(Integer, primary_key=True)
    name = Column(String(128))
    biz_type = Column(String(32), index=True)  # PURCHASE/EXPENSE/ORDER_RETURN/PAYROLL...
    nodes = Column(JSON)  # [{seq,name,approver_role,condition}]
    status = Column(String(16), default="ACTIVE")
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)


class FlowInstance(Base):
    __tablename__ = "flow_instances"
    id = Column(Integer, primary_key=True)
    definition_id = Column(Integer, ForeignKey("flow_definitions.id"))
    biz_type = Column(String(32))
    biz_id = Column(Integer)
    status = Column(String(16), default="RUNNING")  # RUNNING/APPROVED/REJECTED/CANCELLED
    current_node_seq = Column(Integer, default=1)
    initiator_user_id = Column(Integer)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime)


class FlowTask(Base):
    __tablename__ = "flow_tasks"
    id = Column(Integer, primary_key=True)
    instance_id = Column(Integer, ForeignKey("flow_instances.id"), nullable=False)
    node_seq = Column(Integer)
    node_name = Column(String(64))
    assignee_user_id = Column(Integer)
    role_id = Column(Integer)
    status = Column(String(16), default="PENDING")  # PENDING/APPROVED/REJECTED
    comment = Column(Text)
    handled_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
