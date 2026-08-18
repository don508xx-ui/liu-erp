"""
测试数据填充脚本 - 模拟真实业务数据供透视分析使用
运行: python scripts/fill_test_data.py
"""
import sys, os, random
from datetime import datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import SessionLocal, engine, Base
from app.models.order import Order, OrderItem
from app.models.finance import FinanceDoc, WorkOrderCost
from app.models.workshop import WorkOrder, Completion, CompletionItem
from app.models.inventory import InventoryItem, InventoryTxn
from app.models.sales import Company, Opportunity
from app.models.customer import Customer
from app.models.system import User, Role

random.seed(42)
db = SessionLocal()

def get_or_create(model, lookup_field, **kwargs):
    """按唯一字段查找或创建对象,已存在则更新其他字段"""
    lookup_val = kwargs.pop(lookup_field)
    obj = db.query(model).filter_by(**{lookup_field: lookup_val}).first()
    if obj:
        for k, v in kwargs.items():
            setattr(obj, k, v)
        db.flush()
        return obj
    kwargs[lookup_field] = lookup_val
    try:
        obj = model(**kwargs)
        db.add(obj)
        db.flush()
        db.refresh(obj)
        return obj
    except Exception as e:
        db.rollback()
        obj = db.query(model).filter_by(**{lookup_field: lookup_val}).first()
        if obj:
            return obj
        print(f"  WARN: Failed to create {model.__name__}: {e}")
        return None

# ========== 1. 公司 ==========
companies = []
for i, (code, name, tax_type) in enumerate([
    ("GENERAL", "峰业精密制造有限公司", "GENERAL"),
    ("SMALL", "峰业表面处理有限公司", "SMALL"),
]):
    c = get_or_create(Company, "code", code=code, name=name, tax_type=tax_type,
                      tax_no=f"91310000MA{'A' if i==0 else 'B'}1234567",
                      bank_name="招商银行", bank_account=f"622588010000{i+1:04d}",
                      address="上海市嘉定区安亭镇园区路123号",
                      status="ACTIVE")
    companies.append(c)
print(f"创建公司: {len(companies)} 家")

# ========== 2. 客户 ==========
customers_data = [
    ("CUST-001", "上海汽车配件有限公司", "汽配", "月结30天", "SALES"),
    ("CUST-002", "杭州家电制造集团", "家电", "月结60天", "SALES"),
    ("CUST-003", "苏州五金制品有限公司", "五金", "款到发货", "SALES"),
    ("CUST-004", "宁波化工科技股份", "化工", "月结45天", "SALES"),
    ("CUST-005", "无锡机械制造有限公司", "机械", "月结30天", "SALES"),
    ("CUST-006", "常州电子科技集团", "电子", "月结90天", "SALES"),
]
customers = []
for code, name, industry, cycle, _ in customers_data:
    c = get_or_create(Customer, "code", code=code, name=name, industry=industry,
                      settlement_cycle=cycle,
                      contact_name="张经理", contact_phone=f"138{random.randint(10000000,99999999)}",
                      address=f"{name}地址", status="ACTIVE")
    customers.append(c)
print(f"创建客户: {len(customers)} 家")

# ========== 3. 销售人员 ==========
sales_users = []
for username, name in [("sales01", "李销售"), ("sales02", "王销售"), ("sales03", "赵销售")]:
    u = db.query(User).filter(User.username == username).first()
    if not u:
        u = User(username=username, name=name, status="ACTIVE",
                 role_id=db.query(Role).filter(Role.code == "SALES").first().id)
        db.add(u); db.flush()
    sales_users.append(u)
print(f"销售人员: {len(sales_users)} 人")

# ========== 4. 物料 ==========
materials = []
for code, name, cat in [
    ("MAT-001", "环氧树脂粉末涂料", "PAINT_POWDER"),
    ("MAT-002", "聚酯树脂粉末涂料", "PAINT_POWDER"),
    ("MAT-003", "聚氨脂涂料", "PAINT_POWDER"),
    ("MAT-004", "防锈底漆", "PAINT_POWDER"),
    ("MAT-005", "固化剂", "CONSUMABLE"),
]:
    m = get_or_create(InventoryItem, "code", code=code, name=name, category=cat,
                      spec="25kg/袋", unit="kg", stock_qty=Decimal(str(random.randint(200, 1000))),
                      unit_cost=Decimal(str(round(random.uniform(20, 80), 2))),
                      location=f"库房A-{random.randint(1,5):02d}", status="ACTIVE")
    materials.append(m)
print(f"创建物料: {len(materials)} 种")

# ========== 5. 订单 (30单, 跨6个月) ==========
statuses = ["DRAFT", "SUBMITTED", "EFFECTIVE", "PROCESSING", "PENDING_DELIVERY", "DELIVERED", "CLOSED", "CANCELLED"]
billing_types = ["SPECIAL_VAT", "NORMAL", "CASH"]
workshops = ["车间A", "车间B", "车间C"]

