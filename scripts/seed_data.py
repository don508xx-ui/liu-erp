"""
模拟数据填充脚本 - 幂等可重复执行
覆盖:角色/用户/客户/供应商/物料/科目/模板/审批流/预警规则/示例订单/工单/完工单/工资
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import init_db, db_scope
from app.core.auth import hash_password, verify_password
from app.models.system import User, Role, Permission, RolePermission
from app.models.customer import Customer
from app.models.purchase import Supplier
from app.models.inventory import InventoryItem
from app.models.finance import Account
from app.models.analysis import AlertRule, PaymentSchedule
from app.models.notification import NotificationTemplate, NotificationChannel
from app.models.sales import Company, Contract, Opportunity
from app.models.dict import Dict
from app.hooks.bootstrap import seed_templates
from datetime import datetime, timedelta


def seed():
    init_db()
    with db_scope() as db:
        # 迁移: roles表添加status列(老库缺)
        from sqlalchemy import text
        for alter in [
            "ALTER TABLE roles ADD COLUMN status VARCHAR(16) DEFAULT 'ACTIVE'",
            "ALTER TABLE users ADD COLUMN pages TEXT",
        ]:
            try:
                db.execute(text(alter))
                db.commit()
            except Exception:
                pass
        _roles(db)
        _users(db)
        _permissions(db)
        _companies(db)
        _customers(db)
        _suppliers(db)
        _inventory(db)
        _accounts(db)
        _dicts(db)
        seed_templates(db)
        _channels(db)
        _alerts(db)
        _opportunities(db)
        _contracts(db)
        _payment_schedules(db)
    print("seed done")


def _roles(db):
    roles = [
        ("ADMIN", "系统管理员", "超管,全部权限"),
        ("SALES", "销售", "下单/催款,仅自己订单"),
        ("OPERATION", "运营助理", "核单/工单/完工确认"),
        ("FINANCE", "财务", "应收应付/工资/报账"),
        ("WAREHOUSE", "仓管", "出入库/退库/领料"),
        ("GM", "总经理", "全只读+预警"),
        ("MANAGER", "车间厂长", "加工/填完工单"),
        ("DEPARTMENT_HEAD", "部门主管", "部门审批负责人"),
        ("AGENT", "Agent", "配置+分析只读"),
    ]
    for code, name, desc in roles:
        if not db.query(Role).filter(Role.code == code).first():
            db.add(Role(name=name, code=code, description=desc))
    db.flush()


def _users(db):
    role_map = {r.code: r.id for r in db.query(Role).all()}
    users = [
        ("admin", "系统管理员", "ADMIN"),
        ("sales01", "张销售", "SALES"),
        ("sales02", "李销售", "SALES"),
        ("ops01", "王运营", "OPERATION"),
        ("fin01", "赵财务", "FINANCE"),
        ("wh01", "钱仓管", "WAREHOUSE"),
        ("gm01", "孙总", "GM"),
        ("mgr_a", "周厂长A", "MANAGER"),
        ("mgr_b", "吴厂长B", "MANAGER"),
        ("dept01", "李主管", "DEPARTMENT_HEAD"),
    ]
    for username, name, role_code in users:
        if not db.query(User).filter(User.username == username).first():
            # admin 密码 = admin123, 其余统一 123456
            pwd = "admin123" if username == "admin" else "123456"
            db.add(User(
                username=username, password_hash=hash_password(pwd),
                name=name, role_id=role_map.get(role_code), status="ACTIVE",
                email=f"{username}@example.com" if role_code != "GM" else "gm@example.com",
            ))
    # 迁移: 早期seed把admin也写成123456, 若admin已存在且密码是旧默认123456则修正为admin123
    ua = db.query(User).filter(User.username == "admin").first()
    if ua and verify_password("123456", ua.password_hash):
        ua.password_hash = hash_password("admin123")


def _permissions(db):
    perms = []
    modules = ["customer", "order", "work_order", "completion", "requisition",
               "inventory", "finance", "purchase", "approval", "notification", "analysis", "payroll"]
    actions = ["create", "read", "update", "delete", "approve"]
    for m in modules:
        for a in actions:
            perms.append((f"{m}:{a}", f"{m}.{a}", m, a))
    for code, name, m, a in perms:
        if not db.query(Permission).filter(Permission.code == code).first():
            db.add(Permission(code=code, name=name, module=m, action=a))
    db.flush()
    # 给除AGENT外所有角色全权限
    roles = db.query(Role).filter(Role.code != "AGENT").all()
    all_perms = db.query(Permission).all()
    for r in roles:
        for p in all_perms:
            if not db.query(RolePermission).filter(RolePermission.role_id == r.id, RolePermission.permission_id == p.id).first():
                db.add(RolePermission(role_id=r.id, permission_id=p.id))


def _companies(db):
    companies = [
        ("GENERAL", "东莞市峰业精密机械设备有限公司", "峰业机械", "GENERAL", "91441900XXXX001", "工商银行虎门支行", "6222000011110001", "东莞市虎门镇南栅六区民昌路28号", "0769-85507857"),
        ("SMALL", "东莞市峰业热喷涂加工厂", "峰业喷涂", "SMALL", "91441900XXXX002", "建设银行虎门支行", "6227000022220002", "东莞市虎门镇南栅六区民昌路28号", "0769-85507857"),
    ]
    for code, name, short, tax_type, tax_no, bank, acct, addr, phone in companies:
        if not db.query(Company).filter(Company.code == code).first():
            db.add(Company(code=code, name=name, short_name=short, tax_type=tax_type, tax_no=tax_no,
                           bank_name=bank, bank_account=acct, address=addr, phone=phone, status="ACTIVE"))


def _customers(db):
    # (code, name, short_code, tax, addr, contact, phone, industry, cycle, bank, acct, default_company_code)
    general_id = db.query(Company).filter(Company.code == "GENERAL").first()
    general_id = general_id.id if general_id else None
    small_id = db.query(Company).filter(Company.code == "SMALL").first()
    small_id = small_id.id if small_id else None
    customers = [
        ("CUST-001", "上海某汽配有限公司", "SHQP", "91310000XXXX001", "上海市浦东新区XX路100号", "陈经理", "13800001111", "汽配", "月结30", "工商银行", "6222000011110001", general_id),
        ("CUST-002", "广东某家电科技股份有限公司", "GDJD", "91440300XXXX002", "深圳市南山区XX路200号", "林总", "13800002222", "家电", "月结60", "建设银行", "6227000022220002", general_id),
        ("CUST-003", "宁波某五金制品厂", "NBWJ", "91330200XXXX003", "宁波市鄞州区XX路300号", "王工", "13800003333", "五金", "款到发货", "农业银行", "6228480033330003", small_id),
        ("CUST-004", "苏州某化工设备有限公司", "SZHG", "91320500XXXX004", "苏州市吴中区XX路400号", "刘总", "13800004444", "化工", "月结30", "中国银行", "6217000044440004", general_id),
        ("CUST-005", "杭州某机械制造有限公司", "HZJX", "91330100XXXX005", "杭州市余杭区XX路500号", "赵工", "13800005555", "机械", "月结90", "招商银行", "6225880055550005", general_id),
        ("CUST-006", "鹏程新能源科技有限公司", "PCXN", "91330100XXXX006", "深圳市宝安区XX路600号", "钱经理", "13800006666", "新能源", "月结30", "工商银行", "6222000066660006", general_id),
        ("CUST-007", "鼎峰机械装备有限公司", "DFJX", "91330100XXXX007", "东莞市松山湖XX路700号", "孙总", "13800007777", "机械", "款到发货", "建设银行", "6227000077770007", small_id),
    ]
    for code, name, sc, tax, addr, contact, phone, industry, cycle, bank, acct, comp_id in customers:
        if not db.query(Customer).filter(Customer.code == code).first():
            db.add(Customer(code=code, name=name, short_code=sc, tax_no=tax, address=addr,
                            contact_name=contact, contact_phone=phone, industry=industry,
                            settlement_cycle=cycle, bank_name=bank, bank_account=acct,
                            default_company_id=comp_id, status="ACTIVE"))
    db.flush()
    # 给客户分配归属销售(权限隔离演示):前4个归sales01,后3个归sales02
    sales01 = db.query(User).filter(User.username == "sales01").first()
    sales02 = db.query(User).filter(User.username == "sales02").first()
    all_custs = db.query(Customer).filter(Customer.status == "ACTIVE").order_by(Customer.id).all()
    for i, c in enumerate(all_custs):
        if not c.owner_user_id:
            c.owner_user_id = sales01.id if (i < 4 and sales01) else (sales02.id if sales02 else None)


def _dicts(db):
    """字典化 - 降低输入成本,菜单选择"""
    dicts = [
        # 工艺类型
        ("PROCESS_TYPE", "镜面喷瓷", "镜面喷瓷"),
        ("PROCESS_TYPE", "加厚喷瓷0.3MM", "加厚喷瓷0.3MM"),
        ("PROCESS_TYPE", "防水喷瓷", "防水喷瓷"),
        ("PROCESS_TYPE", "等离子喷镍60", "等离子喷镍60"),
        ("PROCESS_TYPE", "超音速喷碳化钨", "超音速喷碳化钨"),
        ("PROCESS_TYPE", "车床+喷瓷", "车床+喷瓷"),
        ("PROCESS_TYPE", "磨床+等离子喷镍60", "磨床+等离子喷镍60"),
        ("PROCESS_TYPE", "动平衡+加厚喷瓷+不打边", "动平衡+加厚喷瓷+不打边"),
        ("PROCESS_TYPE", "喷瓷维修", "喷瓷维修"),
        ("PROCESS_TYPE", "特氟龙喷涂", "特氟龙喷涂"),
        ("PROCESS_TYPE", "静电喷粉", "静电喷粉"),
        ("PROCESS_TYPE", "氟碳漆喷涂", "氟碳漆喷涂"),
        # 涂料规格
        ("PAINT_SPEC", "PTFE-Black", "特氟龙黑"),
        ("PAINT_SPEC", "PTFE-White", "特氟龙白"),
        ("PAINT_SPEC", "NI60A", "镍基合金NI60A"),
        ("PAINT_SPEC", "NI65", "镍基合金NI65"),
        ("PAINT_SPEC", "碳化钨", "碳化钨"),
        ("PAINT_SPEC", "氧化铬", "氧化铬"),
        ("PAINT_SPEC", "氧化锆", "氧化锆"),
        ("PAINT_SPEC", "IN625", "IN625"),
        ("PAINT_SPEC", "316L", "316L不锈钢"),
        # 工件规格(常用)
        ("PART_SPEC", "储线轮", "储线轮系列"),
        ("PART_SPEC", "导轮", "导轮系列"),
        ("PART_SPEC", "计米轮", "计米轮系列"),
        ("PART_SPEC", "滚筒", "滚筒系列"),
        ("PART_SPEC", "皮带轮", "皮带轮系列"),
        ("PART_SPEC", "塔轮", "塔轮系列"),
        ("PART_SPEC", "轴类", "轴类工件"),
        # 行业
        ("INDUSTRY", "汽配", "汽配"),
        ("INDUSTRY", "家电", "家电"),
        ("INDUSTRY", "五金", "五金"),
        ("INDUSTRY", "化工", "化工"),
        ("INDUSTRY", "机械", "机械"),
        ("INDUSTRY", "新能源", "新能源"),
        ("INDUSTRY", "线缆设备", "线缆设备"),
        # 结算周期
        ("SETTLEMENT_CYCLE", "款到发货", "款到发货"),
        ("SETTLEMENT_CYCLE", "月结30", "月结30"),
        ("SETTLEMENT_CYCLE", "月结60", "月结60"),
        ("SETTLEMENT_CYCLE", "月结90", "月结90"),
        ("SETTLEMENT_CYCLE", "预付30%+发货前结清", "预付30%+发货前结清"),
        # 商机阶段
        ("STAGE", "LEAD", "线索"),
        ("STAGE", "FOLLOW", "跟进中"),
        ("STAGE", "QUOTE", "已报价"),
        ("STAGE", "WON", "赢单"),
        ("STAGE", "LOST", "输单"),
        # 车间
        ("WORKSHOP", "A线", "氧乙炔喷涂房"),
        ("WORKSHOP", "B线", "等离子喷涂房"),
        ("WORKSHOP", "C线", "超音速喷涂房"),
        # 开票类型
        ("BILLING_TYPE", "SPECIAL_VAT", "增值税专用发票"),
        ("BILLING_TYPE", "NORMAL", "增值税普通发票"),
        ("BILLING_TYPE", "CASH", "现金(无票)"),
    ]
    for t, code, name in dicts:
        if not db.query(Dict).filter(Dict.type == t, Dict.code == code).first():
            db.add(Dict(type=t, code=code, name=name, status="ACTIVE"))


def _opportunities(db):
    """商机示例"""
    sales01 = db.query(User).filter(User.username == "sales01").first()
    custs = {c.code: c.id for c in db.query(Customer).all()}
    opps = [
        ("OPP-20260701-0001", "CUST-001", "储线轮批量喷涂询价", 80000, "QUOTE", 7, "老客户转介"),
        ("OPP-20260710-0002", "CUST-002", "导轮超音速喷涂新项目", 150000, "FOLLOW", 14, "展会"),
        ("OPP-20260715-0003", "CUST-005", "滚筒喷瓷维修", 35000, "LEAD", 30, "主动开发"),
    ]
    for no, ccode, title, amt, stage, days, src in opps:
        if not db.query(Opportunity).filter(Opportunity.oppo_no == no).first():
            db.add(Opportunity(oppo_no=no, customer_id=custs.get(ccode), title=title,
                               expected_amount=amt, stage=stage,
                               expected_close_date=datetime.utcnow() + timedelta(days=days),
                               source=src, owner_user_id=sales01.id if sales01 else None))


def _contracts(db):
    """合同示例"""
    sales01 = db.query(User).filter(User.username == "sales01").first()
    custs = {c.code: c.id for c in db.query(Customer).all()}
    comps = {c.code: c.id for c in db.query(Company).all()}
    contracts = [
        ("CT-20260701-0001", "CUST-001", "GENERAL", 80000, "预付30%,发货前结清,质保金10%半年"),
        ("CT-20260715-0002", "CUST-002", "GENERAL", 150000, "月结60,验收合格后付款"),
    ]
    for no, ccode, comp_code, amt, terms in contracts:
        if not db.query(Contract).filter(Contract.contract_no == no).first():
            db.add(Contract(contract_no=no, customer_id=custs.get(ccode),
                            company_id=comps.get(comp_code), amount=amt,
                            signed_date=datetime.utcnow() - timedelta(days=10),
                            status="EFFECTIVE", owner_user_id=sales01.id if sales01 else None,
                            payment_terms=terms))


def _payment_schedules(db):
    """回款节点示例 - 用于回款预期预警"""
    sales01 = db.query(User).filter(User.username == "sales01").first()
    custs = {c.code: c.id for c in db.query(Customer).all()}
    contracts = {c.contract_no: c.id for c in db.query(Contract).all()}
    # 合同1: 预付30% + 发货前70% + 质保金10%
    ct1 = contracts.get("CT-20260701-0001")
    if ct1:
        schedules = [
            ("PS-20260701-0001", ct1, custs.get("CUST-001"), datetime.utcnow() - timedelta(days=10), 24000, "预付款30%", "PAID"),
            ("PS-20260701-0002", ct1, custs.get("CUST-001"), datetime.utcnow() + timedelta(days=5), 48000, "发货前70%", "UPCOMING"),
            ("PS-20260701-0003", ct1, custs.get("CUST-001"), datetime.utcnow() + timedelta(days=180), 8000, "质保金10%", "UPCOMING"),
        ]
        for no, ct_id, cust_id, due, amt, stage, status in schedules:
            if not db.query(PaymentSchedule).filter(PaymentSchedule.schedule_no == no).first():
                db.add(PaymentSchedule(schedule_no=no, contract_id=ct_id, customer_id=cust_id,
                                       due_date=due, expected_amount=amt,
                                       actual_amount=amt if status == "PAID" else 0,
                                       status=status, stage=stage))


def _suppliers(db):
    suppliers = [
        ("SUP-001", "立邦涂料(上海)有限公司", "杨经理", "13900001111", "工商银行", "6222000011110001"),
        ("SUP-002", "阿克苏诺贝尔粉末涂料", "马经理", "13900002222", "建设银行", "6227000022220002"),
        ("SUP-003", "上海某前处理材料商行", "周老板", "13900003333", "农业银行", "6228480033330003"),
        ("SUP-004", "某委外电镀加工厂", "吴厂长", "13900004444", "中国银行", "6217000044440004"),
    ]
    for code, name, contact, phone, bank, acct in suppliers:
        if not db.query(Supplier).filter(Supplier.code == code).first():
            db.add(Supplier(code=code, name=name, contact=contact, phone=phone, bank_name=bank, bank_account=acct, status="ACTIVE"))


def _inventory(db):
    items = [
        # 涂料粉末类
        ("MAT-001", "特氟龙PTFE涂料(黑)", "PTFE-Black-1kg", "kg", "PAINT_POWDER", 200, 50, 380, "A区-1号"),
        ("MAT-002", "特氟龙PTFE涂料(白)", "PTFE-White-1kg", "kg", "PAINT_POWDER", 150, 50, 420, "A区-1号"),
        ("MAT-003", "静电粉末涂料(灰)", "RAL7016-20kg", "kg", "PAINT_POWDER", 500, 100, 45, "A区-2号"),
        ("MAT-004", "氟碳漆(红)", "FC-Red-5L", "L", "PAINT_POWDER", 80, 20, 180, "A区-3号"),
        ("MAT-005", "环氧底漆", "EP-Primer-5L", "L", "PAINT_POWDER", 60, 20, 120, "A区-3号"),
        # 耗材类
        ("MAT-010", "稀释剂", "XY-5L", "L", "CONSUMABLE", 100, 30, 35, "B区-1号"),
        ("MAT-011", "固化剂", "CUR-1kg", "kg", "CONSUMABLE", 80, 20, 90, "B区-1号"),
        ("MAT-012", "砂纸(800目)", "SP-800", "包", "CONSUMABLE", 50, 10, 25, "B区-2号"),
        ("MAT-013", "百格刀测试胶带", "3M-610", "卷", "CONSUMABLE", 30, 5, 18, "B区-2号"),
        # 原材料类(前处理用药剂)
        ("MAT-020", "脱脂剂", "DG-25kg", "kg", "RAW_MATERIAL", 300, 100, 12, "C区-1号"),
        ("MAT-021", "磷化液", "PH-25kg", "kg", "RAW_MATERIAL", 200, 50, 28, "C区-1号"),
        # 成品类(喷涂后成品虚拟物料,用于入库记账)
        ("MAT-099", "喷涂成品(虚拟)", "FINISHED", "件", "FINISHED_GOOD", 0, 0, 0, "成品仓"),
    ]
    for code, name, spec, unit, cat, stock, safety, cost, loc in items:
        if not db.query(InventoryItem).filter(InventoryItem.code == code).first():
            db.add(InventoryItem(code=code, name=name, spec=spec, unit=unit, category=cat,
                                 stock_qty=stock, safety_qty=safety, unit_cost=cost, location=loc, status="ACTIVE"))


def _accounts(db):
    accounts = [
        # 资产类
        ("1001", "库存现金", None, "ASSET", "DEBIT", 1, 1),
        ("1002", "银行存款", None, "ASSET", "DEBIT", 1, 1),
        ("1122", "应收账款", None, "ASSET", "DEBIT", 1, 1),
        ("1123", "预付账款", None, "ASSET", "DEBIT", 1, 0),
        ("1401", "原材料", None, "ASSET", "DEBIT", 1, 1),
        ("1403", "周转材料", None, "ASSET", "DEBIT", 1, 0),
        ("1405", "库存商品", None, "ASSET", "DEBIT", 1, 1),
        ("5001", "生产成本", None, "ASSET", "DEBIT", 1, 1),
        ("5101", "制造费用", None, "ASSET", "DEBIT", 1, 1),
        # 负债类
        ("2202", "应付账款", None, "LIABILITY", "CREDIT", 1, 1),
        ("2203", "预收账款", None, "LIABILITY", "CREDIT", 1, 1),
        ("2211", "应付职工薪酬", None, "LIABILITY", "CREDIT", 1, 1),
        ("2221", "应交税费", None, "LIABILITY", "CREDIT", 1, 1),
        # 损益类
        ("6001", "主营业务收入", None, "REVENUE", "CREDIT", 1, 1),
        ("6401", "主营业务成本", None, "EXPENSE", "DEBIT", 1, 1),
        ("6601", "销售费用", None, "EXPENSE", "DEBIT", 1, 0),
        ("6602", "管理费用", None, "EXPENSE", "DEBIT", 1, 1),
        ("6603", "财务费用", None, "EXPENSE", "DEBIT", 1, 0),
    ]
    for code, name, parent, t, direction, is_req, level in accounts:
        if not db.query(Account).filter(Account.code == code).first():
            db.add(Account(code=code, name=name, parent_code=parent, type=t,
                           direction=direction, is_required=is_req, level=level, status="ACTIVE"))


def _channels(db):
    chs = [
        ("FEISHU", "飞书", {"webhook_url": ""}),
        ("WECOM_WORK", "企业微信", {"webhook_url": ""}),
        ("EMAIL", "邮件", {"smtp_host": ""}),
        ("INAPP", "站内信", {}),
    ]
    for ch, name, cfg in chs:
        if not db.query(NotificationChannel).filter(NotificationChannel.channel == ch).first():
            db.add(NotificationChannel(channel=ch, name=name, config=cfg, status="ACTIVE"))


def _alerts(db):
    rules = [
        ("LOW_MARGIN", "毛利率低于15%", "GROSS_MARGIN", {"op": "<", "value": 0.15}, ["INAPP"], ["GM", "FINANCE"]),
        ("AR_OVERDUE", "应收超期(>0元)", "RECEIVABLE_AGING", {"op": ">", "value": 0}, ["INAPP"], ["GM", "FINANCE"]),
        ("STOCK_LOW", "物料低于安全库存", "STOCK_LOW", {"op": ">", "value": 0}, ["INAPP"], ["WAREHOUSE", "FINANCE"]),
        ("UTIL_LOW", "涂料利用率<70%", "UTILIZATION_LOW", {"op": "<", "value": 70}, ["INAPP"], ["OPERATION", "GM"]),
    ]
    for code, name, metric, cond, chs, recvs in rules:
        if not db.query(AlertRule).filter(AlertRule.code == code).first():
            db.add(AlertRule(code=code, name=name, metric=metric, condition=cond,
                             channels=chs, recipients=recvs, enabled=True))


if __name__ == "__main__":
    seed()
