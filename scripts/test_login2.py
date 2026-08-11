import requests
BASE = "http://127.0.0.1:8001"
r = requests.post(f"{BASE}/api/auth/login", json={"username": "wh01", "password": "123456"}, timeout=10)
print("status:", r.status_code)
import json
print("body:", json.dumps(r.json(), ensure_ascii=False, indent=2))