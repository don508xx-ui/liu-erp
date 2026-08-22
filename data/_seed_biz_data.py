"""
全业务关联模拟数据 - 独立入口脚本
================================
数据链条: 客户订单 → 加工工单 → 完工确认 → 应收/收款 → 资金流水
        供应商采购 → 应付/付款 → 资金流水
        工资/经营费用 → 资金流水
"""
import sys, os
from app.core.db import SessionLocal
from app.seed_data import seed_biz_data

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    db = SessionLocal()
    seed_biz_data(db)
    db.close()