"""通用字典表 - 单表通用,按type区分"""
from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
from datetime import datetime
from app.core.db import Base


class Dict(Base):
    """通用字典 - PROCESS_TYPE/PAINT_SPEC/PART_SPEC/INDUSTRY/SETTLEMENT_CYCLE/STAGE/WORKSHOP等"""
    __tablename__ = "dicts"
    __table_args__ = (UniqueConstraint("type", "code", name="uq_dict_type_code"),)

    id = Column(Integer, primary_key=True)
    type = Column(String(32), nullable=False, index=True)  # 字典类型
    code = Column(String(64), nullable=False)  # 编码
    name = Column(String(128), nullable=False)  # 显示名
    parent_code = Column(String(64))  # 父级编码(树形)
    sort = Column(Integer, default=0)
    status = Column(String(16), default="ACTIVE")
    remark = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