orders = []
for i in range(30):
    customer = customers[i % len(customers)]
    sales = sales_users[i % len(sales_users)]
    company = companies[i % len(companies)]
    
    # 状态分布
    if i < 5: status = "DRAFT"
    elif i < 10: status = "SUBMITTED"
    elif i < 18: status = "EFFECTIVE"
    elif i < 22: status = "PROCESSING"
    elif i < 26: status = "DELIVERED"
    else: status = "CLOSED"
    
    total = round(random.uniform(5000, 80000), 2)
    prepay = round(total * random.uniform(0.1, 0.5), 2)
    
    # 创建日期分布在最近6个月
    days_ago = random.randint(1, 180)
    created = datetime.utcnow() - timedelta(days=days_ago)
    
    order = Order(
        order_no=f"SO-{created.strftime('%Y%m%d')}-{i+1:03d}",
        customer_id=customer.id,
        status=status,
        total_amount=Decimal(str(total)),
        prepayment_amount=Decimal(str(prepay)),
        prepayment_ratio=Decimal(str(round(prepay/total*100, 2))),
        sales_user_id=sales.id,
        company_id=company.id,
        billing_type=random.choice(billing_types),
        delivery_status="DELIVERED" if status in ["DELIVERED", "CLOSED"] else "PENDING",
        return_count=random.choice([0, 0, 0, 1, 2]) if status == "CLOSED" else 0,
        created_at=created,
        effective_at=created + timedelta(days=3) if status in ["EFFECTIVE", "PROCESSING", "PENDING_DELIVERY", "DELIVERED", "CLOSED"] else None,
        closed_at=created + timedelta(days=30) if status == "CLOSED" else None,
    )
    db.add(order)
    orders.append(order)
    
    # 订单明细
    item_count = random.randint(1, 3)
    for j in range(item_count):
        qty = round(random.uniform(10, 500), 2)
        price = round(total / item_count / qty, 2)
        item = OrderItem(
            order=order,
            seq=j + 1,
            part_name=f"工件-{i+1}-{j+1}",
            part_spec=f"规格{random.randint(100,999)}",
            price_type=random.choice(["BY_PIECE", "BY_AREA", "BY_WEIGHT"]),
            quantity=Decimal(str(qty)),
            unit=random.choice(["件", "m²", "kg"]),
            unit_price=Decimal(str(price)),
            amount=Decimal(str(round(qty * price, 2))),
            material_mode=random.choice(["CUSTOMER", "SELF"]),
        )
        db.add(item)

db.flush()
print(f"创建订单: {len(orders)} 单")

# ========== 6. 工单 ==========
work_orders = []
for i, order in enumerate(orders):
    if order.status in ["DRAFT", "SUBMITTED", "CANCELLED"]:
        continue
    wo = WorkOrder(
        order_id=order.id,
        workshop=random.choice(workshops),
        status=random.choice(["DRAFT", "CONFIRMED", "IN_PROGRESS"]),
        plan_qty=order.items[0].quantity if order.items else Decimal("100"),
        created_at=order.created_at + timedelta(days=1),
    )
    db.add(wo)
    work_orders.append(wo)

db.flush()
print(f"创建工单: {len(work_orders)} 个")

# ========== 7. 完工单 + 工单成本 ==========
completions = []
for wo in work_orders:
    if wo.status == "DRAFT":
        continue
    comp = Completion(
        work_order_id=wo.id,
        status=random.choice(["DRAFT", "CONFIRMED"]),
        created_at=wo.created_at + timedelta(days=random.randint(3, 15)),
    )
    db.add(comp)
    completions.append(comp)
    
    # 完工明细
    for j in range(random.randint(1, 3)):
        ci = CompletionItem(
            completion=comp,
            item_name=f"工件-{wo.id}-{j+1}",
            theoretical_qty=Decimal(str(random.randint(50, 200))),
            actual_qty=Decimal(str(random.randint(40, 190))),
            utilization_rate=Decimal(str(round(random.uniform(0.6, 1.0), 2))),
            return_qty=Decimal(str(random.randint(0, 10))),
        )
        db.add(ci)
    
    # 工单成本
    for cost_type in ["MATERIAL", "LABOR", "OVERHEAD"]:
        cost = WorkOrderCost(
            work_order_id=wo.id,
            cost_type=cost_type,
            amount=Decimal(str(round(random.uniform(500, 5000), 2))),
            occurred_at=comp.created_at,
        )
        db.add(cost)

db.flush()
print(f"创建完工单: {len(completions)} 个")

