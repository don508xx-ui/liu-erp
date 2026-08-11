"""V2 补充种子 - 回款节点 + 预警规则 + 订单归属销售 + 应收到期日"""
import sys, os
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.db import SessionLocal, engine
from app.core.db import Base
from app.models.analysis import PaymentSchedule, AlertRule
from app.models.order import Order
from app.models.customer import Customer
from app.models.finance import FinanceDoc
from app.models.sales import Contract
from app.models.system import User

db = SessionLocal()
now = datetime.utcnow()

# 1. 订单归属销售(让销售有自己的订单)
print("[1] 订单归属销售...")
users = {u.username: u for u in db.query(User).all()}
custs = {c.id: c for c in db.query(Customer).all()}
orders = db.query(Order).all()
# 按客户owner分配: 客户1归属sales01, 客户2归属sales02, 客户3归属sales01
sales_map = {1: users.get("sales01"), 2: users.get("sales02"), 3: users.get("sales01")}
for o in orders:
    su = sales_map.get(o.customer_id)
    if su and o.sales_user_id != su.id:
        o.sales_user_id = su.id
        print(f"  订单{o.order_no} -> sales_user={su.username}")
db.flush()

# 2. 回款节点(PaymentSchedule) - 含逾期/即将到期/未来
print("[2] 回款节点...")
db.query(PaymentSchedule).delete()
db.flush()
contracts = db.query(Contract).all()
seq = 0
schedules_data = [
    # (customer_id, contract_id, order_id, days_offset, amount, stage, status)
    # 已逾期
    (1, contracts[0].id if contracts else None, orders[0].id if orders else None, -15, 15000, "进度款(逾期)", "OVERDUE"),
    (2, contracts[1].id if len(contracts) > 1 else None, orders[1].id if len(orders) > 1 else None, -5, 12000, "预付款(逾期)", "OVERDUE"),
    # 即将到期(7天内)
    (1, contracts[0].id if contracts else None, orders[0].id if orders else None, 3, 10000, "尾款", "DUE"),
    (3, None, orders[2].id if len(orders) > 2 else None, 7, 8000, "进度款", "UPCOMING"),
    # 未来到期(30天内)
    (2, contracts[1].id if len(contracts) > 1 else None, orders[1].id if len(orders) > 1 else None, 20, 18000, "尾款", "UPCOMING"),
    (1, contracts[0].id if contracts else None, None, 28, 5000, "质保金", "UPCOMING"),
]
for cid, ctid, oid, off, amt, stage, status in schedules_data:
    seq += 1
    ps = PaymentSchedule(
        schedule_no=f"PS-{now.strftime('%Y%m%d')}-{seq:04d}",
        contract_id=ctid, order_id=oid, customer_id=cid,
        due_date=now + timedelta(days=off),
        expected_amount=amt, actual_amount=0,
        status=status, stage=stage,
    )
    db.add(ps)
    print(f"  {ps.schedule_no} 客户{cid} {stage} {off}天 {amt}元 {status}")
db.flush()

# 3. 应收单据强制覆盖到期日(让逾期应收能被预警)
print("[3] 应收到期日覆盖...")
ars = db.query(FinanceDoc).filter(FinanceDoc.doc_type == "RECEIVABLE").all()
for i, ar in enumerate(ars):
    # 第1个逾期20天, 第2个逾期10天, 其余未来30天
    off = -20 if i == 0 else (-10 if i == 1 else 30)
    ar.due_date = now + timedelta(days=off)
    print(f"  {ar.doc_no} due_date={ar.due_date.date()} ({off}天)")
db.flush()

# 4. 预警规则(AlertRule)
print("[4] 预警规则...")
db.query(AlertRule).delete()
db.flush()
rules = [
    AlertRule(code="AR_GM_LOW", name="毛利率低于15%", metric="GROSS_MARGIN",
              condition={"op": "<", "value": 0.15}, channels=["INAPP"], recipients=["GM"], enabled=True),
    AlertRule(code="AR_AR_OVERDUE", name="应收超期(金额>0)", metric="RECEIVABLE_AGING",
              condition={"op": ">", "value": 0}, channels=["INAPP"], recipients=["FINANCE", "GM"], enabled=True),
    AlertRule(code="AR_PAY_OVERDUE", name="回款节点逾期(数>0)", metric="PAYMENT_OVERDUE",
              condition={"op": ">", "value": 0}, channels=["INAPP"], recipients=["SALES", "GM"], enabled=True),
    AlertRule(code="AR_STOCK_LOW", name="库存不足(项>0)", metric="STOCK_LOW",
              condition={"op": ">", "value": 0}, channels=["INAPP"], recipients=["WAREHOUSE"], enabled=True),
    AlertRule(code="AR_UTIL_LOW", name="涂料利用率<80%", metric="UTILIZATION_LOW",
              condition={"op": "<", "value": 80}, channels=["INAPP"], recipients=["OPERATION"], enabled=True),
]
for r in rules:
    db.add(r)
    print(f"  {r.code} {r.name} {r.metric} {r.condition}")
db.commit()

# 5. 汇总
print("\n[完成] V2补充数据已入库")
print(f"  回款节点: {db.query(PaymentSchedule).count()} 条")
print(f"  预警规则: {db.query(AlertRule).count()} 条")
overdue = db.query(PaymentSchedule).filter(PaymentSchedule.due_date < now).count()
print(f"  逾期回款节点: {overdue} 条")
db.close()
