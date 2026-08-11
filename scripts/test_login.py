import requests
BASE = "http://127.0.0.1:8001"
for u in ["wh01", "dept01", "fin01", "gm01", "ops01", "mgr_a", "admin"]:
    r = requests.post(f"{BASE}/api/auth/login", json={"username": u, "password": "123456"}, timeout=10)
    print(f"  {u}: status={r.status_code}", end="")
    if r.status_code == 200:
        j = r.json()
        print(f" token={j.get('access_token','?')[:20]}... role={j.get('user',{}).get('role_code','?')}")
    else:
        print(f" {r.text[:100]}")