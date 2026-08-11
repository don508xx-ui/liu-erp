from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.core.auth import get_current_user
from app.models.system import User
from app.models.notification import NotificationTemplate, NotificationLog, NotificationChannel
from app.schemas import Resp

router = APIRouter(prefix="/api/notifications", tags=["notification"])


@router.get("/templates")
def templates(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(NotificationTemplate).all()
    return {"code": 0, "data": [{"id": t.id, "code": t.code, "name": t.name, "channel": t.channel, "title_template": t.title_template, "body_template": t.body_template} for t in rows]}


@router.get("/logs")
def logs(channel: str = None, page: int = 1, size: int = 20,
         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(NotificationLog)
    if channel:
        q = q.filter(NotificationLog.channel == channel)
    total = q.count()
    rows = q.order_by(NotificationLog.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [{
        "id": l.id, "template_code": l.template_code, "channel": l.channel,
        "recipient": l.recipient, "recipient_name": l.recipient_name,
        "title": l.title, "body": l.body, "status": l.status,
        "error_msg": l.error_msg, "sent_at": l.sent_at.isoformat() if l.sent_at else None,
    } for l in rows]}


@router.get("/channels")
def channels(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(NotificationChannel).all()
    return {"code": 0, "data": [{"id": c.id, "channel": c.channel, "name": c.name, "config": c.config, "status": c.status} for c in rows]}
