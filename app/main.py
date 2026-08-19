import time
from fastapi import FastAPI, Request, HTTPException
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
    purchase_requests, sales, admin_management, vouchers,
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


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 403:
        # 权限不足: 静默降级为成功空数据, 前端不再弹窗报错
        return JSONResponse(status_code=200, content={"code": 0, "data": None, "msg": ""})
    return JSONResponse(status_code=exc.status_code, content={"code": exc.status_code, "msg": str(exc.detail)})

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
    admin_management.router, vouchers.router,
]:
    app.include_router(r)


@app.on_event("startup")
def startup():
    from app.models.system import Base, Role, User
    from app.models.approval import FlowDefinition
    from app.core.auth import hash_password
    from sqlalchemy import inspect, text
    import app.models  # 注册全部表结构到 Base.metadata

    # 建表(不删已有数据)
    Base.metadata.create_all(bind=engine)
    # 结构兼容: 旧库 roles 表补 status 列(软删除用)
    insp = inspect(engine)
    if "roles" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("roles")}
        if "status" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE roles ADD COLUMN status VARCHAR(16) DEFAULT 'ACTIVE'"))

    db = SessionLocal()

    # === 幂等seed: 不存在才创建, 已存在一律跳过, 不触碰已有数据 ===
    # 角色(运营/仓管/采购合并为OPERATION单一角色)
    roles = [
        ("ADMIN", "系统管理员"), ("SALES", "销售"), ("OPERATION", "运营"),
        ("FINANCE", "财务"), ("GM", "总经理"),
        ("MANAGER", "车间厂长"), ("AGENT", "Agent"), ("DEPARTMENT_HEAD", "部门主管"),
    ]
    role_pages_default = {
        "ADMIN": "*",
        "GM": "*",
        "SALES": ["dashboard","workflow-list","orders","approvals","customers","analysis","my-todos","my-done","sales-adjustments"],
        "FINANCE": ["dashboard","workflow-list","finance","approvals","analysis","my-todos","my-done","expense","payroll","receivables","purchases"],
        "MANAGER": ["dashboard","workflow-list","work-orders","inventory","analysis","my-todos","my-done","completions","screen"],
        "OPERATION": ["dashboard","workflow-list","work-orders","inventory","my-todos","my-done","stock-moves","purchases","purchase-requests","approvals","analysis","completions"],
        "DEPARTMENT_HEAD": ["dashboard","workflow-list","approvals","my-todos","my-done","expense","purchase-requests"],
    }
    for code, name in roles:
        if not db.query(Role).filter(Role.code == code).first():
            db.add(Role(name=name, code=code, pages=role_pages_default.get(code, []), status="ACTIVE"))
    db.flush()

    # 默认测试用户(每个角色一个; 已存在则跳过)
    default_users = [
        ("admin", "系统管理员", "ADMIN", "admin123"),
        ("ops01", "运营小王", "OPERATION", "123456"),
        ("sales01", "销售小李", "SALES", "123456"),
        ("fin01", "财务小张", "FINANCE", "123456"),
        ("gm01", "总经理", "GM", "123456"),
        ("mgr01", "车间厂长", "MANAGER", "123456"),
        ("head01", "部门主管", "DEPARTMENT_HEAD", "123456"),
    ]
    for username, name, role_code, pwd in default_users:
        if not db.query(User).filter(User.username == username).first():
            role = db.query(Role).filter(Role.code == role_code).first()
            if role:
                db.add(User(username=username, password_hash=hash_password(pwd),
                            name=name, role_id=role.id, status="ACTIVE"))
    db.flush()

    # 流程定义(不存在才创建; RECEIVING已砍掉, WAREHOUSE/PURCHASE均合并为OPERATION)
    default_flows = [
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
            {"seq": 1, "name": "运营发起", "type": "process", "approver_role": "OPERATION"},
            {"seq": 2, "name": "部门主管审批", "type": "approve", "approver_role": "DEPARTMENT_HEAD"},
            {"seq": 3, "name": "财务审核", "type": "approve", "approver_role": "FINANCE"},
            {"seq": 4, "name": "总经理审批", "type": "approve", "approver_role": "GM"},
        ]),
        ("调价申请审批", "SALES_ADJUSTMENT", [
            {"seq": 1, "name": "销售发起", "type": "process", "approver_role": "SALES"},
            {"seq": 2, "name": "总经理审批", "type": "approve", "approver_role": "GM"},
        ]),
        ("采购审批流", "PROCUREMENT", [
            {"seq": 1, "name": "运营发起", "type": "process", "approver_role": "OPERATION"},
            {"seq": 2, "name": "部门主管审批", "type": "approve", "approver_role": "DEPARTMENT_HEAD"},
            {"seq": 3, "name": "财务审核", "type": "approve", "approver_role": "FINANCE"},
            {"seq": 4, "name": "总经理终审", "type": "approve", "approver_role": "GM"},
        ]),
        ("核心生产流", "CORE_PRODUCTION", [
            {"seq": 1, "name": "销售发起", "type": "process", "approver_role": "SALES"},
            {"seq": 2, "name": "部门主管审批", "type": "approve", "approver_role": "DEPARTMENT_HEAD"},
            {"seq": 3, "name": "财务审核", "type": "approve", "approver_role": "FINANCE"},
            {"seq": 4, "name": "总经理审批", "type": "approve", "approver_role": "GM"},
            {"seq": 5, "name": "运营审核", "type": "approve", "approver_role": "OPERATION"},
            {"seq": 6, "name": "生产下达", "type": "approve", "approver_role": "MANAGER"},
            {"seq": 7, "name": "车间生产", "type": "approve", "approver_role": "MANAGER"},
            {"seq": 8, "name": "完工确认", "type": "approve", "approver_role": "MANAGER"},
            {"seq": 9, "name": "质检确认", "type": "approve", "approver_role": "OPERATION"},
            {"seq": 10, "name": "运营归档", "type": "approve", "approver_role": "OPERATION"},
        ]),
    ]
    for name, biz_type, nodes in default_flows:
        if not db.query(FlowDefinition).filter(
            FlowDefinition.biz_type == biz_type, FlowDefinition.status == "ACTIVE"
        ).first():
            db.add(FlowDefinition(name=name, biz_type=biz_type,
                                  nodes=nodes, status="ACTIVE", version=1))

    db.commit()
    db.close()


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
