"""动态透视分析引擎 - 用户选维度+指标+筛选,实时生成透视表+ECharts数据
白名单防注入;支持时间维度(month/quarter/year)和分类维度。
"""
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct, case, and_, or_, Integer, String, cast
from datetime import datetime
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)

# 数据源白名单 - model/字段/时间字段
def _datasets():
    from app.models.order import Order
    from app.models.workshop import WorkOrder, Completion
    from app.models.finance import FinanceDoc, WorkOrderCost
    from app.models.inventory import InventoryTxn
    from app.models.sales import Opportunity, SalesAdjustment
    return {
        "orders": {
            "model": Order, "label": "订单",
            "time_field": "created_at",
            "dims": {"customer_id": "客户", "status": "状态", "company_id": "公司主体",
                     "billing_type": "开票类型", "sales_user_id": "销售", "delivery_status": "发货状态"},
            "metrics": {"total_amount": "订单金额", "prepayment_amount": "预收金额",
                        "id": "订单数", "return_count": "退单次数"},
            "value_labels": {
                "status": {"DRAFT":"草稿","SUBMITTED":"已提交","EFFECTIVE":"已生效","PROCESSING":"生产中",
                           "PENDING_DELIVERY":"待发货","DELIVERED":"已发货","CLOSED":"已完结","CANCELLED":"已取消"},
                "delivery_status": {"PENDING":"待发货","PARTIAL":"部分发货","DELIVERED":"已发货"},
            },
        },
        "work_orders": {
            "model": WorkOrder, "label": "工单",
            "time_field": "created_at",
            "dims": {"workshop": "车间", "status": "状态", "order_id": "订单"},
            "metrics": {"plan_qty": "计划数量", "id": "工单数"},
            "value_labels": {"status": {"DRAFT":"草稿","CONFIRMED":"已确认"}},
        },
        "finance_docs": {
            "model": FinanceDoc, "label": "财务单据",
            "time_field": "created_at",
            "dims": {"doc_type": "单据类型", "status": "状态", "company_id": "公司主体",
                     "billing_type": "开票类型", "counterparty_type": "往来类型",
                     "counterparty_id": "往来单位"},
            "metrics": {"amount": "金额", "settled_amount": "已核销", "id": "单据数"},
            "value_labels": {
                "status": {"DRAFT":"草稿","OPEN":"在途","SETTLED":"已结算","CANCELLED":"已取消"},
                "doc_type": {"RECEIVABLE":"应收","PAYMENT":"付款"},
            },
        },
        "completions": {
            "model": Completion, "label": "完工单",
            "time_field": "created_at",
            "dims": {"status": "状态", "work_order_id": "工单"},
            "metrics": {"qualified_qty": "合格数", "total_cost": "总成本", "id": "完工单数"},
        },
        "inventory_txns": {
            "model": InventoryTxn, "label": "库存流水",
            "time_field": "created_at",
            "dims": {"txn_type": "流水类型", "item_id": "物料", "work_order_id": "工单"},
            "metrics": {"quantity": "数量", "amount": "金额", "id": "流水数"},
        },
        "opportunities": {
            "model": Opportunity, "label": "商机",
            "time_field": "created_at",
            "dims": {"stage": "阶段", "source": "来源", "customer_id": "客户", "owner_user_id": "销售"},
            "metrics": {"expected_amount": "预计金额", "id": "商机数"},
        },
        "work_order_costs": {
            "model": WorkOrderCost, "label": "工单成本",
            "time_field": "occurred_at",
            "dims": {"cost_type": "成本类型", "work_order_id": "工单"},
            "metrics": {"amount": "成本金额", "id": "成本记录数"},
        },
    }


DATASETS = None
def _get_datasets():
    global DATASETS
    if DATASETS is None:
        DATASETS = _datasets()
    return DATASETS


# 时间维度SQL生成(sqlite语法,兼容)
def _time_expr(time_field: str, grain: str):
    col = time_field
    if grain == "month":
        return func.strftime("%Y-%m", col)
    if grain == "quarter":
        year = func.strftime("%Y", col)
        m = cast(func.strftime("%m", col), Integer)
        q = cast((m - 1) / 3 + 1, String)
        return year + "-Q" + q
    if grain == "year":
        return func.strftime("%Y", col)
    if grain == "week":
        return func.strftime("%Y-W%W", col)
    return None


def _resolve_dim(ds, dim: str):
    """解析维度:返回(select_expr, label)。时间维度形如 created_at:month"""
    if ":" in dim:
        field, grain = dim.split(":", 1)
        if field != ds["time_field"]:
            return None, None
        expr = _time_expr(field, grain)
        return expr, f"{grain}({field})"
    if dim in ds["dims"]:
        return getattr(ds["model"], dim), ds["dims"][dim]
    return None, None


