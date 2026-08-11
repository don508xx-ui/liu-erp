from sqlalchemy import Column, Integer, String, DateTime, JSON, Text
from datetime import datetime
from app.core.db import Base


class NotificationTemplate(Base):
    __tablename__ = "notification_templates"
    id = Column(Integer, primary_key=True)
    code = Column(String(64), unique=True)  # order.effective.notice
    name = Column(String(128))
    channel = Column(String(16))  # FEISHU/WECOM_WORK/EMAIL/INAPP
    title_template = Column(String(255))
    body_template = Column(Text)
    variables = Column(JSON)  # ["order_no","customer_name","amount"]
    status = Column(String(16), default="ACTIVE")


class NotificationChannel(Base):
    __tablename__ = "notification_channels"
    id = Column(Integer, primary_key=True)
    channel = Column(String(16))  # FEISHU/WECOM_WORK/EMAIL/INAPP
    name = Column(String(64))
    config = Column(JSON)  # {webhook_url} / {smtp_host,from...}
    status = Column(String(16), default="ACTIVE")


class NotificationLog(Base):
    __tablename__ = "notification_logs"
    id = Column(Integer, primary_key=True)
    template_id = Column(Integer)
    template_code = Column(String(64))
    channel = Column(String(16))
    recipient = Column(String(255))  # user_id 或 open_id/email
    recipient_name = Column(String(64))
    title = Column(String(255))
    body = Column(Text)
    payload = Column(JSON)
    status = Column(String(16), default="PENDING")  # PENDING/SENT/FAILED
    error_msg = Column(Text)
    sent_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