# ========== 8. 财务单据 (应收/应付) ==========
finance_docs = []
for order in orders:
    if order.status in ["DRAFT", "CANCELLED"]:
        continue
    
    # 应收单
    ar = FinanceDoc(
        doc_no=f"AR-{order.order_no}",
        doc_type="RECEIVABLE",
        status=random.choice(["DRAFT", "OPEN", "SETTLED"]),
        related_type="ORDER",
        related_id=order.id,
        counterparty_type="CUSTOMER",
        counterparty_id=order.customer_id,
        counterparty_name=customers[order.customer_id % len(customers)].name,
        amount=order.total_amount,
        settled_amount=Decimal(str(round(float(order.total_amount) * random.uniform(0.3, 1.0), 2))),
        company_id=order.company_id,
        billing_type=order.billing_type,
        due_date=order.created_at + timedelta(days=90),
        created_at=order.created_at,
    )
    db.add(ar)
    finance_docs.append(ar)
    
    # 应付单 (对应工单成本)
    if order.status in ["EFFECTIVE", "PROCESSING", "DELIVERED", "CLOSED"]:
        ap = FinanceDoc(
            doc_no=f"AP-{order.order_no}",
            doc_type="PAYABLE",
            status=random.choice(["OPEN", "SETTLED"]),
            related_type="ORDER",
            related_id=order.id,
            counterparty_type="SUPPLIER",
            counterparty_id=random.randint(1, 10),
            counterparty_name=f"供应商-{random.randint(1,5)}",
            amount=Decimal(str(round(float(order.total_amount) * random.uniform(0.3, 0.6), 2))),
            settled_amount=Decimal(str(round(float(order.total_amount) * random.uniform(0.2, 0.5), 2))),
            company_id=order.company_id,
            billing_type=order.billing_type,
            due_date=order.created_at + timedelta(days=60),
            created_at=order.created_at + timedelta(days=7),
        )
        db.add(ap)
        finance_docs.append(ap)

db.flush()
print(f"创建财务单据: {len(finance_docs)} 张")

# ========== 9. 库存流水 ==========
inventory_txns = []
for i, mat in enumerate(materials):
    for _ in range(5):  # 每个物料5条流水
        txn_type = random.choice(["IN", "OUT", "RETURN"])
        qty = round(random.uniform(10, 100), 2)
        amount = round(qty * float(mat.unit_cost), 2)
        txn = InventoryTxn(
            txn_no=f"TXN-{datetime.utcnow().strftime('%Y%m%d')}-{i*5+_+1:03d}",
            txn_type=txn_type,
            item_id=mat.id,
            quantity=Decimal(str(qty)),
            unit_cost=mat.unit_cost,
            amount=Decimal(str(amount)),
            warehouse=f"库房{random.randint(1,3)}",
            occurred_at=datetime.utcnow() - timedelta(days=random.randint(1, 90)),
        )
        db.add(txn)
        inventory_txns.append(txn)

db.flush()
print(f"创建库存流水: {len(inventory_txns)} 条")

# ========== 10. 商机 ==========
opportunities = []
for i in range(15):
    customer = customers[i % len(customers)]
    sales = sales_users[i % len(sales_users)]
    opp = Opportunity(
        customer_id=customer.id,
        stage=random.choice(["INITIAL", "QUALIFICATION", "PROPOSAL", "NEGOTIATION", "WON", "LOST"]),
        source=random.choice(["网络推广", "展会", "老客户介绍", "电话营销"]),
        owner_user_id=sales.id,
        expected_amount=Decimal(str(round(random.uniform(10000, 200000), 2))),
        created_at=datetime.utcnow() - timedelta(days=random.randint(10, 200)),
    )
    db.add(opp)
    opportunities.append(opp)

db.flush()
print(f"创建商机: {len(opportunities)} 个")

# ========== 11. 工单成本 (补充零散数据) ==========
for i in range(10):
    wo = random.choice(work_orders)
    cost = WorkOrderCost(
        work_order_id=wo.id,
        cost_type=random.choice(["MATERIAL", "LABOR", "OVERHEAD", "OUTSOURCE"]),
        amount=Decimal(str(round(random.uniform(200, 3000), 2))),
        occurred_at=wo.created_at + timedelta(days=random.randint(1, 20)),
    )
    db.add(cost)

db.commit()
print("\n" + "=" * 50)
print("测试数据填充完成!")
print(f"  公司: {len(companies)} 家")
print(f"  客户: {len(customers)} 家")
print(f"  物料: {len(materials)} 种")
print(f"  订单: {len(orders)} 单")
print(f"  工单: {len(work_orders)} 个")
print(f"  完工单: {len(completions)} 个")
print(f"  财务单据: {len(finance_docs)} 张")
print(f"  库存流水: {len(inventory_txns)} 条")
print(f"  商机: {len(opportunities)} 个")
print("=" * 50)
print("\n可以访问透视分析页面测试多维组合查询了")
db.close()