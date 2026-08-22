"""全业务关联模拟数据 - 每笔资金流水都能追溯到业务单据"""
from datetime import datetime, timedelta
import random
from sqlalchemy import text, func
from app.models.order import Order, OrderItem
from app.models.workshop import WorkOrder, Completion, CompletionItem
from app.models.finance import WorkOrderCost, FinanceDoc, FinanceItem, PayrollRun
from app.models.fund import FundFlow, AcceptanceBill
from app.models.purchase import Supplier, Purchase, PurchaseItem, PurchaseRequest
from app.models.sales import DeliveryNote, DeliveryNoteItem
from app.models.customer import Customer
from app.models.sales import Company
from app.models.finance import Account
from app.models.fund import FundAccount
from app.models.inventory import InventoryItem


def seed_biz_data(db):
    _do_seed(db)


def _do_seed(db):
    now = datetime(2026, 8, 22)

    # 第0步: 确保FundAccount存在
    if db.query(FundAccount).count() == 0:
        default_accounts = [
            FundAccount(code="JX-BANK", name="机械公账", account_type="BANK", opening_balance=2600000.00, company_id=1),
            FundAccount(code="DG-BANK", name="加工厂公账", account_type="BANK", opening_balance=950000.00, company_id=2),
            FundAccount(code="ACCEPTANCE", name="承兑汇票", account_type="ACCEPTANCE", opening_balance=0, company_id=1),
            FundAccount(code="CASH", name="库存现金", account_type="CASH", opening_balance=60000.00, company_id=1),
        ]
        for a in default_accounts:
            db.add(a)
        db.commit()
        print(f"  创建资金账户: {len(default_accounts)}个")

    # 第1步: 清空事务数据
    print("=" * 60)
    print("第1步: 清空旧事务数据(幂等)")
    db.execute(text("PRAGMA foreign_keys=OFF"))
    for tbl in [FundFlow, AcceptanceBill, FinanceItem, FinanceDoc, WorkOrderCost,
                CompletionItem, Completion, DeliveryNoteItem, DeliveryNote,
                PurchaseItem, Purchase, PurchaseRequest,
                PayrollRun, WorkOrder, OrderItem, Order]:
        c = db.query(tbl).delete()
        if c:
            print(f"  清空 {tbl.__tablename__}: {c}条")
    db.execute(text("PRAGMA foreign_keys=ON"))
    db.commit()

    # 第2步: 加载基础档案
    print("\n第2步: 加载基础档案")
    customers = {c.id: c for c in db.query(Customer).all()}
    companies = {c.id: c for c in db.query(Company).all()}
    accounts = {a.code: a.id for a in db.query(FundAccount).all()}
    print(f"  客户: {len(customers)}家, 公司: {len(companies)}家, 资金账户: {len(accounts)}个")
    ACC_ID = {'JX_BANK': accounts.get('JX-BANK'), 'DG_BANK': accounts.get('DG-BANK'),
              'ACCEPTANCE': accounts.get('ACCEPTANCE'), 'CASH': accounts.get('CASH')}
    COMPANY1_CUSTOMERS = [1, 5, 7, 8]
    COMPANY2_CUSTOMERS = [2, 3, 6]
    CRAFTS = ["超音速火焰喷涂", "等离子喷涂", "氧乙炔火焰陶瓷棒", "碳化钨防粘涂层", "碳纤维防粘涂层"]
    SPECS = [("活塞杆 Φ80×1200", "BY_AREA", 350, "m²"), ("辊轴 Φ150×2000", "BY_AREA", 420, "m²"),
             ("耐磨衬板 500×400", "BY_PIECE", 180, "件"), ("导轮 Φ200", "BY_PIECE", 95, "件"),
             ("密封环 Φ300×50", "BY_PIECE", 65, "件"), ("气缸套 Φ120×300", "BY_PIECE", 120, "件"),
             ("刮刀片 600×80", "BY_PIECE", 85, "件"), ("搅拌桨叶 400×300", "BY_PIECE", 250, "件"),
             ("喷嘴 Φ50×100", "BY_PIECE", 55, "件"), ("链轮 Φ250", "BY_PIECE", 110, "件")]

    # 第3步: 订单→工单→应收
    print("\n第3步: 创建订单→工单→应收单据")
    MONTHS = [(2026, m) for m in range(1, 9)]
    order_no = 0
    total_receivable = 0.0
    for mi, (year, month) in enumerate(MONTHS):
        base = 0.7 + 0.5 * mi / (len(MONTHS) - 1)
        for cid in COMPANY1_CUSTOMERS + COMPANY2_CUSTOMERS:
            n_orders = 2 if cid in COMPANY1_CUSTOMERS else 1
            if mi == 0 and cid in COMPANY2_CUSTOMERS: n_orders = 1
            for oi in range(n_orders):
                order_no += 1
                day = 5 + (order_no * 7) % 20
                order_date = datetime(year, month, min(day, 28))
                company_id = 1 if cid in COMPANY1_CUSTOMERS else 2
                n_items = 2 + (order_no % 2)
                items_data = []
                order_amt = 0.0
                for ii in range(n_items):
                    spec, price_type, price, unit = SPECS[(order_no * 3 + ii) % len(SPECS)]
                    qty = round(5 + (order_no * 7 + ii * 13) % 45, 0)
                    if price_type == "BY_AREA": qty = round(qty * 0.5, 2)
                    amt = round(qty * price * base, 2)
                    order_amt += amt
                    items_data.append({"part_name": spec.split()[0], "part_spec": spec,
                                       "price_type": price_type, "quantity": qty, "unit": unit,
                                       "unit_price": round(price * base, 2), "amount": amt,
                                       "craft_type": CRAFTS[(order_no + ii) % len(CRAFTS)]})
                order_amt = round(order_amt, 2)
                o = Order(order_no=f"SO-{year}{month:02d}-{order_no:03d}", customer_id=cid,
                          company_id=company_id, status="EFFECTIVE", total_amount=order_amt,
                          signed_at=order_date, effective_at=order_date, billing_type="SPECIAL_VAT",
                          sales_user_id=2 if cid in COMPANY1_CUSTOMERS else 3)
                db.add(o); db.flush()
                for ii, item in enumerate(items_data):
                    db.add(OrderItem(order_id=o.id, seq=ii+1, part_name=item["part_name"],
                                     part_spec=item["part_spec"], price_type=item["price_type"],
                                     quantity=item["quantity"], unit=item["unit"],
                                     unit_price=item["unit_price"], amount=item["amount"],
                                     craft_type=item["craft_type"], material_mode="SELF"))
                main_item = items_data[0]
                wo = WorkOrder(work_order_no=f"WO-{year}{month:02d}-{order_no:03d}",
                               order_id=o.id, customer_id=cid, customer_name=customers[cid].name,
                               product_spec=main_item["part_spec"], process=main_item["craft_type"],
                               workshop="A车间" if company_id == 1 else "B车间", status="COMPLETED",
                               plan_qty=main_item["quantity"], actual_qty=main_item["quantity"],
                               plan_finish_date=order_date + timedelta(days=15),
                               delivery_date=order_date + timedelta(days=30),
                               released_at=order_date + timedelta(days=3),
                               completed_at=order_date + timedelta(days=14),
                               confirmed_at=order_date + timedelta(days=16))
                db.add(wo); db.flush()
                qualified = round(float(main_item["quantity"]) * 0.95, 3)
                cp = Completion(completion_no=f"CP-{year}{month:02d}-{order_no:03d}",
                                work_order_id=wo.id, status="CONFIRMED",
                                finished_qty=main_item["quantity"], qualified_qty=qualified,
                                scrap_qty=round(float(main_item["quantity"]) * 0.03, 3),
                                rework_qty=round(float(main_item["quantity"]) * 0.02, 3),
                                labor_hours=round(qualified * 0.5, 2),
                                labor_cost=round(qualified * 25, 2),
                                overhead_cost=round(qualified * 15, 2),
                                operator_user_id=8, confirmed_by_user_id=3,
                                confirmed_at=order_date + timedelta(days=16))
                db.add(cp); db.flush()
                db.add(CompletionItem(completion_id=cp.id, item_id=1, item_name="碳化钨粉末",
                                      theoretical_qty=round(qualified * 0.15, 3),
                                      actual_qty=round(qualified * 0.18, 3),
                                      unit_cost=round(120 * base, 2),
                                      cost_amount=round(qualified * 0.18 * 120 * base, 2)))
                receivable = round(order_amt * 0.9, 2)
                status = "SETTLED" if month < 8 else "OPEN"
                settled = receivable if month < 7 else (receivable * 0.6 if month == 7 else 0)
                settled_at = order_date + timedelta(days=45) if settled > 0 else None
                fd = FinanceDoc(doc_no=f"AR-{year}{month:02d}-{order_no:03d}", doc_type="RECEIVABLE",
                                status=status, related_type="ORDER", related_id=o.id,
                                counterparty_type="CUSTOMER", counterparty_id=cid,
                                counterparty_name=customers[cid].name, amount=order_amt,
                                settled_amount=round(settled, 2), company_id=company_id,
                                billing_type="SPECIAL_VAT", account_date=order_date + timedelta(days=20),
                                due_date=order_date + timedelta(days=60), settled_at=settled_at,
                                source_event="order_delivery")
                db.add(fd); db.flush()
                total_receivable += order_amt
                if settled > 0:
                    receipt_no = order_no * 10 + 1
                    rc = FinanceDoc(doc_no=f"RC-{year}{month:02d}-{receipt_no:03d}", doc_type="RECEIPT",
                                    status="SETTLED", related_type="ORDER", related_id=o.id,
                                    counterparty_type="CUSTOMER", counterparty_id=cid,
                                    counterparty_name=customers[cid].name, amount=round(settled, 2),
                                    settled_amount=round(settled, 2), company_id=company_id,
                                    account_date=settled_at or (order_date + timedelta(days=45)),
                                    settled_at=settled_at, source_event="payment_received")
                    db.add(rc); db.flush()
                    fa_id = ACC_ID['JX_BANK'] if company_id == 1 else ACC_ID['DG_BANK']
                    db.add(FundFlow(fund_account_id=fa_id, direction="IN", amount=round(settled, 2),
                                    counterparty=customers[cid].name,
                                    occur_date=settled_at or (order_date + timedelta(days=45)),
                                    summary=f"客户回款 {customers[cid].name} 订单{o.order_no}",
                                    source_type="RECEIPT", source_id=rc.id))
                if order_no % 10 == 0: db.commit()
    db.commit()
    print(f"  创建订单: {order_no}个, 应收总额: {total_receivable:.2f}元")

    # 第4步: 采购应付→付款→资金流出
    print("\n第4步: 采购应付→付款→资金流出")
    suppliers = {s.name: s.id for s in db.query(Supplier).all()}
    po_no = 0
    total_payable = 0.0
    for mi, (year, month) in enumerate(MONTHS):
        base = 0.7 + 0.5 * mi / (len(MONTHS) - 1)
        for company_id in [1, 2]:
            n_po = 2 if company_id == 1 else 1
            for pi in range(n_po):
                po_no += 1
                day = 8 + (po_no * 5) % 20
                po_date = datetime(year, month, min(day, 28))
                if company_id == 1:
                    supplier_name = "立邦涂料(上海)有限公司" if po_no % 3 != 0 else "阿克苏诺贝尔粉末涂料"
                    po_items = [("耐磨钢板 12mm", round(4800 + base * 1200, 2), round(3 + base * 2, 1)),
                                ("不锈钢管 Φ89×5", round(3600 + base * 900, 2), round(2 + base * 1.5, 1))]
                else:
                    supplier_name = "上海某前处理材料商行" if po_no % 2 != 0 else "某委外电镀加工厂"
                    po_items = [("铝板 6061 8mm", round(3500 + base * 800, 2), round(2 + base, 1)),
                                ("喷涂辅料套装", round(2800 + base * 700, 2), round(1 + base * 0.5, 1))]
                sid = suppliers.get(supplier_name, 1)
                po_amt = round(sum(up * qty for _, up, qty in po_items), 2)
                p = Purchase(po_no=f"PO-{year}{month:02d}-{po_no:03d}", supplier_id=sid,
                             status="RECEIVED", total_amount=po_amt,
                             ordered_at=po_date, received_at=po_date + timedelta(days=7))
                db.add(p); db.flush()
                for ii, (name, up, qty) in enumerate(po_items):
                    db.add(PurchaseItem(purchase_id=p.id, item_id=1, item_name=name,
                                        qty=round(qty, 3), unit="kg", unit_price=up,
                                        amount=round(qty * up, 2), received_qty=qty))
                payable = round(po_amt * 0.95, 2)
                settled = payable if month < 7 else (payable * 0.5 if month == 7 else 0)
                paid_at = po_date + timedelta(days=30) if settled > 0 else None
                fd = FinanceDoc(doc_no=f"AP-{year}{month:02d}-{po_no:03d}", doc_type="PAYABLE",
                                status="SETTLED" if settled >= payable else "OPEN",
                                related_type="PURCHASE", related_id=p.id,
                                counterparty_type="SUPPLIER", counterparty_id=sid,
                                counterparty_name=supplier_name, amount=payable,
                                settled_amount=round(settled, 2), company_id=company_id,
                                account_date=po_date + timedelta(days=10),
                                due_date=po_date + timedelta(days=60), settled_at=paid_at,
                                source_event="purchase_receipt")
                db.add(fd); db.flush()
                total_payable += payable
                if settled > 0:
                    pmt_no = po_no * 10 + 2
                    pmt = FinanceDoc(doc_no=f"PY-{year}{month:02d}-{pmt_no:03d}", doc_type="PAYMENT",
                                     status="SETTLED", related_type="PURCHASE", related_id=p.id,
                                     counterparty_type="SUPPLIER", counterparty_id=sid,
                                     counterparty_name=supplier_name, amount=round(settled, 2),
                                     settled_amount=round(settled, 2), company_id=company_id,
                                     account_date=paid_at, settled_at=paid_at,
                                     source_event="payment_made")
                    db.add(pmt); db.flush()
                    fa_id = ACC_ID['JX_BANK'] if company_id == 1 else ACC_ID['DG_BANK']
                    db.add(FundFlow(fund_account_id=fa_id, direction="OUT", amount=round(settled, 2),
                                    expense_category="耗材", counterparty=supplier_name,
                                    occur_date=paid_at,
                                    summary=f"采购付款 {supplier_name} 采购单{p.po_no}",
                                    source_type="PAYMENT", source_id=pmt.id))
    db.commit()
    print(f"  创建采购单: {po_no}个, 应付总额: {total_payable:.2f}元")

    # 第5步: 工资→资金流出
    print("\n第5步: 工资发放")
    pr_no = 0
    total_wage = 0.0
    for mi, (year, month) in enumerate(MONTHS):
        base = 0.7 + 0.5 * mi / (len(MONTHS) - 1)
        for company_id in [1, 2]:
            pay_date = datetime(year, month, 15)
            wage_amt = round((120000 if company_id == 1 else 62000) * base, 2)
            pr_no += 1
            pr = PayrollRun(run_no=f"PR-{year}{month:02d}-{company_id}", period=f"{year}-{month:02d}",
                            total_amount=wage_amt, status="PAID",
                            items=[{"employee_id": 1, "name": "员工A", "department": "生产",
                                    "base_salary": wage_amt * 0.4, "bonus": wage_amt * 0.1,
                                    "net": wage_amt * 0.5, "bank_amount": wage_amt * 0.45,
                                    "cash_amount": wage_amt * 0.05}], paid_at=pay_date)
            db.add(pr); db.flush()
            pmt = FinanceDoc(doc_no=f"PY-W{year}{month:02d}-{company_id:03d}", doc_type="PAYROLL",
                             status="SETTLED", related_type="PAYROLL", related_id=pr.id,
                             counterparty_type="EMPLOYEE", counterparty_name="全体员工",
                             amount=wage_amt, settled_amount=wage_amt, company_id=company_id,
                             account_date=pay_date, settled_at=pay_date, source_event="payroll")
            db.add(pmt); db.flush()
            pr.finance_doc_id = pmt.id
            fa_id = ACC_ID['JX_BANK'] if company_id == 1 else ACC_ID['DG_BANK']
            db.add(FundFlow(fund_account_id=fa_id, direction="OUT", amount=wage_amt,
                            expense_category="工资", counterparty="全体员工", occur_date=pay_date,
                            summary=f"工资发放 {year}-{month:02d} 公司{company_id}",
                            source_type="PAYROLL", source_id=pmt.id))
            total_wage += wage_amt
            cash_amt = round(wage_amt * 0.05, 2)
            if ACC_ID['CASH']:
                db.add(FundFlow(fund_account_id=ACC_ID['CASH'], direction="OUT", amount=cash_amt,
                                expense_category="工资", counterparty="全体员工", occur_date=pay_date,
                                summary=f"工资现金发放 {year}-{month:02d}",
                                source_type="PAYROLL", source_id=pmt.id))
    db.commit()
    print(f"  工资发放: {pr_no}次, 总额: {total_wage:.2f}元")

    # 第6步: 经营费用
    print("\n第6步: 经营费用(气体/食堂/佣金/提成/融资成本/其他)")
    expense_plan = {"气体": [(3000, 8000), (1500, 4000)], "食堂": [(1500, 3000), (800, 1800)],
                    "佣金": [(5000, 15000), (2000, 6000)], "提成": [(4000, 12000), (1500, 4000)],
                    "融资成本": [(800, 3500), (500, 2500)], "其他": [(1000, 5000), (500, 3000)]}
    total_expense = 0.0
    expense_count = 0
    for mi, (year, month) in enumerate(MONTHS):
        base = 0.7 + 0.5 * mi / (len(MONTHS) - 1)
        for week in range(4):
            for company_id in [1, 2]:
                day = week * 7 + 3 + (company_id % 2)
                if day > 28: continue
                exp_date = datetime(year, month, day)
                rng = random.Random(year * 10000 + month * 100 + week * 10 + company_id)
                cats = rng.sample(list(expense_plan.keys()), 3 if company_id == 1 else 2)
                for cat in cats:
                    min_amt, max_amt = expense_plan[cat][company_id - 1]
                    amt = round((min_amt + (max_amt - min_amt) * rng.random()) * base, 2)
                    fa_id = ACC_ID['JX_BANK'] if company_id == 1 else ACC_ID['DG_BANK']
                    if cat == "食堂" and rng.random() < 0.1 and ACC_ID['CASH']:
                        fa_id = ACC_ID['CASH']
                    db.add(FundFlow(fund_account_id=fa_id, direction="OUT", amount=amt,
                                    expense_category=cat, counterparty="", occur_date=exp_date,
                                    summary=f"{cat}费用", source_type="PAYMENT"))
                    total_expense += amt
                    expense_count += 1
    db.commit()
    print(f"  经营费用: {expense_count}笔, 合计: {total_expense:.2f}元")

    # 第7步: 承兑+现金收入
    print("\n第7步: 承兑汇票+现金零星收入")
    cash_income = 0
    if ACC_ID['CASH']:
        for mi, (year, month) in enumerate(MONTHS):
            for week in range(4):
                day = week * 5 + 3
                if day > 28: continue
                amt = round(800 + 400 * (mi / len(MONTHS)), 2)
                db.add(FundFlow(fund_account_id=ACC_ID['CASH'], direction="IN", amount=amt,
                                counterparty="零星客户", occur_date=datetime(year, month, day),
                                summary="现金收款(零星)", source_type="RECEIPT"))
                cash_income += 1
        db.commit()
        print(f"  现金零星收入: {cash_income}笔")
    acceptance_flows = 0
    for mi, (year, month) in enumerate(MONTHS[2:], 2):
        base = 0.7 + 0.5 * mi / (len(MONTHS) - 1)
        if mi % 2 == 0:
            amt = round((80000 + 40000 * (base - 0.7)) * (1 + (mi % 3)), 2)
            exp_date = datetime(year, month, 20)
            if ACC_ID['ACCEPTANCE']:
                db.add(FundFlow(fund_account_id=ACC_ID['ACCEPTANCE'], direction="IN", amount=amt,
                                counterparty="上海汽车配件有限公司" if mi % 2 == 0 else "无锡机械制造有限公司",
                                occur_date=exp_date, summary=f"收承兑汇票 {amt:.2f}元",
                                source_type="ACCEPTANCE_IN"))
                acceptance_flows += 1
                due_date = exp_date + timedelta(days=90)
                if due_date < now:
                    db.add(FundFlow(fund_account_id=ACC_ID['ACCEPTANCE'], direction="OUT", amount=amt,
                                    counterparty="银行托收", occur_date=due_date,
                                    summary=f"承兑到期托收 {amt:.2f}元", source_type="ACCEPTANCE_SETTLE"))
                    db.add(FundFlow(fund_account_id=ACC_ID['JX_BANK'], direction="IN", amount=amt,
                                    counterparty="承兑到期托收", occur_date=due_date,
                                    summary=f"承兑到期入账 {amt:.2f}元", source_type="ACCEPTANCE_SETTLE"))
                    acceptance_flows += 2
    db.commit()
    print(f"  承兑流水: {acceptance_flows}笔 (含到期托收)")

    # 第8步: 统计
    print("\n" + "=" * 60)
    print("数据生成完成, 最终统计:")
    print(f"  Order: {db.query(Order).count()}条")
    print(f"  OrderItem: {db.query(OrderItem).count()}条")
    print(f"  WorkOrder: {db.query(WorkOrder).count()}条")
    print(f"  Completion: {db.query(Completion).count()}条")
    print(f"  FinanceDoc: {db.query(FinanceDoc).count()}条")
    print(f"  FundFlow: {db.query(FundFlow).count()}条")
    print("\nFundFlow方向分布:")
    for r in db.query(FundFlow.direction, func.count(), func.sum(FundFlow.amount)).group_by(FundFlow.direction).all():
        print(f"  {r[0]}: {r[1]}条, 合计{r[2]:.2f}元")
    print("\nFundFlow账户分布:")
    for r in db.query(FundFlow.fund_account_id, func.count(), func.sum(FundFlow.amount)).group_by(FundFlow.fund_account_id).all():
        acc = db.query(FundAccount).filter(FundAccount.id == r[0]).first()
        print(f"  [{acc.code}] {acc.name}: {r[1]}条, 合计{r[2]:.2f}元")
    print("\nFundFlow expense_category分布(支出):")
    for r in db.query(FundFlow.expense_category, func.count(), func.sum(FundFlow.amount))\
            .filter(FundFlow.expense_category.isnot(None), FundFlow.direction == "OUT")\
            .group_by(FundFlow.expense_category).all():
        print(f"  {r[0]}: {r[1]}条, 合计{r[2]:.2f}元")
    print("\nFinanceDoc类型分布:")
    for r in db.query(FinanceDoc.doc_type, func.count(), func.sum(FinanceDoc.amount), func.sum(FinanceDoc.settled_amount))\
            .group_by(FinanceDoc.doc_type).all():
        print(f"  {r[0]}: {r[1]}条, 金额{r[2]:.2f}, 已结算{r[3]:.2f}")
    print("\n===== 完成 =====")