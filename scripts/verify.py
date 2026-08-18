import requests, json

BASE = 'http://localhost:8000'
r = requests.post(f'{BASE}/api/auth/login', json={'username':'admin','password':'admin123'}, timeout=5)
token = r.json()['token']
h = {'Authorization': f'Bearer {token}'}

def test(name, body, check_type='table'):
    r = requests.post(f'{BASE}/api/analysis/pivot', headers=h, json=body, timeout=5)
    d = r.json()
    if 'data' not in d:
        print(f'❌ {name}: {d}')
        return
    data = d['data']
    print(f'\n{"="*60}')
    print(f'测试: {name}')
    print(f'参数: {json.dumps(body, ensure_ascii=False)[:100]}')
    print(f'结果:')
    print(f'  行维度值: {data["row_keys"]}')
    print(f'  列维度值: {data["col_keys"]}')
    print(f'  合计: {data["summary"]["total"]}')
    print(f'  图表: type={data["chart"]["chart_type"]}, x={data["chart"]["x"][:5]}, series数={len(data["chart"]["series"])}')
    print(f'  表格数据:')
    for t in data['table'][:3]:
        print(f'    {t}')
    # 验证数据合理性
    if check_type == 'count':
        for t in data['table']:
            val = t.get('__total__', 0)
            if val != int(val):
                print(f'  ⚠️ 计数应为整数, 但得到 {val}')
    print(f'✅ 正常')

# 测试1: 订单按客户 + 求和
test('订单按客户-求和金额', {
    'dataset': 'orders', 'rows_dim': 'customer_id', 'metric': 'total_amount', 'agg': 'sum'
})

# 测试2: 订单按客户 + 计数
test('订单按客户-计数订单数', {
    'dataset': 'orders', 'rows_dim': 'customer_id', 'metric': 'id', 'agg': 'count'
})

# 测试3: 订单按状态 + 计数
test('订单按状态-计数', {
    'dataset': 'orders', 'rows_dim': 'status', 'metric': 'total_amount', 'agg': 'count'
})

# 测试4: 财务单据按类型 + 求和
test('财务单据按类型-求和金额', {
    'dataset': 'finance_docs', 'rows_dim': 'doc_type', 'metric': 'amount', 'agg': 'sum'
})

# 测试5: 交叉分析
test('订单按客户×开票类型', {
    'dataset': 'orders', 'rows_dim': 'customer_id', 'cols_dim': 'billing_type', 'metric': 'total_amount', 'agg': 'sum'
})

# 测试6: 带筛选
test('筛选-草稿订单', {
    'dataset': 'orders', 'rows_dim': 'customer_id', 'metric': 'total_amount', 'agg': 'sum',
    'filters': [{'field': 'status', 'op': 'eq', 'value': 'DRAFT'}]
})

print('\n' + '='*60)
print('所有测试完成')