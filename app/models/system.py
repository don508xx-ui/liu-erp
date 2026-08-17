from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, JSON, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.db import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(64))
    role_id = Column(Integer, ForeignKey("roles.id"))
    phone = Column(String(32))
    email = Column(String(128))
    status = Column(String(16), default="ACTIVE")  # ACTIVE/DISABLED
    created_at = Column(DateTime, default=datetime.utcnow)

    role = relationship("Role")


class Role(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False)
    code = Column(String(64), unique=True, nullable=False)
    description = Column(String(255))
    scope = Column(JSON)
    pages = Column(JSON)  # 可访问页面key列表 e.g. ["dashboard","orders","approvals"]


class Permission(Base):
    __tablename__ = "permissions"
    id = Column(Integer, primary_key=True)
    code = Column(String(128), unique=True, nullable=False)  # e.g. order:create
    name = Column(String(128))
    module = Column(String(32))  # order/work_order/inventory/finance...
    action = Column(String(32))  # create/read/update/delete/approve


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id = Column(Integer, ForeignKey("roles.id"), primary_key=True)
    permission_id = Column(Integer, ForeignKey("permissions.id"), primary_key=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    user_name = Column(String(64))
    action = Column(String(64))  # create/update/delete/approve/state_change
    entity_type = Column(String(32))
    entity_id = Column(Integer)
    before = Column(JSON)
    after = Column(JSON)
    ip = Column(String(64))
    created_at = Column(DateTime, default=datetime.utcnow)


class EventLog(Base):
    __tablename__ = "event_log"
    id = Column(Integer, primary_key=True)
    event_type = Column(String(64), index=True)  # order.effective / work_order.released ...
    entity_type = Column(String(32))
    entity_id = Column(Integer)
    payload = Column(JSON)
    status = Column(String(16), default="PENDING")  # PENDING/PROCESSED/FAILED
    error = Column(Text)
    occurred_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime)


class AgentApiToken(Base):
    __tablename__ = "agent_api_tokens"
    id = Column(Integer, primary_key=True)
    name = Column(String(64))
    token_hash = Column(String(255), unique=True)
    scopes = Column(JSON)  # ["read:*","write:alert_rules","write:report_templates"]
    expires_at = Column(DateTime)
    status = Column(String(16), default="ACTIVE")
    created_at = Column(DateTime, default=datetime.utcnow)
