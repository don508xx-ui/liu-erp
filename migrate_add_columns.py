"""迁移脚本：添加biz_data和form_data字段"""
import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), 'data', 'erp.db')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 检查FlowInstance表是否已有biz_data字段
cursor.execute("PRAGMA table_info(flow_instances)")
columns = [row[1] for row in cursor.fetchall()]
if 'biz_data' not in columns:
    cursor.execute("ALTER TABLE flow_instances ADD COLUMN biz_data JSON")
    print("Added biz_data column to flow_instances")
else:
    print("biz_data column already exists")

# 检查FlowTask表是否已有form_data字段
cursor.execute("PRAGMA table_info(flow_tasks)")
columns = [row[1] for row in cursor.fetchall()]
if 'form_data' not in columns:
    cursor.execute("ALTER TABLE flow_tasks ADD COLUMN form_data JSON")
    print("Added form_data column to flow_tasks")
else:
    print("form_data column already exists")

conn.commit()
conn.close()
print("Migration completed!")