def build_pivot(db: Session, dataset: str, rows_dim: str, cols_dim: Optional[str] = None,
                metric: str = "id", agg: str = "count",
                filters: Optional[List[dict]] = None,
                role_filter=None) -> dict:
    """构建透视表+图表数据
    agg: count/sum/avg
    filters: [{field, op, value}] op: eq/ne/gt/lt/ge/le/in/contains
    role_filter: 可选,对query加权限过滤(如销售只看自己订单)
    """
    datasets = _get_datasets()
    if dataset not in datasets:
        return {"error": f"未知数据源:{dataset}"}
    ds = datasets[dataset]
    model = ds["model"]

    # 解析行维度
    r_expr, r_label = _resolve_dim(ds, rows_dim)
    if r_expr is None:
        return {"error": f"无效行维度:{rows_dim}"}

    # 解析列维度(可选)
    c_expr, c_label = (None, None)
    if cols_dim:
        c_expr, c_label = _resolve_dim(ds, cols_dim)
        if c_expr is None:
            return {"error": f"无效列维度:{cols_dim}"}

    # 解析指标
    if metric not in ds["metrics"] and metric != "id":
        return {"error": f"无效指标:{metric}"}
    metric_col = getattr(model, metric) if metric != "id" else model.id

    # 聚合函数
    if agg == "count":
        agg_expr = func.count(metric_col)
    elif agg == "sum":
        agg_expr = func.sum(metric_col)
    elif agg == "avg":
        agg_expr = func.avg(metric_col)
    else:
        return {"error": f"无效聚合:{agg}"}

    # 构造query
    q = db.query(r_expr.label("r_dim"))
    if c_expr is not None:
        q = q.add_columns(c_expr.label("c_dim"))
    q = q.add_columns(agg_expr.label("metric"))

    # 筛选
    for f in (filters or []):
        field = f.get("field")
        op = f.get("op", "eq")
        val = f.get("value")
        if field is None:
            continue
        col = getattr(model, field, None)
        if col is None:
            continue
        if op == "eq":
            q = q.filter(col == val)
        elif op == "ne":
            q = q.filter(col != val)
        elif op == "gt":
            q = q.filter(col > val)
        elif op == "lt":
            q = q.filter(col < val)
        elif op == "ge":
            q = q.filter(col >= val)
        elif op == "le":
            q = q.filter(col <= val)
        elif op == "in" and isinstance(val, list):
            q = q.filter(col.in_(val))
        elif op == "contains":
            q = q.filter(col.contains(str(val)))

    # 权限过滤
    if role_filter is not None:
        q = role_filter(q)

    # group by
    q = q.group_by(r_expr)
    if c_expr is not None:
        q = q.group_by(c_expr)
    q = q.order_by(r_expr)

    rows = q.all()

    # 值标签翻译(英文→中文)
    vlabels = ds.get("value_labels", {})
    def _translate(v, dim_field):
        d = vlabels.get(dim_field, {})
        return d.get(v, v)

    # 构造透视表
    row_keys = []
    col_keys = []
    cells = {}  # {(r,c): val}
    for r in rows:
        rk = str(r.r_dim) if r.r_dim is not None else "(空)"
        rk = _translate(rk, rows_dim)
        if rk not in row_keys:
            row_keys.append(rk)
        if c_expr is not None:
            ck = str(r.c_dim) if r.c_dim is not None else "(空)"
            ck = _translate(ck, cols_dim) if cols_dim else ck
            if ck not in col_keys:
                col_keys.append(ck)
            cells[(rk, ck)] = float(r.metric or 0)
        else:
            cells[(rk, "__total__")] = float(r.metric or 0)

    if not col_keys:
        col_keys = ["__total__"]

    # 二维矩阵
    table = []
    for rk in row_keys:
        row = {"dim": rk}
        for ck in col_keys:
            row[ck] = cells.get((rk, ck), 0)
        table.append(row)

    # 图表数据:行维度为X轴,每个列维度一条series
    # 自动选择图表类型
    # 有明确列维度(交叉分析) → 柱状图
    has_cross = cols_dim is not None
    # 单一系列且分类数3~8 → 饼图(占比展示); 分类太少(1-2)用柱状图更清晰,太多(>8)也换柱状图
    single_series = c_expr is None or len(col_keys) <= 1
    # 时间维度 → 折线图(趋势)
    is_time_dim = any(g in rows_dim for g in [":month", ":quarter", ":year", ":week"])

    if has_cross:
        chart_type = "bar"
    elif is_time_dim:
        chart_type = "line"
    elif single_series and 3 <= len(row_keys) <= 8:
        chart_type = "pie"
    else:
        chart_type = "bar"

    chart = {
        "x": row_keys,
        "series": [],
        "chart_type": chart_type,
    }
    if c_expr is not None and len(col_keys) > 1:
        for ck in col_keys:
            chart["series"].append({
                "name": ck,
                "data": [cells.get((rk, ck), 0) for rk in row_keys],
            })
    else:
        ck = col_keys[0] if col_keys else "__total__"
        chart["series"].append({
            "name": ds["metrics"].get(metric, metric),
            "data": [cells.get((rk, ck), 0) for rk in row_keys],
        })

    return {
        "dataset": dataset, "dataset_label": ds["label"],
        "rows_dim": rows_dim, "rows_label": r_label,
        "cols_dim": cols_dim, "cols_label": c_label,
        "metric": metric, "metric_label": ds["metrics"].get(metric, metric),
        "agg": agg,
        "row_keys": row_keys, "col_keys": col_keys,
        "table": table, "chart": chart,
    }


def list_datasets() -> dict:
    """返回可用数据源/维度/指标,供前端选择"""
    ds = _get_datasets()
    out = {}
    for k, v in ds.items():
        out[k] = {
            "label": v["label"],
            "time_field": v["time_field"],
            "dims": [{"key": dk, "label": dl} for dk, dl in v["dims"].items()],
            "time_dims": [
                {"key": f"{v['time_field']}:month", "label": "月"},
                {"key": f"{v['time_field']}:quarter", "label": "季"},
                {"key": f"{v['time_field']}:year", "label": "年"},
                {"key": f"{v['time_field']}:week", "label": "周"},
            ],
            "metrics": [{"key": mk, "label": ml} for mk, ml in v["metrics"].items()],
            "aggs": [{"key": "count", "label": "计数"}, {"key": "sum", "label": "求和"}, {"key": "avg", "label": "平均"}],
        }
    return out
