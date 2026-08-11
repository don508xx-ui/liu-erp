"""
角色模拟: 采购来单→出货全链路 (core_production 11节点)
每个角色登录后查看待办→审批→验证状态变化
"""
import requests, json, sys, time

BASE = "http://127.0.0.1:8001"
SEP = "=" * 56
INSTANCE_ID = None  # 当前审批实例ID


def login(username, password="123456"):
    r = requests.post(f"{BASE}/api/auth/login", json={"username": username, "password": password}, timeout=10)
    assert r.status_code == 200, f"登录失败({username}): {r.text}"
    data = r.json()
    token = data["token"]
    role = data.get("user", {}).get("role", "?")
    name = data.get("user", {}).get("name", username)
    print(f"  ✅ 登录 {name}({username}) → {role}")
    return token, {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def find_my_task(h, role_name, expected_instance_id=None):
    """查找待办任务, 优先匹配当前审批实例ID"""
    r = requests.get(f"{BASE}/api/approvals/tasks/pending", headers=h, timeout=10)
    data = r.json().get("data", [])
    if not data:
        print(f"  ℹ️ {role_name} 暂无待办")
        return None
    print(f"  📋 {role_name} 待办任务 ({len(data)} 条):")
    for t in data:
        dur = t.get("duration", "")
        dur_str = f" [{dur}]" if dur else ""
        marker = " ← 本流程" if expected_instance_id and t.get("instance_id") == expected_instance_id else ""
        print(f"      ID={t['id']} {t['node_name']} | {t['biz_title']} {t['biz_no']}{dur_str}{marker}")
    # 优先匹配本流程任务
    if expected_instance_id:
        for t in data:
            if t.get("instance_id") == expected_instance_id:
                return t
    return data[0]  # 无匹配则取第一个


def approve_task(h, task, role_name, comment="同意"):
    if not task:
        return None
    tid = task["id"]
    instance_id = task.get("instance_id")
    r = requests.post(f"{BASE}/api/approvals/tasks/{tid}/handle",
                      headers=h, json={"action": "approve", "comment": comment}, timeout=10)
    assert r.status_code == 200, f"审批失败: {r.text}"
    j = r.json()
    inst_status = j.get("data", {}).get("instance_status", "?")
    print(f"  ✅ {role_name} 审批通过 ID={tid} → 实例状态: {inst_status}")
    return instance_id


def step(label):
    print(f"\n  ── {label} ──")


# ============================================================
print(f"\n{SEP}")
print("  🚀 角色模拟: 采购来单→出货全链路 (core_production 11节点)")
print(f"{SEP}")

# 1. 创建采购申请 (钱仓管 wh01, WAREHOUSE 角色)
print("\n📦 第一步: 创建采购申请")
h_wh = login("wh01")[1]
step("1.1 创建采购申请单")
r = requests.post(f"{BASE}/api/purchase-requests", headers=h_wh, json={
    "items": [
        {"name": "钛合金棒料", "spec": "φ20×500mm", "qty": 50, "unit": "根", "est_price": 280},
        {"name": "304不锈钢板", "spec": "2mm×1.5m×3m", "qty": 20, "unit": "张", "est_price": 450},
        {"name": "进口轴承", "spec": "6205-2RS", "qty": 100, "unit": "个", "est_price": 35},
    ],
    "reason": "生产备料, 当前库存不足",
})
assert r.status_code == 200, f"创建采购申请失败: {r.text}"
pid = r.json()["data"]["id"]
pr_no = r.json()["data"]["req_no"]
print(f"  ✅ 创建采购申请 ID={pid} 单号={pr_no}")

step("1.2 提交采购申请 → 启动core_production审批流")
r = requests.post(f"{BASE}/api/purchase-requests/{pid}/submit", headers=h_wh, timeout=10)
assert r.status_code == 200, f"提交失败: {r.text}"
j = r.json()
INSTANCE_ID = j["data"]["approval_instance_id"]
print(f"  ✅ 提交成功 → 状态={j['data']['status']} 审批实例ID={INSTANCE_ID}")

# ============================================================
# 2. 角色逐节点审批
# core_production 11节点:
#   1-部门主管审批 → 3-总经理审批 → 5-运营核对 → 7-生产下达 → 9-完工确认 → 11-运营归档
#   2-财务审核     4-仓管来货登记(process) 6-财务入账  8-车间生产(process) 10-质检确认
print(f"\n{SEP}")
print("  🔄 逐节点角色模拟审批")
print(f"{SEP}")


def node_approve(label, username, role_name, expect_inst=None):
    """通用节点审批: 登录→查看待办→审批本流程任务"""
    print(f"\n  ⏭ 节点: {label}")
    h = login(username)[1]
    task = find_my_task(h, role_name, expect_inst or INSTANCE_ID)
    if task:
        approve_task(h, task, role_name)


# 节点1: 部门主管审批 → 李主管
node_approve("1/11 采购申请 → 部门主管审批", "dept01", "李主管")

# 节点2: 财务审核 → 赵财务
node_approve("2/11 财务审核", "fin01", "赵财务")

# 节点3: 总经理审批 → 孙总
node_approve("3/11 总经理审批", "gm01", "孙总")

# 节点4: 仓管来货登记 → process (自动跳过)

# 节点5: 运营核对 → 王运营
node_approve("5/11 运营核对", "ops01", "王运营")

# 节点6: 财务入账 → 赵财务
node_approve("6/11 财务入账", "fin01", "赵财务")

# 节点7: 生产下达 → 周厂长A
node_approve("7/11 生产下达", "mgr_a", "周厂长A")

# 节点8: 车间生产 → process (自动跳过)

# 节点9: 完工确认 → 钱仓管
node_approve("9/11 完工确认", "wh01", "钱仓管")

# 节点10: 质检确认 → 周厂长A
node_approve("10/11 质检确认", "mgr_a", "周厂长A")

# 节点11: 运营归档 → 王运营
node_approve("11/11 运营归档", "ops01", "王运营")

# ============================================================
# 3. 最终验证
print(f"\n{SEP}")
print("  🏁 最终验证: 流程实例状态 & 采购申请状态")
print(f"{SEP}")

# 获取采购申请
r = requests.get(f"{BASE}/api/purchase-requests", headers=h_wh, timeout=10)
prs = r.json().get("data", [])
pr = next((p for p in prs if p["id"] == pid), None)
if pr:
    print(f"  采购申请 #{pr['req_no']} 状态: {pr['status']}")
    if pr['status'] == 'APPROVED':
        print("  ✅ 全链路审批完成! 采购申请已通过")
    else:
        print(f"  ⚠️ 状态异常: {pr['status']}")

# 获取审批实例
r = requests.get(f"{BASE}/api/approvals/instances/core_production/{pid}", headers=h_wh, timeout=10)
inst_data = r.json().get("data", {})
inst = inst_data.get("instance")
if inst:
    print(f"  审批实例状态: {inst['status']}")
    total = len(inst_data.get("nodes", []))
    done = sum(1 for n in inst_data.get("nodes", []) if n["status"] == "done")
    print(f"  节点进度: {done}/{total}")
    for n in inst_data.get("nodes", []):
        status_icon = "✅" if n["status"] == "done" else "❌" if n["status"] == "rejected" else "⏳"
        assignee = f" → {n['assignee_name']}" if n.get("assignee_name") else ""
        comment = f" ({n['comment']})" if n.get("comment") else ""
        print(f"    {status_icon} #{n['seq']} {n['name']}{assignee}{comment}")

print(f"\n{SEP}")
print("  🎉 角色模拟完成!")
print(f"{SEP}")