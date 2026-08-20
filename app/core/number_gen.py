"""单据编号生成器 - 大厂标准方案（极简版）"""
from datetime import datetime
from sqlalchemy import update
from app.core.db import SessionLocal
from app.models.approval import NumberRule

# 业务类型 → 编号前缀 自动映射表（预设，无需管理员配置）
BIZ_TYPE_TO_PREFIX = {
    "SALES_ADJUSTMENT": "SA",
    "EXPENSE": "BX",
    "PURCHASE_REQUEST": "CG",
    "PROCUREMENT": "CG",
    "COMPLETION": "WC",
    "CORE_PRODUCTION": "PO",
    "PAYROLL": "GZ",
    "RECEIVING": "LAI",
}

# 默认编号规则（首次启动时自动创建，序号永不重置）
DEFAULT_RULES = {
    "SALES_ADJUSTMENT": {"seq_length": 4},
    "EXPENSE":          {"seq_length": 4},
    "PURCHASE_REQUEST": {"seq_length": 4},
    "PROCUREMENT":      {"seq_length": 4},
    "COMPLETION":       {"seq_length": 4},
    "CORE_PRODUCTION":  {"seq_length": 5},
    "PAYROLL":          {"seq_length": 4},
    "RECEIVING":        {"seq_length": 4},
}


def ensure_default_rules(db=None):
    """确保默认编号规则存在（幂等，序号永不重置）
    传入db时复用该会话(与启动seed同一事务, 避免SQLite锁冲突), 由调用方commit并关闭。"""
    own = False
    if db is None:
        db = SessionLocal()
        own = True
    try:
        for biz_type, rule in DEFAULT_RULES.items():
            existing = db.query(NumberRule).filter(NumberRule.biz_type == biz_type).first()
            if not existing:
                prefix = BIZ_TYPE_TO_PREFIX.get(biz_type, biz_type[:2].upper())
                db.add(NumberRule(
                    biz_type=biz_type,
                    prefix=prefix,
                    seq_length=rule["seq_length"],
                    reset_cycle="NONE",  # 永不重置
                    date_format="%Y%m%d",
                    current_seq=0,
                    current_period="ALL"
                ))
        if own:
            db.commit()
    finally:
        if own:
            db.close()


def generate_number(biz_type, db=None):
    """生成唯一单据编号（序号单调递增，永不重置）
    
    格式: 前缀-YYYYMMDD-序号
    示例: SA-20260820-0001, SA-20260820-0002
    """
    own_db = False
    if db is None:
        db = SessionLocal()
        own_db = True
    try:
        rule = db.query(NumberRule).filter(NumberRule.biz_type == biz_type).first()
        if not rule:
            prefix = BIZ_TYPE_TO_PREFIX.get(biz_type, biz_type[:2].upper())
            rule = NumberRule(
                biz_type=biz_type,
                prefix=prefix,
                seq_length=4,
                reset_cycle="NONE",
                date_format="%Y%m%d",
                current_seq=0,
                current_period="ALL"
            )
            db.add(rule)
            db.flush()

        now = datetime.utcnow()
        date_str = now.strftime(rule.date_format)

        # 原子递增 current_seq: 单条UPDATE在SQLite下自动获取写锁,
        # 并发请求会串行执行, 保证编号唯一且单调
        db.execute(
            update(NumberRule)
            .where(NumberRule.biz_type == biz_type)
            .values(current_seq=NumberRule.current_seq + 1)
        )
        db.refresh(rule)  # 读取递增后的最新值
        seq = str(rule.current_seq).zfill(rule.seq_length)

        # 生成编号: 前缀-日期-序号
        number = f"{rule.prefix}-{date_str}-{seq}"

        db.commit()
        return number
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()


def get_rule(biz_type, db=None):
    """获取编号规则"""
    own_db = False
    if db is None:
        db = SessionLocal()
        own_db = True
    try:
        return db.query(NumberRule).filter(NumberRule.biz_type == biz_type).first()
    finally:
        if own_db:
            db.close()


def list_rules(db=None):
    """列出所有编号规则"""
    own_db = False
    if db is None:
        db = SessionLocal()
        own_db = True
    try:
        return db.query(NumberRule).all()
    finally:
        if own_db:
            db.close()


def get_prefix_for_biz_type(biz_type):
    """获取业务类型对应的编号前缀"""
    return BIZ_TYPE_TO_PREFIX.get(biz_type, biz_type[:2].upper())
