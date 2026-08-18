import json,urllib.request
B='http://127.0.0.1:8000'
def call(m,p,b=None,t=None):
 r=urllib.request.Request(B+p,data=json.dumps(b).encode() if b else None,method=m); r.add_header('Content-Type','application/json');
 if t:r.add_header('Authorization','Bearer '+t)
 return json.loads(urllib.request.urlopen(r,timeout=60).read().decode())
tk=call('POST','/api/auth/login',{'username':'admin','password':'admin123'})['token']

print("=== 按客户分析订单金额 ===")
r=call('POST','/api/analysis/pivot',{'dataset':'orders','rows_dim':'customer_id','metric':'total_amount','agg':'sum'},tk)
for row in r['data']['table']:
    print(f"  {row['dim']}: {row.get('__total__',0):,.0f}")

print("\n=== 按状态分析订单 ===")
r2=call('POST','/api/analysis/pivot',{'dataset':'orders','rows_dim':'status','metric':'total_amount','agg':'sum'},tk)
for row in r2['data']['table']:
    print(f"  {row['dim']}: {row.get('__total__',0):,.0f}")

print("\n=== 交叉分析 (客户x状态) ===")
r3=call('POST','/api/analysis/pivot',{'dataset':'orders','rows_dim':'customer_id','cols_dim':'status','metric':'total_amount','agg':'sum','chart_type':'bar'},tk)
print(f"  col_keys: {r3['data']['col_keys']}")
for row in r3['data']['table']:
    vals = {k:f"{v:,.0f}" for k,v in row.items() if k!='dim'}
    print(f"  {row['dim']}: {vals}")

print("\n=== 财务单据按类型分析 ===")
r4=call('POST','/api/analysis/pivot',{'dataset':'finance_docs','rows_dim':'doc_type','metric':'amount','agg':'sum'},tk)
for row in r4['data']['table']:
    print(f"  {row['dim']}: {row.get('__total__',0):,.0f}")

print("\n=== 工单成本按类型分析 ===")
r5=call('POST','/api/analysis/pivot',{'dataset':'work_order_costs','rows_dim':'cost_type','metric':'amount','agg':'sum'},tk)
for row in r5['data']['table']:
    print(f"  {row['dim']}: {row.get('__total__',0):,.0f}")

print("\n=== 商机按阶段分析 ===")
r6=call('POST','/api/analysis/pivot',{'dataset':'opportunities','rows_dim':'stage','metric':'expected_amount','agg':'sum'},tk)
for row in r6['data']['table']:
    print(f"  {row['dim']}: {row.get('__total__',0):,.0f}")

print("\n✅ 透视分析验证完成")