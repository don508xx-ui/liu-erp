"""V2 演示数据流 - 建订单→生效→下工单→完工→送货→调价,填充分析/预警数据"""
import json, urllib.request

BASE = "http://127.0.0.1:8000"


def call(method, path, token=None, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "msg": e.read().decode()[:300]}


def login(u, p):
    return call("POST", "/api/auth/login", body={"username": u, "password": p}).get("token")


def main():
    tk = login("admin", "123456")
    print("admin login:", "OK" if tk else "FAIL")

    # 3个订单: 不同公司主体/开票类型
    orders = [
        {"customer_id": 1, "company_id": 1, "billing_type": "SPECIAL_VAT", "prepayment_amount": 10000,
         "items": [{"part_name": "储线轮", "part_spec": "FL-200", "price_type": "BY_PIECE", "quantity": 50, "unit": "件", "unit_price": 800, "material_mode": "SELF", "paint_spec": "NI60A"}]},
        {"customer_id": 2, "company_id": 1, "billing_type": "NORMAL", "prepayment_amount": 20000,
         "items": [{"part_name": "导轮", "part_spec": "DL-150", "price_type": "BY_AREA", "quantity": 120, "unit": "m²", "unit_price": 350, "material_mode": "SELF", "paint_spec": "碳化钨"}]},
        {"customer_id": 3, "company_id": 2, "billing_type": "CASH", "prepayment_amount": 0,
         "items": [{"part_name": "滚筒", "part_spec": "GT-300", "price_type": "BY_PIECE", "quantity": 20, "unit": "件", "unit_price": 1200, "material_mode": "CUSTOMER", "paint_spec": "特氟龙黑"}]},
    ]
    oids = []
    for i, o in enumerate(orders):
        r = call("POST", "/api/orders", tk, o)
        if "error" in r:
            print(f"  订单{i+1}创建FAIL: {r['msg'][:100]}")
            continue
        oid = r["data"]["id"]
        oids.append(oid)
        print(f"  订单{i+1}创建OK: id={oid} total={r['data']['total_amount']}")
        # 提交+生效
        call("POST", f"/api/orders/{oid}/submit", tk)
        r = call("POST", f"/api/orders/{oid}/effect", tk)
        print(f"    生效: {r.get('data', {}).get('status', r)}")

    # 下工单 + 完工(前2个订单)
    for oid in oids[:2]:
        r = call("GET", f"/api/orders/{oid}", tk)
        items = r["data"].get("items", [])
        if not items:
            continue
        wo = {"order_id": oid, "order_item_id": items[0]["id"], "workshop": "A线",
              "plan_qty": items[0]["quantity"], "part_name": items[0]["part_name"]}
        r = call("POST", "/api/work-orders", tk, wo)
        if "error" in r:
            print(f"  工单FAIL: {r['msg'][:100]}")
            continue
        wid = r["data"]["id"]
        print(f"  工单OK: id={wid} order={oid}")
        # 下达
        call("POST", f"/api/work-orders/{wid}/release", tk)
        # 领料确认
        reqs = call("GET", "/api/requisitions", tk).get("data", [])
        for rq in reqs:
            if rq.get("work_order_id") == wid:
                call("POST", f"/api/requisitions/{rq['id']}/confirm", tk)
                break
        # 完工
        cp = {"work_order_id": wid, "qualified_qty": wo["plan_qty"], "defect_qty": 0,
              "labor_cost": 800, "overhead_cost": 500, "items": []}
        r = call("POST", "/api/completions", tk, cp)
        if "error" in r:
            print(f"  完工FAIL: {r['msg'][:100]}")
        else:
            cid = r["data"]["id"]
            call("POST", f"/api/completions/{cid}/confirm", tk)
            print(f"  完工OK: id={cid}")

    # 送货单 + 发货(订单1)
    if oids:
        oid = oids[0]
        r = call("GET", f"/api/orders/{oid}", tk)
        items = r["data"].get("items", [])
        dn = {"order_id": oid, "delivery_address": "客户工厂",
              "items": [{"order_item_id": it["id"], "part_name": it["part_name"], "qty": it["quantity"], "unit": it["unit"], "unit_price": it["unit_price"]} for it in items]}
        r = call("POST", "/api/deliveries", tk, dn)
        if "error" not in r:
            did = r["data"]["id"]
            call("POST", f"/api/deliveries/{did}/ship", tk)
            print(f"  送货单OK+发货: id={did}")
        else:
            print(f"  送货FAIL: {r['msg'][:100]}")

    # 调价申请(订单2) + 审批
    if len(oids) >= 2:
        oid = oids[1]
        r = call("GET", f"/api/orders/{oid}", tk)
        total = r["data"]["total_amount"]
        adj = {"order_id": oid, "original_amount": total, "adjusted_amount": total * 0.9, "reason": "客户议价让利10%"}
        r = call("POST", "/api/adjustments", tk, adj)
        if "error" not in r:
            aid = r["data"]["id"]
            # GM审批
            tkg = login("gm01", "123456")
            call("POST", f"/api/adjustments/{aid}/approve", tkg)
            print(f"  调价申请OK+审批: id={aid}")
        else:
            print(f"  调价FAIL: {r['msg'][:100]}")

    # 来货登记
    rcv = {"customer_id": 1, "order_id": oids[0] if oids else None,
           "part_name": "储线轮", "part_spec": "FL-200", "qty": 50, "unit": "件", "process_requirement": "镜面喷瓷"}
    r = call("POST", "/api/receiving", tk, rcv)
    print(f"  来货登记: {'OK' if 'error' not in r else r['msg'][:80]}")

    print("\n=== 演示数据填充完成,验证分析 ===")
    # 透视验证
    r = call("POST", "/api/analysis/pivot", tk, {"dataset": "orders", "rows_dim": "status", "metric": "total_amount", "agg": "sum"})
    if "error" not in r:
        d = r["data"]
        print(f"  透视(订单×状态): rows={len(d.get('row_keys', []))} table={len(d.get('table', []))}")
    r = call("POST", "/api/analysis/pivot", tk, {"dataset": "finance_docs", "rows_dim": "created_at:month", "cols_dim": "doc_type", "metric": "amount", "agg": "sum"})
    if "error" not in r:
        d = r["data"]
        print(f"  透视(财务月×类型): rows={len(d.get('row_keys', []))} cols={len(d.get('col_keys', []))}")
    r = call("GET", "/api/analysis/receivable-aging", tk)
    if "error" not in r:
        print(f"  应收账龄total: {r['data']['summary']['total']}")
    r = call("GET", "/api/analysis/company-revenue", tk)
    if "error" not in r:
        for c in r["data"]:
            print(f"  公司{c['code']}: 应收{c['receivable']} 收款{c['receipt']}")


if __name__ == "__main__":
    main()
