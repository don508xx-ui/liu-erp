"""
事件总线 - 业财联动核心
事件触发时同步执行所有已注册钩子,在同一事务内,失败回滚
"""
from sqlalchemy.orm import Session
from app.models.system import EventLog
from datetime import datetime, timezone, timedelta
from typing import Callable, Dict, List
import logging

BJT = timezone(timedelta(hours=8))
def bjt_now():
    return datetime.now(BJT).replace(tzinfo=None)

logger = logging.getLogger(__name__)

_hooks: Dict[str, List[Callable]] = {}


def register(event_type: str):
    def deco(fn: Callable):
        _hooks.setdefault(event_type, []).append(fn)
        return fn
    return deco


def emit(db: Session, event_type: str, entity_type: str = None, entity_id: int = None,
         payload: dict = None, user=None):
    """触发事件 - 同步执行所有钩子,失败抛异常导致事务回滚"""
    log = EventLog(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload or {},
        status="PROCESSING",
    )
    db.add(log)
    db.flush()

    handlers = _hooks.get(event_type, [])
    errors = []
    for h in handlers:
        try:
            h(db, entity_type, entity_id, payload or {}, user)
        except Exception as e:
            logger.exception(f"hook {h.__name__} failed for {event_type}: {e}")
            errors.append(f"{h.__name__}: {e}")

    log.processed_at = bjt_now()
    if errors:
        log.status = "FAILED"
        log.error = "; ".join(errors)
        db.flush()
        raise RuntimeError(f"事件钩子失败: {errors}")
    log.status = "PROCESSED"
    db.flush()


def list_hooks():
    return {k: [h.__name__ for h in v] for k, v in _hooks.items()}
