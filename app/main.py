import time
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
import sqlite3

from app.core.db import engine, SessionLocal
from app.config import settings

from app.api import (
    auth, workbench, dicts, orders, customers, inventory, purchases,
    work_orders, completions, finance, payroll, requisitions,
    notifications, approvals, agent, analysis, ai_analysis, expense,
    purchase_requests, sales, admin_management, vouchers, ai_finance,
    acceptances, shipments, stock_check, loans, outsource, prepayments,
    ai_ops, employees,
)

# 安全: 生产关闭API文档(防止接口结构泄露), 本地调试置 ENABLE_DOCS=true
_docs_url = "/docs" if settings.ENABLE_DOCS else None
app = FastAPI(title="峰业精密ERP", docs_url=_docs_url, redoc_url=None,
              openapi_url="/openapi.json" if settings.ENABLE_DOCS else None)

# 安全: CORS白名单制。前后端同域部署无需跨域; 仅在显式配置域名时放行
_cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# 压缩: 静态文件(app.js 668KB→~150KB)和API响应统一gzip, 最低阈值1KB避免压小响应
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def no_cache_middleware(request: Request, call_next):
    # L1短路: 任何探活路径前缀(health/healthz/live/ready/ping)直接回200 JSON,
    # 不走路由/DB/静态/中间件链, <1ms, GET/HEAD/OPTION都过, 彻底绕过PaaS探活配置不一致
    p = request.url.path.rstrip('/') or '/'
    if p in ('/health','/healthz','/live','/ready','/ping','') and p != '/':
        return JSONResponse(status_code=200, content={"status":"ok","ts":time.time()},
                            media_type="application/json")

    # 全局请求体大小上限: 防御超大负载/恶意大包攻击(文件上传走独立接口,默认阈值不影响)
    if request.method in ("POST", "PUT", "PATCH") and (request.headers.get("content-length") or 0):
        try:
            if int(request.headers["content-length"]) > settings.MAX_BODY_MB * 1024 * 1024:
                return JSONResponse(status_code=413, content={"code":413,"msg":f"请求体超过上限{settings.MAX_BODY_MB}MB"})
        except ValueError:
            pass

    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    # 安全响应头: 防点击劫持/MIME嗅探/Referrer泄露
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.ENABLE_HSTS:
        # 强制HTTPS: 浏览器后续只走TLS, 抗中间人/降级攻击(Zeabur原生TLS)
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    if settings.HIDE_SERVER_HEADER:
        response.headers["Server"] = ""
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
    # 安全: 不把内部异常细节(SQL/路径/堆栈)回给客户端, 仅服务端日志留痕
    return JSONResponse(status_code=500, content={"code": 500, "msg": "服务器内部错误,请稍后重试"})


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
    sales.sample_router, sales.deli_router, sales.adj_router,
    admin_management.router, vouchers.router, ai_finance.router,
    acceptances.router, shipments.router, stock_check.router, loans.router,
    outsource.router, prepayments.router,
    ai_ops.router, employees.router,
]:
    app.include_router(r)


