# -*- coding: utf-8 -*-
"""
结构迁移脚本 - 幂等, 每次启动安全可重跑。
1) work_orders 表补 customer_id / customer_name 列 (缺啥补啥)
2) CORE_PRODUCTION 流程定义升级为新4节点(销售发起→部门主管→运营核单转工单→总经理抄送)
3) 给已关联订单的工单回填 customer_id/customer_name
"""
import os, sys, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import settings

DB_URL = settings.DB_URL
# 从 sqlite url 提取文件路径 sqlite:///path 或 sqlite:///./path
if DB_URL.startswith("sqlite:///"):
    db_path = DB_URL[len("sqlite:///"):]
    if db_path.startswith("./"):
        db_path = os.path.abspath(db_path[2:])
    elif not os.path.isabs(db_path):
        db_path = os.path.abspath(db_path)
else:
    db_path = DB_URL
print("DB:", db_path)
con = sqlite3.connect(db_path)
c = con.cursor()


def cols(tbl):
    return set(r[1] for r in c.execute(f"PRAGMA table_info({tbl})").fetchall())


def ensure_col(tbl, col, col_sql):
    cs = cols(tbl)
    if col in cs:
        return False
    c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col_sql}")
    print(f"[migrate] {tbl} 新增列 {col}")
    return True


# 1. work_orders 补列
ensure_col("work_orders", "customer_id", "customer_id INTEGER")
ensure_col("work_orders", "customer_name", "customer_name VARCHAR(128)")
con.commit()

# 2. 回填 customer_id/customer_name (基于 order_id → orders.customer_id)
rows = c.execute("""
    SELECT wo.id, wo.order_id, o.customer_id, cu.name
    FROM work_orders wo
    JOIN orders o ON o.id = wo.order_id
    LEFT JOIN customers cu ON cu.id = o.customer_id
    WHERE wo.customer_id IS NULL OR wo.customer_name IS NULL OR wo.customer_name = ''
""").fetchall()
for wid, oid, cid, cname in rows:
    c.execute("UPDATE work_orders SET customer_id=?, customer_name=? WHERE id=?", (cid, cname or "", wid))
print(f"[migrate] 回填工单客户信息 {len(rows)} 条")
con.commit()

# 3. CORE_PRODUCTION 流程定义升级
NODES = [
    {"seq": 1, "name": "销售发起", "type": "process", "approver_role": "SALES"},
    {"seq": 2, "name": "部门主管审批", "type": "approve", "approver_role": "DEPARTMENT_HEAD"},
    {"seq": 3, "name": "运营核单转工单", "type": "approve", "approver_role": "OPERATION"},
    {"seq": 4, "name": "总经理抄送", "type": "cc", "approver_role": "GM", "cc_roles": ["GM"]},
]
import json
nodes_json = json.dumps(NODES, ensure_ascii=False)
cur = c.execute(
    "UPDATE flow_definitions SET name=?, nodes=?, version=version+1 WHERE biz_type='CORE_PRODUCTION' AND status='ACTIVE'",
    ("订单生产审批(运营转工单)", nodes_json),
)
print(f"[migrate] 更新CORE_PRODUCTION定义, 影响行数={cur.rowcount}")
con.commit()

print("完成")
con.close()