"""全流程测试 - 覆盖业财联动主链+采购+工资+分析+Agent+RBAC"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import login, auth_headers


# ============ 认证 ============

def test_login_admin(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
    assert r.status_code == 200
    assert "token" in r.json()


def test_login_invalid_password(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_login_unknown_user(client):
    r = client.post("/api/auth/login", json={"username": "nobody", "password": "123456"})
    assert r.status_code == 401


def test_me(client):
    t = login(client, "admin")
    r = client.get("/api/auth/me", headers=auth_headers(t))
    assert r.status_code == 200
    assert r.json()["username"] == "admin"


def test_me_no_token(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


# ============ 客户 ============

def test_create_customer(client):
    t = login(client, "sales01")
    r = client.post("/api/customers", headers=auth_headers(t), json={
        "code": "CUST-100", "name": "测试客户A", "contact_name": "X", "contact_phone": "1",
        "industry": "汽配", "settlement_cycle": "月结30",
    })
    assert r.status_code == 200
    assert r.json()["data"]["code"] == "CUST-100"


def test_create_customer_dup_code(client):
    t = login(client, "sales01")
    client.post("/api/customers", headers=auth_headers(t), json={"code": "CUST-200", "name": "X"})
    r = client.post("/api/customers", headers=auth_headers(t), json={"code": "CUST-200", "name": "Y"})
    assert r.status_code == 400


def test_list_customers(client):
    t = login(client, "sales01")
    r = client.get("/api/customers", headers=auth_headers(t))
    assert r.status_code == 200
    assert r.json()["total"] >= 3  # seed的3个


def test_get_customer(client):
    t = login(client, "sales01")
    r = client.get("/api/customers/1", headers=auth_headers(t))
    assert r.status_code == 200


def test_update_customer(client):
    t = login(client, "sales01")
    r = client.put("/api/customers/1", headers=auth_headers(t), json={
        "code": "CUST-001", "name": "上海某汽配有限公司(改)", "contact_name": "陈经理"
    })
    assert r.status_code == 200


# ============ 订单全流程 ============

def _create_order(client, token, customer_id=1, material_mode="SELF", prepayment=0):
    payload = {
        "customer_id": customer_id,
        "prepayment_amount": prepayment,
        "prepayment_ratio": 30 if prepayment else 0,
        "items": [{
            "seq": 1, "part_name": "汽车配件A", "part_spec": "100x50x20mm",
            "price_type": "BY_AREA", "quantity": 100, "unit": "m²", "unit_price": 25,
            "material_mode": material_mode, "paint_spec": "PTFE-Black-1kg",
            "process_requirement": "喷涂特氟龙,膜厚20um",
        }],
    }
    r = client.post("/api/orders", headers=auth_headers(token), json=payload)
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def test_create_order(client):
    t = login(client, "sales01")
    oid = _create_order(client, t, prepayment=750)
    assert oid > 0


def test_submit_order(client):
    t = login(client, "sales01")
    oid = _create_order(client, t)
    r = client.post(f"/api/orders/{oid}/submit", headers=auth_headers(t))
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "SUBMITTED"


def test_effect_order_creates_receivable(client):
    """订单生效→自动建应收草稿+预收款核销+通知"""
    t = login(client, "sales01")
    oid = _create_order(client, t, prepayment=750)  # 总额2500,预收750
    client.post(f"/api/orders/{oid}/submit", headers=auth_headers(t))
    ops = login(client, "ops01")
    r = client.post(f"/api/orders/{oid}/effect", headers=auth_headers(ops))
    assert r.status_code == 200
    # 查应收
    fin = login(client, "fin01")
    docs = client.get("/api/finance/docs?doc_type=RECEIVABLE", headers=auth_headers(fin)).json()["data"]
    ar = next(d for d in docs if d["related_id"] == oid)
    assert ar["status"] == "DRAFT"
    assert float(ar["amount"]) == 2500
    assert float(ar["settled_amount"]) == 750  # 预收款已自动核销
    # 查收款单
    rcs = client.get("/api/finance/docs?doc_type=RECEIPT", headers=auth_headers(fin)).json()["data"]
    assert any(r["related_id"] == oid for r in rcs)
    # 通知已发(运营+GM)
    logs = client.get("/api/notifications/logs", headers=auth_headers(fin)).json()["data"]
    assert any(l["template_code"] == "order.effective.notice" for l in logs)


def test_return_order(client):
    """运营退单→订单回RETURNED,销售可重提"""
    t = login(client, "sales01")
    oid = _create_order(client, t)
    client.post(f"/api/orders/{oid}/submit", headers=auth_headers(t))
    ops = login(client, "ops01")
    r = client.post(f"/api/orders/{oid}/return", headers=auth_headers(ops), json={"reason": "客户料不足"})
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "RETURNED"
    # 销售可重新提交
    r2 = client.post(f"/api/orders/{oid}/submit", headers=auth_headers(t))
    assert r2.status_code == 200
    assert r2.json()["data"]["status"] == "SUBMITTED"


def test_rbac_sales_cannot_effect(client):
    """销售无生效权限"""
    t = login(client, "sales01")
    oid = _create_order(client, t)
    client.post(f"/api/orders/{oid}/submit", headers=auth_headers(t))
    r = client.post(f"/api/orders/{oid}/effect", headers=auth_headers(t))
    assert r.status_code == 403


# ============ 加工单 + 领料 + 完工 ============

def _full_chain_to_completion(client, material_mode="SELF"):
    """跑通:订单→生效→加工单→下达→领料→完工确认,返回completion_id"""
    sales = login(client, "sales01")
    oid = _create_order(client, sales, material_mode=material_mode, prepayment=0)
    client.post(f"/api/orders/{oid}/submit", headers=auth_headers(sales))
    ops = login(client, "ops01")
    client.post(f"/api/orders/{oid}/effect", headers=auth_headers(ops))

    # 取订单明细id,传给加工单(客供料台账判断依赖order_item_id)
    od = client.get(f"/api/orders/{oid}", headers=auth_headers(ops)).json()["data"]
    order_item_id = od["items"][0]["id"] if od.get("items") else None

    r = client.post("/api/work-orders", headers=auth_headers(ops), json={
        "order_id": oid, "order_item_id": order_item_id,
        "batch_no": f"BATCH-{oid}", "workshop": "A",
        "plan_qty": 100, "work_manager_user_id": 7,
    })
    wid = r.json()["data"]["id"]
    r = client.post(f"/api/work-orders/{wid}/release", headers=auth_headers(ops))
    assert r.status_code == 200

    # 客供料模式:不生成领料单,直接返回(不跑领料完工)
    if material_mode == "CUSTOMER":
        return oid, wid, None

    # 自营料:领料单已自动生成
    reqs = client.get("/api/requisitions?status=PENDING", headers=auth_headers(ops)).json()["data"]
    assert any(rq["work_order_id"] == wid for rq in reqs)
    req_id = next(rq["id"] for rq in reqs if rq["work_order_id"] == wid)

    # 仓管确认领料
    wh = login(client, "wh01")
    r = client.post(f"/api/requisitions/{req_id}/confirm", headers=auth_headers(wh))
    assert r.status_code == 200, r.text

    # 厂长填完工
    mgr = login(client, "mgr_a")
    r = client.post("/api/completions", headers=auth_headers(mgr), json={
        "work_order_id": wid, "finished_qty": 100, "qualified_qty": 95,
        "rework_qty": 3, "scrap_qty": 2,
        "labor_hours": 8, "labor_cost": 400, "overhead_cost": 200,
        "items": [{
            "item_id": 1, "item_name": "特氟龙PTFE涂料(黑)",
            "theoretical_qty": 15, "actual_qty": 18, "return_qty": 2, "unit_cost": 380,
        }],
    })
    cid = r.json()["data"]["id"]
    # 运营确认
    r = client.post(f"/api/completions/{cid}/confirm", headers=auth_headers(ops))
    assert r.status_code == 200, r.text
    return oid, wid, cid


def test_full_chain_material_self(client):
    """自营料全链路:验证材料成本+成品入库+利用率+应收转OPEN"""
    oid, wid, cid = _full_chain_to_completion(client, material_mode="SELF")
    fin = login(client, "fin01")

    # 应收转OPEN
    docs = client.get("/api/finance/docs?doc_type=RECEIVABLE", headers=auth_headers(fin)).json()["data"]
    ar = next(d for d in docs if d["related_id"] == oid)
    assert ar["status"] == "OPEN"

    # 工单成本汇总
    costs = client.get(f"/api/finance/work-order-costs/{wid}", headers=auth_headers(fin)).json()["data"]
    # 材料15kg*380=6840? 不对:actual_qty=18*380=6840, 但领料按理论15kg扣库存,成本按领料单actual=15
    # 实际:领料单theoretical=15kg,actual=15, cost=15*380=5700; 退料2kg回冲库存
    assert costs["total_cost"] > 0
    assert "MATERIAL" in costs["breakdown"]
    assert "LABOR" in costs["breakdown"]
    assert "OVERHEAD" in costs["breakdown"]
    # 人工+制造费用=400+200=600
    assert costs["breakdown"]["LABOR"] == 400
    assert costs["breakdown"]["OVERHEAD"] == 200

    # 利润分析
    profit = client.get(f"/api/finance/profit/order/{oid}", headers=auth_headers(fin)).json()["data"]
    assert profit["revenue"] == 2500
    assert profit["cost"] == costs["total_cost"]
    assert profit["profit"] == profit["revenue"] - profit["cost"]

    # 完工单利用率
    cps = client.get("/api/completions", headers=auth_headers(fin)).json()["data"]
    cp = next(c for c in cps if c["id"] == cid)
    util = cp["items"][0]["utilization_rate"]
    assert util == round(15 / 18 * 100, 2)  # 理论/实际

    # 成品入库(库存增加95件)
    items = client.get("/api/inventory/items?category=FINISHED_GOOD", headers=auth_headers(fin)).json()["data"]
    assert float(items[0]["stock_qty"]) >= 95


def test_full_chain_customer_material(client):
    """客供料全链路:验证客供料台账,不生成领料单,不入库存账"""
    oid, wid, cid = _full_chain_to_completion(client, material_mode="CUSTOMER")
    ops = login(client, "ops01")
    # 客供料台账
    cl = client.get("/api/inventory/consign-log", headers=auth_headers(ops)).json()["data"]
    assert any(c["work_order_id"] == wid for c in cl)
    # 不应有领料单
    reqs = client.get("/api/requisitions", headers=auth_headers(ops)).json()["data"]
    assert not any(r["work_order_id"] == wid for r in reqs)


def test_completion_notify_sales(client):
    """完工确认→通知销售催款"""
    oid, wid, cid = _full_chain_to_completion(client)
    fin = login(client, "fin01")
    logs = client.get("/api/notifications/logs", headers=auth_headers(fin)).json()["data"]
    assert any(l["template_code"] == "completion.confirmed.notice" for l in logs)
    assert any(l["template_code"] == "payment.remind" for l in logs)


# ============ 库存 ============

def test_list_inventory_items(client):
    t = login(client, "wh01")
    r = client.get("/api/inventory/items", headers=auth_headers(t))
    assert r.status_code == 200
    assert len(r.json()["data"]) >= 5


def test_create_inventory_item(client):
    t = login(client, "wh01")
    r = client.post("/api/inventory/items", headers=auth_headers(t), json={
        "code": "MAT-999", "name": "测试物料", "unit": "kg", "category": "CONSUMABLE", "stock_qty": 10,
    })
    assert r.status_code == 200


def test_requisition_insufficient_stock(client):
    """库存不足应拒"""
    sales = login(client, "sales01")
    oid = _create_order(client, sales, prepayment=0)
    client.post(f"/api/orders/{oid}/submit", headers=auth_headers(sales))
    ops = login(client, "ops01")
    client.post(f"/api/orders/{oid}/effect", headers=auth_headers(ops))
    # 加工单plan_qty设很大,触发领料超库存
    r = client.post("/api/work-orders", headers=auth_headers(ops), json={
        "order_id": oid, "batch_no": f"BIG-{oid}", "workshop": "A",
        "plan_qty": 999999, "work_manager_user_id": 7,
    })
    wid = r.json()["data"]["id"]
    client.post(f"/api/work-orders/{wid}/release", headers=auth_headers(ops))
    reqs = client.get("/api/requisitions?status=PENDING", headers=auth_headers(ops)).json()["data"]
    req_id = next(rq["id"] for rq in reqs if rq["work_order_id"] == wid)
    wh = login(client, "wh01")
    r = client.post(f"/api/requisitions/{req_id}/confirm", headers=auth_headers(wh))
    assert r.status_code == 400


# ============ 财务 ============

def test_create_receipt_settles_receivable(client):
    """收款核销应收"""
    oid, wid, cid = _full_chain_to_completion(client)
    fin = login(client, "fin01")
    # 收2500(全额)
    r = client.post("/api/finance/receipts", headers=auth_headers(fin), json={
        "order_id": oid, "amount": 2500,
    })
    assert r.status_code == 200
    docs = client.get("/api/finance/docs?doc_type=RECEIVABLE", headers=auth_headers(fin)).json()["data"]
    ar = next(d for d in docs if d["related_id"] == oid)
    assert ar["status"] == "SETTLED"
    assert float(ar["settled_amount"]) == 2500


def test_receivables_aging(client):
    fin = login(client, "fin01")
    r = client.get("/api/finance/receivables/aging", headers=auth_headers(fin))
    assert r.status_code == 200
    assert "0-30" in r.json()["data"]


def test_finance_accounts(client):
    fin = login(client, "fin01")
    r = client.get("/api/finance/accounts", headers=auth_headers(fin))
    assert r.status_code == 200
    assert len(r.json()["data"]) >= 10


def test_finance_docs_filter(client):
    fin = login(client, "fin01")
    r = client.get("/api/finance/docs?doc_type=RECEIVABLE", headers=auth_headers(fin))
    assert r.status_code == 200
    for d in r.json()["data"]:
        assert d["doc_type"] == "RECEIVABLE"


# ============ 采购 ============

def test_purchase_request_flow(client):
    """采购申请→提交→审批→通过"""
    wh = login(client, "wh01")
    r = client.post("/api/purchase-requests", headers=auth_headers(wh), json={
        "items": [{"name": "稀释剂", "qty": 50, "unit": "L", "est_price": 35}],
        "reason": "库存不足",
    })
    pid = r.json()["data"]["id"]
    r = client.post(f"/api/purchase-requests/{pid}/submit", headers=auth_headers(wh))
    assert r.status_code == 200
    # GM审批
    gm = login(client, "gm01")
    tasks = client.get("/api/approvals/tasks/pending", headers=auth_headers(gm)).json()["data"]
    assert len(tasks) > 0
    tid = tasks[0]["id"]
    r = client.post(f"/api/approvals/tasks/{tid}/handle", headers=auth_headers(gm), json={"action": "approve", "comment": "ok"})
    assert r.status_code == 200
    # 申请状态变APPROVED
    prs = client.get("/api/purchase-requests", headers=auth_headers(wh)).json()["data"]
    pr = next(p for p in prs if p["id"] == pid)
    assert pr["status"] == "APPROVED"


def test_purchase_receive_creates_payable(client):
    """PO入库→自动建应付+库存增加"""
    fin = login(client, "fin01")
    r = client.post("/api/purchases/suppliers", headers=auth_headers(fin), json={
        "code": "SUP-999", "name": "测试供应商", "contact": "X", "phone": "1",
    })
    r = client.post("/api/purchases", headers=auth_headers(fin), json={
        "supplier_id": 1, "items": [{"item_id": 1, "item_name": "特氟龙涂料", "qty": 10, "unit": "kg", "unit_price": 380}],
    })
    pid = r.json()["data"]["id"]
    client.post(f"/api/purchases/{pid}/order", headers=auth_headers(fin))
    # 入库前库存
    before = client.get("/api/inventory/items", headers=auth_headers(fin)).json()["data"]
    before_qty = float(next(i for i in before if i["id"] == 1)["stock_qty"])
    wh = login(client, "wh01")
    r = client.post(f"/api/purchases/{pid}/receive", headers=auth_headers(wh))
    assert r.status_code == 200
    # 库存增加10
    after = client.get("/api/inventory/items", headers=auth_headers(fin)).json()["data"]
    after_qty = float(next(i for i in after if i["id"] == 1)["stock_qty"])
    assert after_qty == before_qty + 10
    # 应付已生成
    aps = client.get("/api/finance/docs?doc_type=PAYABLE", headers=auth_headers(fin)).json()["data"]
    assert any(a["related_id"] == pid for a in aps)


def test_list_suppliers(client):
    t = login(client, "fin01")
    r = client.get("/api/purchases/suppliers", headers=auth_headers(t))
    assert r.status_code == 200


# ============ 工资 ============

def test_payroll_flow(client):
    fin = login(client, "fin01")
    r = client.post("/api/payroll", headers=auth_headers(fin), json={
        "period": "2026-07",
        "items": [
            {"employee_id": 2, "name": "张销售", "position": "销售", "amount": 8000},
            {"employee_id": 3, "name": "王运营", "position": "运营", "amount": 9000},
        ],
    })
    pid = r.json()["data"]["id"]
    assert float(r.json()["data"]["total"]) == 17000
    r = client.post(f"/api/payroll/{pid}/confirm", headers=auth_headers(fin))
    assert r.status_code == 200
    # 付款单已生成
    pys = client.get("/api/finance/docs?doc_type=PAYMENT", headers=auth_headers(fin)).json()["data"]
    assert any(p["related_type"] == "PAYROLL" for p in pys)


# ============ 分析预警 ============

def test_kpi(client):
    t = login(client, "gm01")
    r = client.get("/api/analysis/kpi", headers=auth_headers(t))
    assert r.status_code == 200
    d = r.json()["data"]
    assert "revenue" in d and "cost" in d and "gross_margin_pct" in d


def test_alert_rules_crud(client):
    t = login(client, "fin01")
    r = client.post("/api/analysis/alert-rules", headers=auth_headers(t), json={
        "code": "TEST_ALERT", "name": "测试预警", "metric": "STOCK_LOW",
        "condition": {"op": ">", "value": 0}, "channels": ["INAPP"], "recipients": ["GM"],
    })
    assert r.status_code == 200
    r = client.get("/api/analysis/alert-rules", headers=auth_headers(t))
    assert any(ru["code"] == "TEST_ALERT" for ru in r.json()["data"])


def test_check_alerts(client):
    t = login(client, "fin01")
    r = client.post("/api/analysis/alert-rules/check", headers=auth_headers(t))
    assert r.status_code == 200


def test_paint_utilization_report(client):
    _full_chain_to_completion(client)
    t = login(client, "ops01")
    r = client.get("/api/analysis/paint-utilization", headers=auth_headers(t))
    assert r.status_code == 200
    assert len(r.json()["data"]) > 0


def test_order_profit_after_completion(client):
    oid, wid, cid = _full_chain_to_completion(client)
    fin = login(client, "fin01")
    r = client.get(f"/api/finance/profit/order/{oid}", headers=auth_headers(fin))
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["revenue"] > 0 and d["cost"] > 0


# ============ Agent API ============

def _make_agent_token(client):
    """直接DB插入agent token,返回明文token"""
    from app.core.db import db_scope
    from app.models.system import AgentApiToken
    from app.core.auth import hash_password
    import secrets
    raw = f"agent:test:{secrets.token_hex(16)}"
    with db_scope() as db:
        db.add(AgentApiToken(name="test-agent", token_hash=hash_password(raw),
                             scopes=["read:*", "write:alert_rules", "write:report_templates",
                                     "write:flow_definitions", "read:analysis", "write:migration_proposal"],
                             status="ACTIVE"))
        db.commit()
    return raw


def test_agent_query(client):
    token = _make_agent_token(client)
    r = client.post("/api/agent/v1/query", headers=auth_headers(token), json={
        "table": "customers", "fields": ["id", "name"], "limit": 5,
    })
    assert r.status_code == 200
    assert len(r.json()["data"]) > 0


def test_agent_query_forbidden_table(client):
    token = _make_agent_token(client)
    r = client.post("/api/agent/v1/query", headers=auth_headers(token), json={
        "table": "users", "fields": "*", "limit": 5,
    })
    assert r.status_code == 403


def test_agent_create_alert_rule(client):
    token = _make_agent_token(client)
    r = client.post("/api/agent/v1/alert-rules", headers=auth_headers(token), json={
        "code": "AGENT_ALERT_1", "name": "Agent建的预警", "metric": "STOCK_LOW",
        "condition": {"op": ">", "value": 0}, "channels": ["INAPP"], "recipients": ["GM"],
    })
    assert r.status_code == 200


def test_agent_schema(client):
    token = _make_agent_token(client)
    r = client.get("/api/agent/v1/schema", headers=auth_headers(token))
    assert r.status_code == 200
    assert "orders" in r.json()["data"]
    assert "users" not in r.json()["data"]  # 敏感表不在schema


def test_agent_migration_proposal(client):
    token = _make_agent_token(client)
    r = client.post("/api/agent/v1/migration/propose", headers=auth_headers(token), json={
        "name": "add_xxx_table", "sql": "CREATE TABLE xxx(...)",
    })
    assert r.status_code == 200


def test_agent_no_token_rejected(client):
    r = client.get("/api/agent/v1/schema")
    assert r.status_code == 401


# ============ 审计 ============

def test_audit_log_recorded(client):
    """订单状态变更应记审计"""
    from app.core.db import db_scope
    from app.models.system import AuditLog
    sales = login(client, "sales01")
    oid = _create_order(client, sales)
    client.post(f"/api/orders/{oid}/submit", headers=auth_headers(sales))
    with db_scope() as db:
        logs = db.query(AuditLog).filter(AuditLog.entity_type == "order", AuditLog.entity_id == oid).all()
        assert len(logs) >= 2  # create + state_change


# ============ 通知 ============

def test_notification_logs(client):
    t = login(client, "gm01")
    r = client.get("/api/notifications/logs", headers=auth_headers(t))
    assert r.status_code == 200


def test_notification_templates(client):
    t = login(client, "gm01")
    r = client.get("/api/notifications/templates", headers=auth_headers(t))
    assert r.status_code == 200
    assert len(r.json()["data"]) >= 5


# ============ 状态机校验 ============

def test_order_invalid_state_transition(client):
    """DRAFT状态不可直接effect"""
    t = login(client, "sales01")
    oid = _create_order(client, t)
    ops = login(client, "ops01")
    r = client.post(f"/api/orders/{oid}/effect", headers=auth_headers(ops))
    assert r.status_code == 400


def test_work_order_invalid_release(client):
    """非CREATED状态不可下达"""
    sales = login(client, "sales01")
    oid = _create_order(client, sales)
    client.post(f"/api/orders/{oid}/submit", headers=auth_headers(sales))
    ops = login(client, "ops01")
    client.post(f"/api/orders/{oid}/effect", headers=auth_headers(ops))
    r = client.post("/api/work-orders", headers=auth_headers(ops), json={
        "order_id": oid, "batch_no": "X", "workshop": "A", "plan_qty": 10,
    })
    wid = r.json()["data"]["id"]
    client.post(f"/api/work-orders/{wid}/release", headers=auth_headers(ops))
    r2 = client.post(f"/api/work-orders/{wid}/release", headers=auth_headers(ops))
    assert r2.status_code == 400


def test_completion_invalid_confirm(client):
    """非DRAFT完工单不可确认"""
    oid, wid, cid = _full_chain_to_completion(client)
    ops = login(client, "ops01")
    r = client.post(f"/api/completions/{cid}/confirm", headers=auth_headers(ops))
    assert r.status_code == 400  # 已CONFIRMED


# ============ 事件钩子信息 ============

def test_hooks_registered(client):
    r = client.get("/api/hooks")
    assert r.status_code == 200
    hooks = r.json()
    assert "order.effective" in hooks
    assert "work_order.released" in hooks
    assert "completion.confirmed" in hooks
    assert "material.confirmed" in hooks
    assert "purchase.received" in hooks
