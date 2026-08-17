import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import event
from sqlalchemy.engine import Engine
import sqlite3

from app.core.db import engine, SessionLocal
from app.models.approval import FlowDefinition

from app.api import (
    auth, workbench, dicts, orders, customers, inventory, purchases,
    work_orders, completions, finance, payroll, requisitions,
    notifications, approvals, agent, analysis, ai_analysis, expense,
    purchase_requests, sales, admin_management,
)

app = FastAPI(title="峰业精密ERP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_middleware(request: Request, call_next):
    # L1短路: 任何探活路径前缀(health/healthz/live/ready/ping)直接回200 JSON,
    # 不走路由/DB/静态/中间件链, <1ms, GET/HEAD/OPTION都过, 彻底绕过PaaS探活配置不一致
    p = request.url.path.rstrip('/') or '/'
    if p in ('/health','/healthz','/live','/ready','/ping','') and p != '/':
        return JSONResponse(status_code=200, content={"status":"ok","ts":time.time()},
                            media_type="application/json")
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.exception_handler(Exception)
async def global_exc_handler(request: Request, exc: Exception):
    import traceback
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"code": 500, "msg": str(exc)})


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


for r in [
    auth.router, workbench.router, dicts.router, orders.router, customers.router,
    inventory.router, purchases.router, work_orders.router, completions.router,
    finance.router, payroll.router, requisitions.router,
    notifications.router, approvals.router, agent.router, analysis.router,
    ai_analysis.router, expense.router, purchase_requests.router,
    sales.company_router, sales.contract_router, sales.oppo_router,
    sales.recv_router, sales.deli_router, sales.adj_router,
    admin_management.router,
]:
    app.include_router(r)


@app.on_event("startup")
def startup():
    from app.models.system import Base, Role, User
    from app.core.auth import hash_password
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # Ensure roles
    roles = [
        ("ADMIN", "系统管理员"), ("SALES", "销售"), ("OPERATION", "运营助理"),
        ("FINANCE", "财务"), ("WAREHOUSE", "仓管"), ("GM", "总经理"),
        ("MANAGER", "车间厂长"), ("AGENT", "Agent"), ("DEPARTMENT_HEAD", "部门主管"),
        ("PURCHASE", "采购"),
    ]
    role_map = {}
    # 角色页面权限配置
    role_pages_default = {
        "ADMIN": "*",
        "GM": "*",
        "SALES": ["dashboard","workflow-list","orders","approvals","customers","analysis","my-todos","my-done","sales-adjustments","receiving"],
        "FINANCE": ["dashboard","workflow-list","finance","approvals","analysis","my-todos","my-done","expense","payroll","receivables","purchases"],
        "WAREHOUSE": ["dashboard","workflow-list","inventory","my-todos","my-done","stock-moves","purchases","receiving"],
        "MANAGER": ["dashboard","workflow-list","work-orders","inventory","analysis","my-todos","my-done","completions","screen"],
        "OPERATION": ["dashboard","workflow-list","work-orders","approvals","analysis","my-todos","my-done","receiving","completions"],
        "PURCHASE": ["dashboard","workflow-list","approvals","my-todos","my-done","purchase-requests","purchases"],
        "DEPARTMENT_HEAD": ["dashboard","workflow-list","approvals","my-todos","my-done","expense","purchase-requests"],
    }
    for code, name in roles:
        r = db.query(Role).filter(Role.code == code).first()
        if not r:
            r = Role(name=name, code=code, pages=role_pages_default.get(code, []))
            db.add(r); db.flush()
        else:
            # 强制更新pages权限（如果与默认值不同）
            default_pages = role_pages_default.get(code, [])
            if r.pages != default_pages:
                r.pages = default_pages
        role_map[code] = r.id
    
    db.commit()

    # Ensure default admin
    if not db.query(User).filter(User.username == "admin").first():
        db.add(User(username="admin", password_hash=hash_password("admin123"),
                    name="系统管理员", role_id=role_map["ADMIN"], status="ACTIVE"))

    # Ensure default users (for testing)
    default_users = [
        ("purchase01", "采购小王", "PURCHASE", "123456"),
    ]
    for username, name, role_code, pwd in default_users:
        if not db.query(User).filter(User.username == username).first():
            db.add(User(username=username, password_hash=hash_password(pwd),
                        name=name, role_id=role_map[role_code], status="ACTIVE"))

    # Default flow definitions
    default_flows = [
        ("来货登记流程", "RECEIVING", [
            {"seq": 1, "name": "仓管发起", "type": "process", "approver_role": "WAREHOUSE"},
            {"seq": 2, "name": "运营核对", "type": "approve", "approver_role": "OPERATION"},
            {"seq": 3, "name": "财务入账", "type": "approve", "approver_role": "FINANCE"},
            {"seq": 4, "name": "归档", "type": "process", "approver_role": "OPERATION"},
        ]),
        ("完工单确认", "COMPLETION", [
            {"seq": 1, "name": "厂长发起", "type": "process", "approver_role": "MANAGER"},
            {"seq": 2, "name": "质检确认", "type": "approve", "approver_role": "MANAGER"},
            {"seq": 3, "name": "运营归档", "type": "approve", "approver_role": "OPERATION"},
        ]),
        ("费用报销审批", "EXPENSE", [
            {"seq": 1, "name": "员工发起", "type": "process", "approver_role": "DEPARTMENT_HEAD"},
            {"seq": 2, "name": "部门主管初审", "type": "approve", "approver_role": "DEPARTMENT_HEAD"},
            {"seq": 3, "name": "财务审核", "type": "approve", "approver_role": "FINANCE"},
            {"seq": 4, "name": "总经理终审", "type": "approve", "approver_role": "GM"},
        ]),
        ("采购请求审批", "PURCHASE_REQUEST", [
            {"seq": 1, "name": "采购发起", "type": "process", "approver_role": "PURCHASE"},
            {"seq": 2, "name": "部门主管审批", "type": "approve", "approver_role": "DEPARTMENT_HEAD"},
            {"seq": 3, "name": "财务审核", "type": "approve", "approver_role": "FINANCE"},
            {"seq": 4, "name": "总经理审批", "type": "approve", "approver_role": "GM"},
        ]),
        ("调价申请审批", "SALES_ADJUSTMENT", [
            {"seq": 1, "name": "销售发起", "type": "process", "approver_role": "SALES"},
            {"seq": 2, "name": "总经理审批", "type": "approve", "approver_role": "GM"},
        ]),
        ("采购审批流", "PROCUREMENT", [
            {"seq": 1, "name": "采购发起", "type": "process", "approver_role": "PURCHASE"},
            {"seq": 2, "name": "部门主管审批", "type": "approve", "approver_role": "DEPARTMENT_HEAD"},
            {"seq": 3, "name": "财务审核", "type": "approve", "approver_role": "FINANCE"},
            {"seq": 4, "name": "总经理终审", "type": "approve", "approver_role": "GM"},
        ]),
        ("核心生产流", "CORE_PRODUCTION", [
            {"seq": 1, "name": "销售发起", "type": "process", "approver_role": "SALES"},
            {"seq": 2, "name": "部门主管审批", "type": "approve", "approver_role": "DEPARTMENT_HEAD"},
            {"seq": 3, "name": "财务审核", "type": "approve", "approver_role": "FINANCE"},
            {"seq": 4, "name": "总经理审批", "type": "approve", "approver_role": "GM"},
            {"seq": 5, "name": "仓管来货登记", "type": "approve", "approver_role": "WAREHOUSE"},
            {"seq": 6, "name": "运营核对", "type": "approve", "approver_role": "OPERATION"},
            {"seq": 7, "name": "财务入账", "type": "approve", "approver_role": "FINANCE"},
            {"seq": 8, "name": "生产下达", "type": "approve", "approver_role": "MANAGER"},
            {"seq": 9, "name": "车间生产", "type": "approve", "approver_role": "MANAGER"},
            {"seq": 10, "name": "完工确认", "type": "approve", "approver_role": "MANAGER"},
            {"seq": 11, "name": "质检确认", "type": "approve", "approver_role": "OPERATION"},
            {"seq": 12, "name": "运营归档", "type": "approve", "approver_role": "OPERATION"},
        ]),
    ]
    # 流程定义种子版本号(每次节点定义变更时递增,触发强制覆盖)
    FLOW_SEED_VERSION = 20260819
    for name, biz_type, nodes in default_flows:
        existing = db.query(FlowDefinition).filter(
            FlowDefinition.biz_type == biz_type, FlowDefinition.status == "ACTIVE"
        ).first()
        if not existing:
            db.add(FlowDefinition(name=name, biz_type=biz_type,
                                  nodes=nodes, status="ACTIVE",
                                  version=FLOW_SEED_VERSION))
        else:
            # 版本号不一致则强制覆盖节点定义
            if (existing.version or 0) < FLOW_SEED_VERSION:
                existing.nodes = nodes
                existing.name = name
                existing.version = FLOW_SEED_VERSION

    db.commit()
    db.close()


