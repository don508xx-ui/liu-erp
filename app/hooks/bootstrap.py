"""通知模板预置 - 由seed脚本调用"""
from app.models.notification import NotificationTemplate


NOTIFICATION_TEMPLATES = [
    ("order.submitted.notice", "新订单待处理", "INAPP",
     "新订单 ${order_no} 已提交",
     "客户:${customer}\n金额:${amount}元\n销售:${sales}"),
    ("order.effective.notice", "订单已生效", "INAPP",
     "订单 ${order_no} 已生效",
     "客户:${customer}\n合同金额:${amount}元\n预收款:${prepayment}元"),
    ("work_order.released.notice", "加工单已下达", "INAPP",
     "加工单 ${work_order_no} 已下达${workshop}",
     "请安排加工"),
    ("completion.confirmed.notice", "完工单已确认", "INAPP",
     "完工单 ${completion_no} 已确认",
     "工单:${work_order_no}\n订单:${order_no}\n客户:${customer}\n应收金额:${amount}元"),
    ("payment.remind", "催款通知", "INAPP",
     "请催收 ${customer} 货款",
     "订单:${order_no}\n应收余额:${amount}元"),
    ("alert.trigger", "预警触发", "INAPP",
     "${message}",
     "规则:${rule}"),
]


def seed_templates(db):
    for code, name, ch, title, body in NOTIFICATION_TEMPLATES:
        if db.query(NotificationTemplate).filter(NotificationTemplate.code == code).first():
            continue
        db.add(NotificationTemplate(
            code=code, name=name, channel=ch,
            title_template=title, body_template=body,
            variables=[], status="ACTIVE",
        ))
    db.commit()
