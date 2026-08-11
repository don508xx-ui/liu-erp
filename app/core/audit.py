from sqlalchemy.orm import Session
from app.models.system import AuditLog
import json


def log_audit(db: Session, user, action: str, entity_type: str, entity_id: int,
              before=None, after=None, ip: str = ""):
    entry = AuditLog(
        user_id=user.id if user else None,
        user_name=user.name if user else "system",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=_safe(before),
        after=_safe(after),
        ip=ip,
    )
    db.add(entry)
    db.flush()


def _safe(obj):
    if obj is None:
        return None
    if isinstance(obj, (dict, list)):
        return obj
    try:
        # SQLAlchemy模型转dict
        return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    except Exception:
        return str(obj)
