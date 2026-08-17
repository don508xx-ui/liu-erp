"""
通知服务 - 飞书/企微/邮件,失败不阻塞业务
所有外发走这里,通过事件钩子触发
"""
from sqlalchemy.orm import Session
from app.models.notification import NotificationTemplate, NotificationLog, NotificationChannel
from app.config import settings
from datetime import datetime, timezone, timedelta
import httpx
import logging

BJT = timezone(timedelta(hours=8))
def bjt_now():
    return datetime.now(BJT).replace(tzinfo=None)

logger = logging.getLogger(__name__)


def render(template: str, vars: dict) -> str:
    try:
        from string import Template
        return Template(template).safe_substitute(**(vars or {}))
    except Exception:
        return template


def send(db: Session, template_code: str, channel: str, recipient: str,
         recipient_name: str = "", variables: dict = None):
    tpl = db.query(NotificationTemplate).filter(NotificationTemplate.code == template_code).first()
    title = render(tpl.title_template, variables) if tpl else template_code
    body = render(tpl.body_template, variables) if tpl else str(variables)

    log = NotificationLog(
        template_id=tpl.id if tpl else None,
        template_code=template_code,
        channel=channel,
        recipient=recipient,
        recipient_name=recipient_name,
        title=title,
        body=body,
        payload=variables,
        status="PENDING",
    )
    db.add(log)
    db.flush()

    try:
        if channel == "FEISHU" and settings.FEISHU_WEBHOOK:
            _send_feishu(settings.FEISHU_WEBHOOK, title, body)
        elif channel == "WECOM_WORK" and settings.WECOM_WEBHOOK:
            _send_wecom(settings.WECOM_WEBHOOK, title, body)
        elif channel == "EMAIL" and settings.SMTP_HOST:
            _send_email(recipient, title, body)
        elif channel == "INAPP":
            pass  # 站内信仅落库
        else:
            log.status = "SENT"  # 渠道未配置,记日志即可,不阻塞
            log.error_msg = "channel not configured, mock sent"
        if log.status == "PENDING":
            log.status = "SENT"
        log.sent_at = bjt_now()
    except Exception as e:
        log.status = "FAILED"
        log.error_msg = str(e)
        logger.warning(f"通知发送失败({channel}): {e}")
    db.flush()
    return log


def _send_feishu(webhook: str, title: str, body: str):
    payload = {"msg_type": "text", "content": {"text": f"【{title}】\n{body}"}}
    with httpx.Client(timeout=5) as c:
        r = c.post(webhook, json=payload)
        r.raise_for_status()


def _send_wecom(webhook: str, title: str, body: str):
    payload = {"msgtype": "text", "text": {"content": f"【{title}】\n{body}"}}
    with httpx.Client(timeout=5) as c:
        r = c.post(webhook, json=payload)
        r.raise_for_status()


def _send_email(to: str, title: str, body: str):
    import asyncio
    import aiosmtplib
    from email.mime.text import MIMEText

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = title
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to

    async def _go():
        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASS,
            start_tls=True,
        )
    asyncio.get_event_loop().run_until_complete(_go()) if asyncio.get_event_loop().is_running() is False else None
