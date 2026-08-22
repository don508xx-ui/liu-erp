"""
Agent API - scoped, 沙箱
- 只读查询(限定表)
- 写 alert_rules/report_templates/flow_definitions
- 禁止直写 finance_docs/inventory_txns
- 禁止改表结构(走migration提案)
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.core.db import get_db
from app.core.auth import get_current_agent
from app.models.system import AgentApiToken
from app.models.analysis import AlertRule, ReportTemplate, KpiSnapshot
from app.models.notification import NotificationTemplate
from app.models.approval import FlowDefinition
from app.api.approvals import bjt_now
from app.schemas import Resp

router = APIRouter(prefix="/api/agent/v1", tags=["agent"])


# 允许Agent查询的表白名单
ALLOWED_TABLES = {
    "orders", "order_items", "customers", "work_orders", "completions", "completion_items",
    "inventory_items", "inventory_txns", "material_requisitions", "customer_consign_log",
    "finance_docs", "finance_items", "work_order_costs", "payroll_runs", "accounts_chart",
    "purchases", "purchase_items", "suppliers", "purchase_requests",
    "kpi_snapshots", "alert_logs",
}

# Agent禁写的表
FORBIDDEN_WRITE_TABLES = {
    "finance_docs", "finance_items", "inventory_txns", "audit_logs",
    "users", "roles", "agent_api_tokens",
}


def _check_scope(token: AgentApiToken, perm: str):
    scopes = token.scopes or []
    if "*" in scopes or perm in scopes:
        return
    if f"{perm.split(':')[0]}:*" in scopes:
        return
    raise HTTPException(403, f"Agent scope不足,需{perm}")


@router.get("/schema")
def schema(token: AgentApiToken = Depends(get_current_agent), db: Session = Depends(get_db)):
    _check_scope(token, "read:schema")
    from app.core.db import Base
    tables = {}
    for name, t in Base.metadata.tables.items():
        if name not in ALLOWED_TABLES:
            continue
        tables[name] = [{"name": c.name, "type": str(c.type), "nullable": c.nullable} for c in t.columns]
    return {"code": 0, "data": tables}


@router.post("/query")
def query(body: dict, token: AgentApiToken = Depends(get_current_agent), db: Session = Depends(get_db)):
    _check_scope(token, "read:query")
    table = body.get("table")
    if table not in ALLOWED_TABLES:
        raise HTTPException(403, f"表{table}不在允许列表")
    limit = min(int(body.get("limit", 100)), 500)
    where = body.get("where")  # dict, 转成 = 条件
    from sqlalchemy import text
    from app.core.db import Base
    import re
    # 安全: 表已白名单, 但 fields/where键 仍直接拼SQL, 必须逐字校验标识符且属于该表真实列
    valid_cols = {c.name for c in Base.metadata.tables[table].columns}
    ident_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    fields = body.get("fields", "*")
    if isinstance(fields, list):
        bad = [f for f in fields if not isinstance(f, str) or f not in valid_cols]
        if bad:
            raise HTTPException(400, f"非法字段: {bad}")
        fields_sql = ",".join(fields)
    elif fields == "*":
        fields_sql = "*"
    else:
        raise HTTPException(400, "fields必须是*或列名数组")

    sql = f"SELECT {fields_sql} FROM {table}"
    params = {}
    if where:
        clauses = []
        for k, v in where.items():
            if not ident_re.match(str(k)) or k not in valid_cols:
                raise HTTPException(400, f"非法条件字段: {k}")
            clauses.append(f"{k} = :{k}")
            params[k] = v
        sql += " WHERE " + " AND ".join(clauses)
    sql += f" LIMIT {limit}"
    rows = db.execute(text(sql), params).mappings().all()
    return {"code": 0, "data": [dict(r) for r in rows]}


@router.post("/alert-rules")
def create_alert_rule(body: dict, token: AgentApiToken = Depends(get_current_agent), db: Session = Depends(get_db)):
    _check_scope(token, "write:alert_rules")
    r = AlertRule(code=body["code"], name=body["name"], metric=body["metric"],
                  condition=body["condition"], channels=body.get("channels", ["INAPP"]),
                  recipients=body.get("recipients", []), enabled=True)
    db.add(r)
    db.commit()
    return Resp.ok({"id": r.id})


@router.post("/report-templates")
def create_report(body: dict, token: AgentApiToken = Depends(get_current_agent), db: Session = Depends(get_db)):
    _check_scope(token, "write:report_templates")
    r = ReportTemplate(code=body["code"], name=body["name"], type="CUSTOM",
                       data_source=body.get("data_source", ""), config=body.get("config", {}))
    db.add(r)
    db.commit()
    return Resp.ok({"id": r.id})


@router.post("/flow-definitions")
def create_flow(body: dict, token: AgentApiToken = Depends(get_current_agent), db: Session = Depends(get_db)):
    _check_scope(token, "write:flow_definitions")
    fd = FlowDefinition(name=body["name"], biz_type=body["biz_type"],
                        nodes=body["nodes"], status="ACTIVE")
    db.add(fd)
    db.commit()
    return Resp.ok({"id": fd.id})


@router.get("/kpi")
def agent_kpi(token: AgentApiToken = Depends(get_current_agent), db: Session = Depends(get_db)):
    _check_scope(token, "read:analysis")
    from app.api.analysis import kpi
    # 复用KPI计算
    last = db.query(KpiSnapshot).order_by(KpiSnapshot.id.desc()).first()
    if last:
        return {"code": 0, "data": last.metrics}
    return {"code": 0, "data": None}


# Migration提案(人工审批,Agent不直接执行)
@router.post("/migration/propose")
def propose_migration(body: dict, token: AgentApiToken = Depends(get_current_agent), db: Session = Depends(get_db)):
    _check_scope(token, "write:migration_proposal")
    # 仅记录提案,不执行
    from app.models.system import AuditLog
    from datetime import datetime
    log = AuditLog(user_id=None, user_name=f"agent:{token.name}",
                   action="migration_proposal", entity_type="migration",
                   entity_id=0, after=body, created_at=bjt_now())
    db.add(log)
    db.commit()
    return Resp.ok({"status": "proposed, pending human approval"})
