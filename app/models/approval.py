from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, ForeignKey
from datetime import datetime
from app.core.db import Base
import json


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
    status = Column(String(16), default="RUNNING")
    current_node_seq = Column(Integer, default=1)
    initiator_user_id = Column(Integer)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime)
    biz_data = Column(Text)  # 流程实例的业务数据（JSON字符串）

    def get_biz_data(self):
        if not self.biz_data:
            return {}
        if isinstance(self.biz_data, str):
            return json.loads(self.biz_data)
        return self.biz_data or {}

    def set_biz_data(self, data):
        self.biz_data = json.dumps(data, ensure_ascii=False)


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
    form_data = Column(Text)  # 节点表单数据（JSON字符串）
    handled_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    def get_form_data(self):
        if not self.form_data:
            return {}
        if isinstance(self.form_data, str):
            return json.loads(self.form_data)
        return self.form_data or {}

    def set_form_data(self, data):
        self.form_data = json.dumps(data, ensure_ascii=False)


class NumberRule(Base):
    """单据编号规则配置"""
    __tablename__ = "number_rules"
    id = Column(Integer, primary_key=True)
    biz_type = Column(String(32), index=True, unique=True)
    prefix = Column(String(16))           # 业务前缀: SA/BX/CG/WC
    seq_length = Column(Integer, default=4)  # 自增序号位数: 3-9位
    reset_cycle = Column(String(8), default="DAILY")  # DAILY/MONTHLY/YEARLY/NONE
    date_format = Column(String(16), default="%Y%m%d")  # 日期格式
    current_seq = Column(Integer, default=0)     # 当前序号
    current_period = Column(String(16))          # 当前周期标识(如20260820)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
