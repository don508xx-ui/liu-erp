from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.db import Base


class AIConversation(Base):
    """AI对话会话 - 每个用户可有多个会话"""
    __tablename__ = "ai_conversations"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    title = Column(String(128), default="新对话")
    scope = Column(String(16), default="analysis")  # analysis经营助手/finance财务专职助手
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages = relationship("AIMessage", back_populates="conversation", cascade="all, delete-orphan", order_by="AIMessage.created_at")


class AIMessage(Base):
    """AI消息 - 对话中的单条消息"""
    __tablename__ = "ai_messages"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("ai_conversations.id"), nullable=False, index=True)
    role = Column(String(16), nullable=False)  # user / ai / system
    content = Column(Text, nullable=False)
    data_type = Column(String(16), default="text")  # text / pivot / general / clarify / error
    extra = Column(Text)  # JSON extra data (pivot结果等)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    conversation = relationship("AIConversation", back_populates="messages")


class AIMemory(Base):
    """AI长期记忆 - 用户偏好/常用查询/业务上下文"""
    __tablename__ = "ai_memories"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    category = Column(String(32), default="preference")  # preference / pattern / context
    content = Column(Text, nullable=False)
    keywords = Column(String(512))  # 触发关键词,逗号分隔
    is_active = Column(Boolean, default=True)
    hit_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (Index("ix_mem_user_active", "user_id", "is_active"),)
