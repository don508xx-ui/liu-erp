import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# 测试用文件SQLite(每个session独立文件,测试结束删除)
TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "test.db")
os.makedirs(os.path.dirname(TEST_DB_PATH), exist_ok=True)
if os.path.exists(TEST_DB_PATH):
    os.remove(TEST_DB_PATH)

from app.core.db import Base
import app.models  # noqa: 确保模型注册
from app.core import db as db_mod

engine = create_engine(f"sqlite:///{TEST_DB_PATH}", connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _fk(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


# 替换全局engine和SessionLocal
db_mod.engine = engine
db_mod.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    Base.metadata.create_all(engine)
    # seed基础数据
    from app.core.auth import hash_password
    from app.models.system import User, Role, Permission, RolePermission
    from app.models.customer import Customer
    from app.models.purchase import Supplier
    from app.models.inventory import InventoryItem
    from app.models.finance import Account
    from app.models.approval import FlowDefinition
    from app.models.analysis import AlertRule
    from app.models.notification import NotificationTemplate, NotificationChannel
    from app.hooks.bootstrap import seed_templates

    with db_mod.db_scope() as db:
        # 角色
        roles = [("ADMIN", "管理员"), ("SALES", "销售"), ("OPERATION", "运营"),
                 ("FINANCE", "财务"), ("WAREHOUSE", "仓管"), ("GM", "总经理"),
                 ("MANAGER", "厂长"), ("AGENT", "Agent")]
        role_map = {}
        for code, name in roles:
            r = db.query(Role).filter(Role.code == code).first()
            if not r:
                r = Role(name=name, code=code)
                db.add(r)
                db.flush()
            role_map[code] = r.id
        # 用户
        users = [("admin", "管理员", "ADMIN"), ("sales01", "张销售", "SALES"),
                 ("ops01", "王运营", "OPERATION"), ("fin01", "赵财务", "FINANCE"),
                 ("wh01", "钱仓管", "WAREHOUSE"), ("gm01", "孙总", "GM"),
                 ("mgr_a", "周厂长A", "MANAGER")]
        for u, n, rc in users:
            if not db.query(User).filter(User.username == u).first():
                db.add(User(username=u, password_hash=hash_password("123456"),
                            name=n, role_id=role_map.get(rc), status="ACTIVE",
                            email=f"{u}@example.com"))
        # 客户
        cs = [("CUST-001", "上海某汽配有限公司", "91310000XX001", "上海浦东", "陈经理", "13800001111", "汽配", "月结30"),
              ("CUST-002", "广东某家电公司", "91440300XX002", "深圳南山", "林总", "13800002222", "家电", "月结60"),
              ("CUST-003", "宁波某五金厂", "91330200XX003", "宁波鄞州", "王工", "13800003333", "五金", "款到发货")]
        for code, name, tax, addr, contact, phone, ind, cycle in cs:
            if not db.query(Customer).filter(Customer.code == code).first():
                db.add(Customer(code=code, name=name, tax_no=tax, address=addr,
                                contact_name=contact, contact_phone=phone,
                                industry=ind, settlement_cycle=cycle, status="ACTIVE"))
        # 供应商
        ss = [("SUP-001", "立邦涂料", "杨经理", "13900001111"),
              ("SUP-002", "阿克苏诺贝尔粉末", "马经理", "13900002222")]
        for code, name, c, p in ss:
            if not db.query(Supplier).filter(Supplier.code == code).first():
                db.add(Supplier(code=code, name=name, contact=c, phone=p, status="ACTIVE"))
        # 物料
        items = [("MAT-001", "特氟龙PTFE涂料(黑)", "PTFE-Black-1kg", "kg", "PAINT_POWDER", 200, 50, 380, "A区"),
                 ("MAT-002", "静电粉末(灰)", "RAL7016-20kg", "kg", "PAINT_POWDER", 500, 100, 45, "A区"),
                 ("MAT-010", "稀释剂", "XY-5L", "L", "CONSUMABLE", 100, 30, 35, "B区"),
                 ("MAT-020", "脱脂剂", "DG-25kg", "kg", "RAW_MATERIAL", 300, 100, 12, "C区"),
                 ("MAT-099", "喷涂成品(虚拟)", "FINISHED", "件", "FINISHED_GOOD", 0, 0, 0, "成品仓")]
        for code, name, spec, unit, cat, stock, safety, cost, loc in items:
            if not db.query(InventoryItem).filter(InventoryItem.code == code).first():
                db.add(InventoryItem(code=code, name=name, spec=spec, unit=unit, category=cat,
                                     stock_qty=stock, safety_qty=safety, unit_cost=cost, location=loc, status="ACTIVE"))
        # 科目
        accs = [("1001", "库存现金", "ASSET", "DEBIT", 1), ("1002", "银行存款", "ASSET", "DEBIT", 1),
                ("1122", "应收账款", "ASSET", "DEBIT", 1), ("1401", "原材料", "ASSET", "DEBIT", 1),
                ("1405", "库存商品", "ASSET", "DEBIT", 1), ("5001", "生产成本", "ASSET", "DEBIT", 1),
                ("5101", "制造费用", "ASSET", "DEBIT", 1), ("2202", "应付账款", "LIABILITY", "CREDIT", 1),
                ("2211", "应付职工薪酬", "LIABILITY", "CREDIT", 1), ("6001", "主营业务收入", "REVENUE", "CREDIT", 1),
                ("6401", "主营业务成本", "EXPENSE", "DEBIT", 1), ("6602", "管理费用", "EXPENSE", "DEBIT", 1)]
        for code, name, t, d, req in accs:
            if not db.query(Account).filter(Account.code == code).first():
                db.add(Account(code=code, name=name, type=t, direction=d, is_required=req, level=1, status="ACTIVE"))
        seed_templates(db)
        # 通知渠道
        for ch, name in [("FEISHU", "飞书"), ("EMAIL", "邮件"), ("INAPP", "站内信")]:
            if not db.query(NotificationChannel).filter(NotificationChannel.channel == ch).first():
                db.add(NotificationChannel(channel=ch, name=name, config={}, status="ACTIVE"))
        # 审批流
        if not db.query(FlowDefinition).filter(FlowDefinition.biz_type == "PURCHASE_REQUEST").first():
            db.add(FlowDefinition(name="采购申请审批", biz_type="PURCHASE_REQUEST",
                                  nodes=[{"seq": 1, "name": "总经理审批", "approver_role": "GM"}], status="ACTIVE", version=1))
        # 预警规则
        if not db.query(AlertRule).filter(AlertRule.code == "LOW_MARGIN").first():
            db.add(AlertRule(code="LOW_MARGIN", name="毛利率<15%", metric="GROSS_MARGIN",
                             condition={"op": "<", "value": 0.15}, channels=["INAPP"], recipients=["GM"], enabled=True))

    yield


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def login(client, username="admin", password="123456"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}