@app.on_event("startup")
def startup():
    from app.models.system import Base, Role, User
    from app.core.auth import hash_password
    from sqlalchemy import inspect, text
    import app.models  # 注册全部表结构到 Base.metadata

    # 安全自检: 默认密钥/弱密码在生产是致命隐患, 启动即大字告警
    import logging
    if settings.JWT_SECRET == "dev-secret-change-me":
        logging.warning("=" * 60)
        logging.warning("[安全告警] JWT_SECRET 正在使用默认值! 任何人可伪造管理员token!")
        logging.warning("[安全告警] 请立即在环境变量中配置强随机 JWT_SECRET!")
        logging.warning("=" * 60)

    # 建表(不删已有数据)
    Base.metadata.create_all(bind=engine)
    # 结构兼容: 旧库缺新列时(模型新增列), create_all 不会改已有表, 在此通用补齐
    # 只补可安全 ADD COLUMN 的列: 可空 或 有默认值(避免NOT NULL无默认导致SQLite报错)
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    if existing_tables:
        with engine.begin() as conn:
            for _tname, _table in Base.metadata.tables.items():
                if _tname not in existing_tables:
                    continue
                existing_cols = {c["name"] for c in insp.get_columns(_tname)}
                for col in _table.columns:
                    if col.name in existing_cols:
                        continue
                    # 跳过不可安全补的列(主键/不可空且无默认)
                    if col.primary_key or not (col.nullable or col.default is not None or col.server_default is not None):
                        continue
                    coltype = col.type.compile(dialect=engine.dialect)
                    ddl = f"ALTER TABLE {_tname} ADD COLUMN {col.name} {coltype}"
                    if col.server_default is not None:
                        arg = col.server_default.arg
                        if isinstance(arg, str):
                            ddl += f" DEFAULT '{arg}'"
                    conn.execute(text(ddl))
    # 结构兼容: 旧库 roles 表补 status 列(软删除用, 默认值ACTIVE)
    insp = inspect(engine)
    if "roles" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("roles")}
        if "status" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE roles ADD COLUMN status VARCHAR(16) DEFAULT 'ACTIVE'"))

    # === 结构兼容迁移: 旧库 work_orders 缺 customer_id/customer_name (v20260819) ===
    if "work_orders" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("work_orders")}
        with engine.begin() as conn:
            if "customer_id" not in cols:
                conn.execute(text("ALTER TABLE work_orders ADD COLUMN customer_id INTEGER"))
            if "customer_name" not in cols:
                conn.execute(text("ALTER TABLE work_orders ADD COLUMN customer_name VARCHAR(128)"))
            # 回填 customer_id/customer_name
            conn.execute(text("""
                UPDATE work_orders SET
                    customer_id = (SELECT o.customer_id FROM orders o WHERE o.id = work_orders.order_id),
                    customer_name = (SELECT COALESCE(cu.name,'') FROM customers cu WHERE cu.id = (SELECT o.customer_id FROM orders o WHERE o.id = work_orders.order_id))
                WHERE (work_orders.customer_id IS NULL OR work_orders.customer_name IS NULL OR work_orders.customer_name = '')
                  AND work_orders.order_id IS NOT NULL
            """))

    db = SessionLocal()

    # === 迁移: roles表添加status列(老库缺) ===
    try:
        db.execute(text("ALTER TABLE roles ADD COLUMN status VARCHAR(16) DEFAULT 'ACTIVE'"))
    except Exception:
        pass  # 已存在则忽略

    # === 迁移: users表添加pages列(老库缺) ===
    try:
        db.execute(text("ALTER TABLE users ADD COLUMN pages TEXT"))
    except Exception:
        pass

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
        "SALES": ["dashboard","workflow-list","orders","approvals","customers","my-todos","my-done","sales-adjustments"],
        "FINANCE": ["dashboard","workflow-list","finance","approvals","my-todos","my-done","expense","payroll","receivables","purchases","vouchers","reports","accounts","acceptances","loan-request","prepayments"],
        "MANAGER": ["dashboard","workflow-list","work-orders","inventory","my-todos","my-done","completions","screen","outsource"],
        "OPERATION": ["dashboard","workflow-list","work-orders","inventory","my-todos","my-done","stock-moves","purchases","purchase-requests","approvals","completions","outsource"],
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

    # === 幂等seed: 编号规则 (复用db会话, 同事务避免SQLite锁冲突) ===
    from app.core.number_gen import ensure_default_rules
    ensure_default_rules(db=db)

    # === 幂等seed: 财务角色追加 ai-finance/acceptances/loan-request/prepayments 页面权限 (老库已存角色则补充该入口) ===
    fin_role = db.query(Role).filter(Role.code == "FINANCE").first()
    if fin_role and fin_role.pages and fin_role.pages != "*":
        _pages = list(fin_role.pages)
        for _p in ("ai-finance", "acceptances", "loan-request", "prepayments"):
            if _p not in _pages:
                _pages.append(_p)
        fin_role.pages = _pages

    # === 幂等seed: 厂长/运营角色追加 outsource(外协单) 页面权限 ===
    for _rc in ("MANAGER", "OPERATION"):
        _r = db.query(Role).filter(Role.code == _rc).first()
        if _r and _r.pages and _r.pages != "*":
            _pages = list(_r.pages)
            if "outsource" not in _pages:
                _pages.append("outsource")
                _r.pages = _pages

    # === 幂等seed: 全业务模拟数据(空库才跑,有数据跳过) ===
    from app.seed_data import seed_biz_data
    seed_biz_data(db)

    # === 幂等seed: 资金账户统一称谓(峰业精密机械/东莞加工厂/现金) ===
    from app.models.fund import FundAccount
    _acc_rename = {"机械公账": "峰业精密机械", "加工厂公账": "东莞加工厂", "库存现金": "现金"}
    for _fa in db.query(FundAccount).all():
        if _fa.name in _acc_rename:
            _fa.name = _acc_rename[_fa.name]

    # === 幂等seed: 审批流程定义升级(称谓统一 + 审批链按业务调整) ===
    from app.models.approval import FlowDefinition
    import json as _json

    def _seed_flow(name, biz_type, start_node, tail_nodes):
        """幂等升级/新增流程定义: 保留seq1发起节点(含表单), 重定向后续审批链"""
        fd = db.query(FlowDefinition).filter(
            FlowDefinition.biz_type == biz_type, FlowDefinition.status == "ACTIVE"
        ).first()
        nodes = ([start_node] if start_node else []) + tail_nodes
        if fd:
            old = fd.nodes or []
            if old == nodes:
                fd.name = name
                return
            fd.name = name
            fd.nodes = nodes
            fd.version = (fd.version or 1) + 1
        else:
            db.add(FlowDefinition(name=name, biz_type=biz_type,
                                  nodes=nodes, status="ACTIVE", version=1))

    def _first_node(biz_type):
        fd = db.query(FlowDefinition).filter(
            FlowDefinition.biz_type == biz_type, FlowDefinition.status == "ACTIVE"
        ).first()
        nodes = fd.nodes if fd and fd.nodes else []
        return nodes[0] if nodes else None

    # A) 销售下单(核心生产流): 销售→销售经理→总经理→运营→打印
    _seed_flow("订单生产审批(运营转工单)", "CORE_PRODUCTION",
               _first_node("CORE_PRODUCTION"), [
        {"seq": 2, "name": "销售经理审批", "type": "approve", "approver_role": "SALES_MANAGER"},
        {"seq": 3, "name": "总经理审批", "type": "approve", "approver_role": "GM"},
        {"seq": 4, "name": "运营确认", "type": "approve", "approver_role": "OPERATION"},
        {"seq": 5, "name": "打印", "type": "end"},
    ])
    # B) 调价申请: 销售→销售经理→总经理→抄送财务→抄送运营
    _seed_flow("调价申请审批", "SALES_ADJUSTMENT",
               _first_node("SALES_ADJUSTMENT"), [
        {"seq": 2, "name": "销售经理审批", "type": "approve", "approver_role": "SALES_MANAGER"},
        {"seq": 3, "name": "总经理审批", "type": "approve", "approver_role": "GM"},
        {"seq": 4, "name": "抄送财务", "type": "cc", "cc_roles": ["FINANCE"], "approver_role": "FINANCE"},
        {"seq": 5, "name": "抄送运营", "type": "cc", "cc_roles": ["OPERATION"], "approver_role": "OPERATION"},
    ])
    # C) 费用报销: 销售报销→销售经理→总经理→抄送财务(支付); 其他(含运营/财务)报销→直接总经理
    _seed_flow("费用报销审批", "EXPENSE",
               _first_node("EXPENSE"), [
        {"seq": 2, "name": "销售报销分支", "type": "branch",
         "condition": "initiator_role == 'SALES' or initiator_role == 'SALES_MANAGER' or initiator_role == 'SALES_VICE_MANAGER'"},
        {"seq": 3, "name": "销售经理审批", "type": "approve", "approver_role": "SALES_MANAGER"},
        {"seq": 4, "name": "总经理终审", "type": "approve", "approver_role": "GM"},
        {"seq": 5, "name": "抄送财务(支付)", "type": "cc", "cc_roles": ["FINANCE"], "approver_role": "FINANCE"},
    ])
    # D) 返工/退货: 销售→销售经理→运营→打印, 抄送总经理和财务
    _rw_ret_tail = [
        {"seq": 2, "name": "销售经理审批", "type": "approve", "approver_role": "SALES_MANAGER"},
        {"seq": 3, "name": "运营确认", "type": "approve", "approver_role": "OPERATION"},
        {"seq": 4, "name": "抄送总经理", "type": "cc", "cc_roles": ["GM"], "approver_role": "GM"},
        {"seq": 5, "name": "抄送财务", "type": "cc", "cc_roles": ["FINANCE"], "approver_role": "FINANCE"},
        {"seq": 6, "name": "打印", "type": "end"},
    ]
    _seed_flow("返工申请", "REWORK",
               {"seq": 1, "name": "销售发起", "type": "process", "approver_role": "SALES"},
               _rw_ret_tail)
    _seed_flow("退货申请", "RETURN",
               {"seq": 1, "name": "销售发起", "type": "process", "approver_role": "SALES"},
               _rw_ret_tail)
    # E) 借款申请: 审批人→总经理终审(财务负责打款)
    _seed_flow("借款申请审批", "LOAN",
               {"seq": 1, "name": "借支发起", "type": "process", "approver_role": "DEPARTMENT_HEAD"}, [
        {"seq": 2, "name": "审批人审批", "type": "approve", "approver_role": "DEPARTMENT_HEAD"},
        {"seq": 3, "name": "总经理终审", "type": "approve", "approver_role": "GM"},
    ])

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