def _migrate_stale_flow_instances(db):
    """流程定义版本变更时,将旧版本RUNNING实例标记为OBSOLETE,保留审计痕迹不删除"""
    from app.models.approval import FlowInstance, FlowTask
    changed_types = []
    for name, biz_type, nodes in default_flows:
        existing = db.query(FlowDefinition).filter(
            FlowDefinition.biz_type == biz_type, FlowDefinition.status == "ACTIVE"
        ).first()
        if existing and (existing.version or 0) < FLOW_SEED_VERSION:
            changed_types.append(biz_type)
    if not changed_types:
        return
    print(f"[migrate] Flow definition changed for {changed_types}, marking stale RUNNING instances as OBSOLETE...")
    running = db.query(FlowInstance).filter(
        FlowInstance.status == "RUNNING",
        FlowInstance.biz_type.in_(changed_types)
    ).all()
    for inst in running:
        inst.status = "OBSOLETE"
    db.commit()
    print(f"[migrate] Marked {len(running)} stale instances as OBSOLETE (audit trail preserved)")


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health", tags=["system"])
@app.head("/health")
@app.get("/healthz", tags=["system"])
@app.head("/healthz")
@app.get("/live", tags=["system"])
@app.get("/ready", tags=["system"])
async def health():
    """Zeabur/K8s探活专用: 无DB无静态IO <1ms返回, 同时支持GET/HEAD + 多别名路径,
    兼容平台误读zeabur.yaml导致路径/方法不一致的场景"""
    return {"status": "ok", "ts": time.time()}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """浏览器默认favicon请求兜底: 返回1x1透明PNG(避免404导致反向代理健康检查误判)"""
    import base64
    _1x1_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )
    return Response(content=_1x1_png, media_type="image/png")


@app.get("/")
async def index():
    return FileResponse("static/index.html")
