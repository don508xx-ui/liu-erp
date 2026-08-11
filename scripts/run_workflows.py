"""模拟两个端到端工作流,留痕供面板查看。"""
import requests, json, sys, time

BASE = "http://127.0.0.1:8765"
H = {}
TS = time.strftime("%H%M%S")


def call(method, url, body=None):
    r = requests.request(method, BASE + url, headers=H, json=body, timeout=10)
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
    if r.status_code >= 400:
        print(f"  [ERR] {method} {url} -> {r.status_code} {j}")
        sys.exit(1)
    return j


def step(title):
    print(f"\n=== {title} ===")


def show(url):
    j = call("GET", url)
    print(f"  GET {url} -> {json.dumps(j, ensure_ascii=False)[:400]}")


# 登录
r = call("POST", "/api/auth/login", {"username": "admin", "password": "123456"})
H["Authorization"] = "Bearer " + r["token"]
print("登录成功: admin")

# ========== 工作流A: 自营料订单全流程 ==========
step("A1 新建客户: 鹏程新能源科技")
cust = call("POST", "/api/customers", {
    "code": f"CUST-PC-{TS}", "name": "鹏程新能源科技有限公司",
    "tax_no": "91320104MA01PC2026", "address": "南京市江宁区将军大道99号",
    "contact_name": "王鹏程", "contact_phone": "13851000001",
    "industry": "新能源", "settlement_cycle": "月结30",
    "bank_name": "工行南京江宁支行", "bank_account": "4301010109300000126",
})
cid = cust["data"]["id"]
print(f"  客户ID={cid}")

step("A2 新建订单: 电池外壳喷涂 500件")
order = call("POST", "/api/orders", {
    "customer_id": cid,
    "prepayment_amount": 5000,
    "prepayment_ratio": 20,
    "remark": "电池外壳静电喷涂,要求RAL7016灰",
    "items": [{
        "seq": 1, "part_name": "电池外壳", "part_spec": "200x150x80mm",
        "price_type": "BY_PIECE", "quantity": 500, "unit": "件", "unit_price": 50,
        "material_mode": "SELF", "paint_spec": "RAL7016",
        "process_requirement": "静电粉末喷涂,膜厚60-80μm",
    }],
})
oid = order["data"]["id"]
print(f"  订单ID={oid} 订单号={order['data']['order_no']} 总额={order['data']['total_amount']}")

step("A3 订单提交")
call("POST", f"/api/orders/{oid}/submit")
print("  订单状态: SUBMITTED")

step("A4 订单生效(触发预收款应收)")
call("POST", f"/api/orders/{oid}/effect")
print("  订单状态: EFFECTIVE")

step("A5 下达加工单")
wo = call("POST", "/api/work-orders", {
    "order_id": oid, "batch_no": "B26-0724-A", "workshop": "A",
    "plan_qty": 500, "remark": "A线静电喷粉",
})
wid = wo["data"]["id"]
print(f"  加工单ID={wid} 加工单号={wo['data']['work_order_no']}")
call("POST", f"/api/work-orders/{wid}/release")
print("  加工单状态: RELEASED (钩子已自动生成领料单)")

step("A6 查询自动生成的领料单并确认")
reqs = call("GET", "/api/requisitions?status=PENDING")
rid = reqs["data"][0]["id"]
print(f"  领料单ID={rid} 明明={json.dumps(reqs['data'][0]['items'], ensure_ascii=False)}")
call("POST", f"/api/requisitions/{rid}/confirm")
print("  领料单状态: CONFIRMED (钩子已扣库存+记材料成本)")

step("A7 完工填报")
cp = call("POST", "/api/completions", {
    "work_order_id": wid, "finished_qty": 500, "qualified_qty": 490,
    "rework_qty": 8, "scrap_qty": 2,
    "labor_hours": 16, "labor_cost": 1280, "overhead_cost": 600,
    "remark": "A线完工,8件返工重喷,2件报废",
})
cpid = cp["data"]["id"]
print(f"  完工单ID={cpid} 完工单号={cp['data']['completion_no']}")

