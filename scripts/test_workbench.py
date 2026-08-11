"""测试工作台接口"""
import requests

BASE = "http://127.0.0.1:8000"

# 登录
r = requests.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": "123456"})
token = r.json()["token"]
print(f"[OK] 登录成功, token前20: {token[:20]}...")

# 工作台
r = requests.get(f"{BASE}/api/workbench", headers={"Authorization": f"Bearer {token}"})
data = r.json()
if data.get("code") != 0:
    print(f"[FAIL] 接口返回错误: {data}")
else:
    d = data["data"]
    print(f"[OK] 角色: {d['role']}")
    print(f"[OK] 待办数: {len(d['todos'])}")
    for t in d["todos"]:
        print(f"     - [{t['color']}] {t['text']} → {t['route']}")
    print(f"[OK] KPI卡片: {len(d['kpis'])}")
    for k in d["kpis"]:
        print(f"     - {k['label']}: {k['value']}")
    print(f"[OK] 应用分组: {list(d['apps'].keys())}")
    for gname, apps in d["apps"].items():
        print(f"     {gname}: {len(apps)}个应用")
    print(f"\n[OK] 汇总: {d['summary']}")
