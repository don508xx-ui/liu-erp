"""修复测试数据: 给所有订单补上缺失的开票类型"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import SessionLocal
from app.models.order import Order
import random

db = SessionLocal()

orders = db.query(Order).filter(Order.billing_type == None).all()
print(f"需要修复的订单: {len(orders)} 条")

billing_types = ['NORMAL', 'VAT', 'CASH']
for o in orders:
    o.billing_type = random.choice(billing_types)
    o.delivery_status = o.delivery_status or 'PENDING'

db.commit()
print(f"✅ 已修复 {len(orders)} 条订单")

# 验证
empty_count = db.query(Order).filter(Order.billing_type == None).count()
print(f"剩余空开票类型: {empty_count} 条")

db.close()