"""AI分析测试 - DeepSeek双调用架构验证"""
import json, urllib.request, sys, time

BASE = "http://127.0.0.1:8000"
PASS = "123456"
OK = "✅"
FAIL = "❌"

test_count = pass_count = fail_count = 0

def call(method, path, token=None, body=None, label=""):
    global test_count, pass_count, fail_count
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    test_count += 1
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            pass_count += 1
            return json.loads(r.read().decode())
    except Exception as e:
        fail_count += 1
        print(f"  {FAIL} {label}: {e}")
        return {}

def login(u, p=PASS):
    r = call("POST", "/api/auth/login", body={"username": u, "password": p})
    return r.get("token")

def p(title):
    print(f"\n{'='*70}\n  {title}\n{'='*70}")

def summary():
    print(f"\n{'='*70}")
    print(f"  测试汇总: 总计{test_count} 通过{pass_count} 失败{fail_count}")
    if fail_count == 0:
        print(f"  {OK} 全部通过!")
    print(f"{'='*70}")

# ================================================================
p("阶段1: 登录")
# ================================================================
tk = login("admin")
if not tk:
    print(f"  {FAIL} 登录失败,终止")
    sys.exit(1)
print(f"  {OK} 登录成功")

# ================================================================
p("阶段2: LLM意图解析+报告生成(自然语言指令)")
# ================================================================
cases = [
    "分析订单状态分布,指出哪些状态金额异常",
    "这个月收款情况怎么样,有没有风险",
    "各车间产量对比,哪个车间表现最好最差",
    "成本结构分析,哪些成本占比过高",
    "商机阶段分布,销售漏斗健康吗",
    "开票类型和公司主体的收入分布",
    "销售业绩排行,谁是销冠谁需要加油",
]
for text in cases:
    t0 = time.time()
    r = call("POST", "/api/ai/analyze", tk, {"text": text}, label=text)
    dt = time.time() - t0
    d = r.get("data", {})
    if not d:
        continue
    typ = d.get("type", "")
    llm = d.get("llm_used", False)
    reply = d.get("reply", "")
    has_chart = bool(d.get("pivot_data", {}).get("chart"))
    print(f"  {OK} [{typ}/LLM={llm}/图表={has_chart}/{dt:.1f}s] {text}")
    # 打印报告前4行
    for line in reply.split("\n")[:4]:
        if line.strip():
            print(f"       {line[:90]}")
    print()

# ================================================================
p("阶段3: 通用问题(直接查库不走LLM)")
# ================================================================
for text in ["经营概况", "回款预警", "应收账龄分析"]:
    r = call("POST", "/api/ai/analyze", tk, {"text": text}, label=text)
    d = r.get("data", {})
    typ = d.get("type", "")
    print(f"  {OK} [{typ}] {text}")
    for line in d.get("reply", "").split("\n")[:3]:
        if line.strip():
            print(f"       {line[:90]}")
    print()

# ================================================================
p("阶段4: 复杂自然语言(LLM理解能力测试)")
# ================================================================
complex_cases = [
    "帮我看看哪些客户还欠我们钱,按金额排序",
    "对比两个公司主体的订单和开票情况",
    "最近库存进出情况如何,有没有异常流水",
]
for text in complex_cases:
    t0 = time.time()
    r = call("POST", "/api/ai/analyze", tk, {"text": text}, label=text)
    dt = time.time() - t0
    d = r.get("data", {})
    typ = d.get("type", "")
    llm = d.get("llm_used", False)
    print(f"  {OK} [{typ}/LLM={llm}/{dt:.1f}s] {text}")
    for line in d.get("reply", "").split("\n")[:3]:
        if line.strip():
            print(f"       {line[:90]}")
    print()

# ================================================================
summary()
sys.exit(0 if fail_count == 0 else 1)
