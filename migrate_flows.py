"""迁移: 清理旧版流程定义创建的RUNNING实例,重置业务状态为DRAFT"""
import sqlite3, json

conn = sqlite3.connect("data/erp.db")
c = conn.cursor()

# 获取当前 ACTIVE 的流程定义
c.execute("SELECT id, biz_type, nodes, version FROM flow_definitions WHERE status='ACTIVE'")
defs = {row[1]: json.loads(row[2]) if isinstance(row[2], str) else row[2] for row in c.fetchall()}

# 找出所有RUNNING的流程实例
c.execute("SELECT id, biz_type, biz_id, status FROM flow_instances WHERE status='RUNNING'")
instances = c.fetchall()
print(f"Found {len(instances)} RUNNING instances")

# 业务实体表映射
BIZ_TABLES = {
    "CORE_PRODUCTION": "orders",
    "SALES_ADJUSTMENT": "sales_adjustments",
    "RECEIVING": "receiving_logs",
    "COMPLETION": "completions",
    "EXPENSE": "expense_claims",
    "PURCHASE_REQUEST": "purchase_requests",
    "PROCUREMENT": "purchase_requests",
}

for inst_id, biz_type, biz_id, status in instances:
    if biz_type in BIZ_TABLES:
        table = BIZ_TABLES[biz_type]
        # 重置业务状态为 DRAFT 或 PENDING
        if table == "orders":
            c.execute(f"UPDATE {table} SET status='DRAFT', approval_instance_id=NULL WHERE id=?", (biz_id,))
        elif table == "sales_adjustments":
            c.execute(f"UPDATE {table} SET status='DRAFT', approval_instance_id=NULL WHERE id=?", (biz_id,))
        elif table == "receiving_logs":
            c.execute(f"UPDATE {table} SET status='RECEIVED', approval_instance_id=NULL WHERE id=?", (biz_id,))
        elif table == "completions":
            c.execute(f"UPDATE {table} SET status='DRAFT', approval_instance_id=NULL WHERE id=?", (biz_id,))
        elif table == "expense_claims":
            c.execute(f"UPDATE {table} SET status='DRAFT', approval_instance_id=NULL WHERE id=?", (biz_id,))
        elif table == "purchase_requests":
            c.execute(f"UPDATE {table} SET status='DRAFT', approval_instance_id=NULL WHERE id=?", (biz_id,))
        print(f"  Reset {table}.{biz_id} to DRAFT")
    
    # 删除关联的任务
    c.execute("DELETE FROM flow_tasks WHERE instance_id=?", (inst_id,))
    # 删除实例
    c.execute("DELETE FROM flow_instances WHERE id=?", (inst_id,))
    print(f"  Deleted flow instance {inst_id} ({biz_type})")

conn.commit()
conn.close()
print("Migration complete.")