from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role
from app.core.audit import log_audit
from app.models.system import User
from app.models.analysis import ReportTemplate, AlertRule, AlertLog, KpiSnapshot, PaymentSchedule
from app.models.finance import FinanceDoc, WorkOrderCost
from app.models.order import Order
from app.models.workshop import WorkOrder, Completion
from app.models.inventory import InventoryItem
from app.models.customer import Customer
from app.core.notify import send as notify_send
from app.core.permissions import apply_scope_filter, get_user_role_code, mask_customer
from app.core.pivot import build_pivot, list_datasets
from app.api.approvals import bjt_now
from app.schemas import Resp
from datetime import timedelta

router = APIRouter(prefix="/api/analysis", tags=["analysis"],
                   dependencies=[Depends(require_role("ADMIN", "GM"))])


# KPI看板
@router.get("/kpi")
def kpi(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # 营收=已生效订单总额
    revenue = sum(float(o.total_amount or 0) for o in db.query(Order).filter(Order.status.in_(["EFFECTIVE", "CLOSED"])).all())
    # 成本=所有工单成本
    costs = db.query(WorkOrderCost).all()
    total_cost = sum(float(c.amount or 0) for c in costs)
    by_type = {}
    for c in costs:
        by_type[c.cost_type] = by_type.get(c.cost_type, 0) + float(c.amount or 0)
    # 应收余额
    ar = db.query(FinanceDoc).filter(FinanceDoc.doc_type == "RECEIVABLE").all()
    ar_balance = sum(float(r.amount or 0) - float(r.settled_amount or 0) for r in ar)
    # 应付余额
    ap = db.query(FinanceDoc).filter(FinanceDoc.doc_type == "PAYABLE").all()
    ap_balance = sum(float(r.amount or 0) - float(r.settled_amount or 0) for r in ap)
    # 库存价值
    inv_value = sum(float(i.stock_qty or 0) * float(i.unit_cost or 0) for i in db.query(InventoryItem).all())
    # 订单数/工单数/完工数
    order_cnt = db.query(Order).count()
    wo_cnt = db.query(WorkOrder).count()
    cp_cnt = db.query(Completion).filter(Completion.status == "CONFIRMED").count()
    profit = revenue - total_cost
    margin = round(profit / revenue * 100, 2) if revenue else 0
    return {"code": 0, "data": {
        "revenue": round(revenue, 2), "cost": round(total_cost, 2),
        "cost_breakdown": by_type, "profit": round(profit, 2),
        "gross_margin_pct": margin,
        "ar_balance": round(ar_balance, 2), "ap_balance": round(ap_balance, 2),
        "inventory_value": round(inv_value, 2),
        "order_count": order_cnt, "work_order_count": wo_cnt,
        "completion_count": cp_cnt,
    }}


# 预警规则CRUD
class AlertRuleIn(BaseModel):
    code: str
    name: str
    metric: str  # GROSS_MARGIN/RECEIVABLE_AGING/STOCK_LOW/UTILIZATION_LOW
    condition: dict  # {op:"<",value:0.15}
    channels: list = ["INAPP"]
    recipients: list = []
    enabled: bool = True


@router.post("/alert-rules")
def create_rule(body: AlertRuleIn, user: User = Depends(require_role("ADMIN", "GM", "FINANCE", "AGENT")),
                db: Session = Depends(get_db)):
    r = AlertRule(**body.model_dump())
    db.add(r)
    db.commit()
    return Resp.ok({"id": r.id})


@router.get("/alert-rules")
def list_rules(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(AlertRule).all()
    return {"code": 0, "data": [{"id": r.id, "code": r.code, "name": r.name, "metric": r.metric, "condition": r.condition, "enabled": r.enabled} for r in rows]}


# 触发预警检查(定时任务也可调)
@router.post("/alert-rules/check")
def check_alerts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rules = db.query(AlertRule).filter(AlertRule.enabled == True).all()
    triggered = []
    for r in rules:
        metric_val = _get_metric(db, r.metric)
        if metric_val is None:
            continue
        cond = r.condition or {}
        op, val = cond.get("op"), cond.get("value")
        hit = False
        if op == "<" and metric_val < val:
            hit = True
        elif op == ">" and metric_val > val:
            hit = True
        elif op == "<=" and metric_val <= val:
            hit = True
        elif op == ">=" and metric_val >= val:
            hit = True
        if hit:
            msg = f"[{r.name}] {r.metric}={metric_val} {op} {val}"
            log = AlertLog(rule_id=r.id, rule_code=r.code, metric_value=str(metric_val), message=msg, sent_status="PENDING")
            db.add(log)
            db.flush()
            for ch in (r.channels or ["INAPP"]):
                notify_send(db, "alert.trigger", ch, "alert", "预警系统", {"message": msg, "rule": r.code})
            log.sent_status = "SENT"
            log.sent_at = bjt_now()
            db.flush()
            triggered.append({"rule": r.code, "metric": r.metric, "value": metric_val})
    db.commit()
    return {"code": 0, "data": triggered}


def _get_metric(db: Session, metric: str):
    if metric == "GROSS_MARGIN":
        revenue = sum(float(o.total_amount or 0) for o in db.query(Order).filter(Order.status.in_(["EFFECTIVE", "CLOSED"])).all())
        cost = sum(float(c.amount or 0) for c in db.query(WorkOrderCost).all())
        return round((revenue - cost) / revenue, 4) if revenue else None
    if metric == "RECEIVABLE_AGING":
        # 超期应收总额(含DRAFT/OPEN)
        now = bjt_now()
        rows = db.query(FinanceDoc).filter(
            FinanceDoc.doc_type == "RECEIVABLE",
            FinanceDoc.status.in_(["OPEN", "DRAFT"]),
        ).all()
        return round(sum(float(r.amount or 0) - float(r.settled_amount or 0) for r in rows if r.due_date and r.due_date < now), 2)
    if metric == "PAYMENT_OVERDUE":
        # 逾期未回款节点数
        now = bjt_now()
        return db.query(PaymentSchedule).filter(
            PaymentSchedule.due_date < now,
            PaymentSchedule.status.in_(["UPCOMING", "DUE", "OVERDUE"]),
        ).count()
    if metric == "STOCK_LOW":
        items = db.query(InventoryItem).filter(InventoryItem.category != "FINISHED_GOOD").all()
        return sum(1 for i in items if float(i.stock_qty or 0) < float(i.safety_qty or 0))
    if metric == "UTILIZATION_LOW":
        # 平均利用率
        from app.models.workshop import CompletionItem
        rows = db.query(CompletionItem).filter(CompletionItem.utilization_rate != None).all()
        if not rows:
            return None
        return round(sum(float(r.utilization_rate or 0) for r in rows) / len(rows), 2)
    return None


@router.get("/alert-logs")
def alert_logs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(AlertLog).order_by(AlertLog.id.desc()).limit(50).all()
    return {"code": 0, "data": [{"id": l.id, "rule_code": l.rule_code, "metric_value": l.metric_value, "message": l.message, "sent_status": l.sent_status, "created_at": l.created_at.isoformat() if l.created_at else None} for l in rows]}


# 涂料利用率报表
@router.get("/paint-utilization")
def paint_utilization(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.workshop import CompletionItem
    rows = db.query(CompletionItem).filter(CompletionItem.utilization_rate != None).all()
    return {"code": 0, "data": [{
        "id": r.id, "completion_id": r.completion_id, "item_name": r.item_name,
        "theoretical_qty": float(r.theoretical_qty or 0), "actual_qty": float(r.actual_qty or 0),
        "utilization_rate": float(r.utilization_rate or 0), "return_qty": float(r.return_qty or 0),
    } for r in rows]}


# ============ 动态透视分析 V2 ============

@router.get("/datasets")
def datasets(user: User = Depends(get_current_user)):
    """返回可用数据源/维度/指标,供前端选择"""
    return Resp.ok(list_datasets())


@router.get("/dataset-values")
def dataset_values(pivot_field: str, dataset: str = "orders", user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取维度字段的可选值列表(用于筛选器下拉)"""
    from app.core.pivot import _get_datasets, _load_fk_map
    ds = _get_datasets()
    if dataset not in ds:
        raise HTTPException(400, "未知数据源")
    ds_conf = ds[dataset]
    model = ds_conf["model"]

    # 1. 优先从dims中查找（维度配置更完整）
    if pivot_field in ds_conf["dims"]:
        dim_conf = ds_conf["dims"][pivot_field]
        if isinstance(dim_conf, dict) and "enum" in dim_conf:
            values = [{"value": k, "label": v} for k, v in dim_conf["enum"].items()]
            return Resp.ok({"values": values})
        if isinstance(dim_conf, dict) and "fk" in dim_conf:
            fk_model, fk_field, display_field = dim_conf["fk"]
            mapping = _load_fk_map(db, fk_model, fk_field, display_field)
            values = [{"value": name, "label": name} for _id, name in sorted(mapping.items(), key=lambda x: x[1])]
            return Resp.ok({"values": values})

    # 2. 从filter_fields中查找
    filter_field = ds_conf.get("filter_fields", {}).get(pivot_field)
    if filter_field:
        # 枚举类型
        if filter_field.get("type") == "enum" and filter_field.get("options"):
            values = [{"value": opt, "label": opt} for opt in filter_field["options"]]
            # 尝试从dims中找label映射
            if pivot_field in ds_conf["dims"]:
                dim_conf = ds_conf["dims"][pivot_field]
                if isinstance(dim_conf, dict) and "enum" in dim_conf:
                    values = [{"value": k, "label": v} for k, v in dim_conf["enum"].items()]
            return Resp.ok({"values": values})
        # 外键类型 - 需要从dims中找到对应的fk配置
        if filter_field.get("type") == "fk":
            # 尝试从dims中找到同名字段的fk配置
            if pivot_field in ds_conf["dims"]:
                dim_conf = ds_conf["dims"][pivot_field]
                if isinstance(dim_conf, dict) and "fk" in dim_conf:
                    fk_model, fk_field, display_field = dim_conf["fk"]
                    mapping = _load_fk_map(db, fk_model, fk_field, display_field)
                    values = [{"value": name, "label": name} for _id, name in sorted(mapping.items(), key=lambda x: x[1])]
                    return Resp.ok({"values": values})
            # 如果dims中没有，尝试通过fk_ref查找
            fk_ref = filter_field.get("fk_ref", pivot_field)
            if fk_ref in ds_conf["dims"]:
                dim_conf = ds_conf["dims"][fk_ref]
                if isinstance(dim_conf, dict) and "fk" in dim_conf:
                    fk_model, fk_field, display_field = dim_conf["fk"]
                    mapping = _load_fk_map(db, fk_model, fk_field, display_field)
                    values = [{"value": name, "label": name} for _id, name in sorted(mapping.items(), key=lambda x: x[1])]
                    return Resp.ok({"values": values})

    return Resp.ok({"values": []})


class PivotIn(BaseModel):
    dataset: str
    rows_dim: str
    cols_dim: Optional[str] = None
    metric: str = "id"
    agg: str = "count"
    chart_type: Optional[str] = None  # auto/bar/line/pie
    filters: Optional[list] = None


@router.post("/pivot")
def pivot(body: PivotIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """动态透视分析 - 销售自动加权限过滤(只看自己数据)"""
    role_code = get_user_role_code(user, db)

    def role_filter(q):
        # 销售只看自己订单/商机;财务/运营全可见;GM全可见
        if role_code == "SALES":
            from app.models.order import Order
            from app.models.sales import Opportunity
            ds_model = q.column_descriptions[0]['entity'] if q.column_descriptions else None
            if ds_model is Order:
                return q.filter(Order.sales_user_id == user.id)
            if ds_model is Opportunity:
                return q.filter(Opportunity.owner_user_id == user.id)
        return q

    result = build_pivot(db, body.dataset, body.rows_dim, body.cols_dim,
                         body.metric, body.agg, body.filters, role_filter, body.chart_type)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return Resp.ok(result)


class DrillIn(BaseModel):
    dataset: str
    rows_dim: str
    dim_value: str
    filters: Optional[list] = None


@router.post("/pivot/drill")
def pivot_drill(body: DrillIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """下钻分析 - 点击维度值查看明细记录"""
    from app.core.pivot import _get_datasets, _translate_val
    datasets = _get_datasets()
    if body.dataset not in datasets:
        raise HTTPException(400, "未知数据源")
    ds = datasets[body.dataset]
    model = ds["model"]

    # 找到维度字段
    dim_field = None
    dim_conf = None
    if ":" in body.rows_dim:
        parts = body.rows_dim.split(":", 1)
        dim_field = parts[0]
    elif body.rows_dim in ds["dims"]:
        dim_field = body.rows_dim
        dim_conf = ds["dims"][body.rows_dim]

    if not dim_field or not hasattr(model, dim_field):
        raise HTTPException(400, f"无效维度:{body.rows_dim}")

    role_code = get_user_role_code(user, db)

    def role_filter(q):
        if role_code == "SALES":
            from app.models.order import Order
            from app.models.sales import Opportunity
            ds_model = q.column_descriptions[0]['entity'] if q.column_descriptions else None
            if ds_model is Order:
                return q.filter(Order.sales_user_id == user.id)
            if ds_model is Opportunity:
                return q.filter(Opportunity.owner_user_id == user.id)
        return q

    # 构造查询
    cols_to_show = list(ds["dims"].keys()) + list(ds["metrics"].keys())
    select_cols = [getattr(model, c) for c in cols_to_show if hasattr(model, c)]
    q = db.query(*select_cols) if select_cols else db.query(model)

    # 尝试反查维度值对应的原始ID
    from app.core.pivot import _load_fk_map, _FK_CACHE
    _FK_CACHE.clear()

    # 如果是外键维度，需要从名称反查ID
    if isinstance(dim_conf, dict) and "fk" in dim_conf:
        fk_model, fk_field, display_field = dim_conf["fk"]
        mapping = _load_fk_map(db, fk_model, fk_field, display_field)
        # 反向查找：名称→ID
        reverse_map = {v: k for k, v in mapping.items()}
        dim_id = reverse_map.get(body.dim_value)
        if dim_id is not None:
            q = q.filter(getattr(model, dim_field) == dim_id)
        else:
            q = q.filter(getattr(model, dim_field) == body.dim_value)
    elif isinstance(dim_conf, dict) and "enum" in dim_conf:
        # 枚举维度，从中文名反查英文key
        enum_map = dim_conf["enum"]
        reverse_map = {v: k for k, v in enum_map.items()}
        dim_key = reverse_map.get(body.dim_value, body.dim_value)
        q = q.filter(getattr(model, dim_field) == dim_key)
    else:
        q = q.filter(getattr(model, dim_field) == body.dim_value)

    # 应用筛选
    for f in (body.filters or []):
        field = f.get("field")
        op = f.get("op", "eq")
        val = f.get("value")
        if field is None or val is None:
            continue
        col = getattr(model, field, None)
        if col is None:
            continue
        if op == "eq":
            q = q.filter(col == val)
        elif op == "contains":
            q = q.filter(col.contains(str(val)))

    if role_filter is not None:
        q = role_filter(q)

    rows = q.limit(500).all()

    # 翻译结果
    result_rows = []
    for r in rows:
        row = {}
        for c in cols_to_show:
            val = getattr(r, c, None)
            if c in ds["dims"]:
                d_conf = ds["dims"][c]
                if isinstance(d_conf, dict):
                    row[c] = _translate_val(db, val, d_conf)
                else:
                    row[c] = str(val) if val is not None else "(空)"
            elif c in ds["metrics"]:
                row[c] = float(val or 0)
            else:
                row[c] = str(val) if val is not None else "(空)"
        result_rows.append(row)

    # 构建列信息
    columns = []
    for c in cols_to_show:
        if c in ds["dims"]:
            d = ds["dims"][c]
            label = d["label"] if isinstance(d, dict) else d
            columns.append({"key": c, "label": label})
        elif c in ds["metrics"]:
            m = ds["metrics"][c]
            label = m["label"] if isinstance(m, dict) else m
            mtype = m.get("type", "number") if isinstance(m, dict) else "number"
            columns.append({"key": c, "label": label, "type": mtype})

    return Resp.ok({
        "dataset": body.dataset,
        "dim": body.rows_dim,
        "dim_value": body.dim_value,
        "columns": columns,
        "rows": result_rows,
        "total": len(result_rows),
    })


# ============ 应收账龄分析 + 回款节点预警 ============

@router.get("/receivable-aging")
def receivable_aging(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """应收账龄分析 - 按逾期天数分段(0-30/31-60/61-90/90+)"""
    now = bjt_now()
    rows = db.query(FinanceDoc).filter(
        FinanceDoc.doc_type == "RECEIVABLE",
        FinanceDoc.status.in_(["OPEN", "DRAFT"]),
    ).all()
    buckets = {"0-30天": [], "31-60天": [], "61-90天": [], "90天以上": [], "未到期": []}
    for r in rows:
        balance = float(r.amount or 0) - float(r.settled_amount or 0)
        if balance <= 0:
            continue
        cust = db.query(Customer).filter(Customer.id == r.counterparty_id).first()
        cust_name = mask_customer(user, db, cust)["name"] if cust else r.counterparty_name
        item = {
            "doc_no": r.doc_no, "customer_name": cust_name,
            "amount": float(r.amount or 0), "settled": float(r.settled_amount or 0),
            "balance": round(balance, 2),
            "due_date": r.due_date.isoformat() if r.due_date else None,
            "company_id": r.company_id, "billing_type": r.billing_type,
            "days_overdue": (now - r.due_date).days if r.due_date and r.due_date < now else 0,
        }
        if not r.due_date or r.due_date > now:
            buckets["未到期"].append(item)
        elif item["days_overdue"] <= 30:
            buckets["0-30天"].append(item)
        elif item["days_overdue"] <= 60:
            buckets["31-60天"].append(item)
        elif item["days_overdue"] <= 90:
            buckets["61-90天"].append(item)
        else:
            buckets["90天以上"].append(item)
    summary = {k: round(sum(i["balance"] for i in v), 2) for k, v in buckets.items()}
    summary["total"] = round(sum(summary.values()), 2)
    return Resp.ok({"buckets": buckets, "summary": summary})


@router.get("/payment-schedules")
def payment_schedules(days_ahead: int = 30, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """回款节点预警 - 未来N天到期 + 已逾期"""
    now = bjt_now()
    deadline = now + timedelta(days=days_ahead)
    rows = db.query(PaymentSchedule).filter(
        PaymentSchedule.status.in_(["UPCOMING", "DUE", "OVERDUE"]),
    ).all()
    out = []
    for s in rows:
        cust = db.query(Customer).filter(Customer.id == s.customer_id).first()
        cust_name = mask_customer(user, db, cust)["name"] if cust else ""
        days_to_due = (s.due_date - now).days if s.due_date else 0
        if s.due_date < now:
            status = "OVERDUE"
        elif s.due_date <= deadline:
            status = "DUE_SOON"
        else:
            status = "UPCOMING"
        out.append({
            "id": s.id, "schedule_no": s.schedule_no,
            "contract_id": s.contract_id, "order_id": s.order_id,
            "customer_id": s.customer_id, "customer_name": cust_name,
            "due_date": s.due_date.isoformat() if s.due_date else None,
            "expected_amount": float(s.expected_amount or 0),
            "actual_amount": float(s.actual_amount or 0),
            "balance": round(float(s.expected_amount or 0) - float(s.actual_amount or 0), 2),
            "stage": s.stage, "status": status,
            "days_to_due": days_to_due,
        })
    out.sort(key=lambda x: x["days_to_due"])
    return Resp.ok(out)


# ============ 财务双公司主体分析 ============

@router.get("/company-revenue")
def company_revenue(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """双公司主体收入/收款分布"""
    docs = db.query(FinanceDoc).filter(
        FinanceDoc.doc_type.in_(["RECEIVABLE", "RECEIPT"]),
    ).all()
    from app.models.sales import Company
    comps = {c.id: c for c in db.query(Company).all()}
    out = {}
    for c in comps.values():
        out[c.code] = {
            "company_id": c.id, "code": c.code, "name": c.short_name or c.name,
            "tax_type": c.tax_type,
            "receivable": 0, "receipt": 0, "balance": 0,
            "by_billing": {"SPECIAL_VAT": 0, "NORMAL": 0, "CASH": 0},
        }
    for d in docs:
        comp = comps.get(d.company_id)
        if not comp:
            continue
        key = comp.code
        amt = float(d.amount or 0)
        if d.doc_type == "RECEIVABLE":
            out[key]["receivable"] += amt
            if d.billing_type in out[key]["by_billing"]:
                out[key]["by_billing"][d.billing_type] += amt
        elif d.doc_type == "RECEIPT":
            out[key]["receipt"] += amt
    for k in out:
        out[k]["balance"] = round(out[k]["receivable"] - out[k]["receipt"], 2)
        out[k]["receivable"] = round(out[k]["receivable"], 2)
        out[k]["receipt"] = round(out[k]["receipt"], 2)
        for bk in out[k]["by_billing"]:
            out[k]["by_billing"][bk] = round(out[k]["by_billing"][bk], 2)
    return Resp.ok(list(out.values()))