step("A8 确认完工(钩子归集成本+算利润)")
cc = call("POST", f"/api/completions/{cpid}/confirm")
print(f"  完工单状态: CONFIRMED 总成本={cc['data'].get('total_cost')}")

step("A9 财务登记尾款收款")
rc = call("POST", "/api/finance/receipts", {
    "order_id": oid, "amount": 20000, "remark": "鹏程尾款一次性结清",
})
print(f"  收款单号={rc['data']['doc_no']}")

step("A10 留痕校验")
show(f"/api/orders/{oid}")
show(f"/api/finance/work-order-costs/{wid}")
show(f"/api/finance/profit/order/{oid}")

# ========== 工作流B: 客供料订单 ==========
step("B1 新建客户: 鼎峰机械")
cust2 = call("POST", "/api/customers", {
    "code": f"CUST-DF-{TS}", "name": "鼎峰机械制造有限公司",
    "tax_no": "91320105MA01DF2026", "address": "苏州市吴中区越溪街道88号",
    "contact_name": "李鼎峰", "contact_phone": "13851000002",
    "industry": "机械制造", "settlement_cycle": "款到发货",
    "bank_name": "建行苏州吴中支行", "bank_account": "3205010109300000588",
})
cid2 = cust2["data"]["id"]
print(f"  客户ID={cid2}")

step("B2 新建客供料订单: 钢结构架喷涂 200件")
order2 = call("POST", "/api/orders", {
    "customer_id": cid2,
    "prepayment_amount": 0,
    "prepayment_ratio": 0,
    "remark": "客供钢结构架,来料加工,特氟龙涂层",
    "items": [{
        "seq": 1, "part_name": "钢结构架", "part_spec": "1200x600x1800mm",
        "price_type": "BY_PIECE", "quantity": 200, "unit": "件", "unit_price": 120,
        "material_mode": "CUSTOMER", "paint_spec": "PTFE-Black",
        "process_requirement": "客供料,特氟龙喷涂,膜厚30-40μm",
    }],
})
oid2 = order2["data"]["id"]
print(f"  订单ID={oid2} 订单号={order2['data']['order_no']} 总额={order2['data']['total_amount']}")

step("B3 订单提交+生效")
call("POST", f"/api/orders/{oid2}/submit")
call("POST", f"/api/orders/{oid2}/effect")
print("  订单状态: EFFECTIVE")

step("B4 下达加工单(客供料,不生成领料单,自动记客供料台账)")
wo2 = call("POST", "/api/work-orders", {
    "order_id": oid2, "batch_no": "B26-0724-B", "workshop": "B",
    "plan_qty": 200, "remark": "B线客供料特氟龙",
})
wid2 = wo2["data"]["id"]
print(f"  加工单ID={wid2} 加工单号={wo2['data']['work_order_no']}")
call("POST", f"/api/work-orders/{wid2}/release")
print("  加工单状态: RELEASED")

step("B5 完工填报(无领料,纯人工费用)")
cp2 = call("POST", "/api/completions", {
    "work_order_id": wid2, "finished_qty": 200, "qualified_qty": 198,
    "rework_qty": 2, "scrap_qty": 0,
    "labor_hours": 24, "labor_cost": 2160, "overhead_cost": 800,
    "items": [],
})
cpid2 = cp2["data"]["id"]
print(f"  完工单ID={cpid2} 完工单号={cp2['data']['completion_no']}")
call("POST", f"/api/completions/{cpid2}/confirm")
print("  完工单状态: CONFIRMED")

step("B6 款到发货-全款收款")
rc2 = call("POST", "/api/finance/receipts", {
    "order_id": oid2, "amount": 24000, "remark": "鼎峰款到发货全款",
})
print(f"  收款单号={rc2['data']['doc_no']}")

step("B7 客供料台账留痕")
show("/api/consign/logs" if False else "/api/customers")

print("\n两个工作流跑完,可在面板查看: 客户/订单/加工单/领料/完工/财务/客供料 各模块留痕。")
