"""动态透视分析引擎 V3 - 对标大厂 BI (Tableau/Power BI/SAP BW)
核心能力:
1. 外键自动翻译: 维度是ID时自动关联表显示名称
2. 智能筛选: 支持日期范围、枚举下拉、数值区间
3. 多维交叉: 行×列任意组合
4. 图表自动适配: 时间维度→折线、分类→柱状/饼图、交叉→堆叠
5. 权限隔离: 销售只看自己数据
"""
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct, case, and_, or_, Integer, String, cast, Numeric, Column, DateTime, Date, Float, Text, Boolean
from sqlalchemy import inspect as sa_inspect
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
import logging

from app.core.db import engine

logger = logging.getLogger(__name__)


# ============ 数据源定义 (含外键映射) ============
def _datasets():
    from app.models.order import Order
    from app.models.workshop import WorkOrder, Completion
    from app.models.finance import FinanceDoc, WorkOrderCost
    from app.models.inventory import InventoryTxn, InventoryItem
    from app.models.sales import Opportunity, Company
    from app.models.customer import Customer
    from app.models.system import User
    from app.models.purchase import Supplier, Purchase

    return {
        "orders": {
            "model": Order, "label": "订单",
            "time_field": "created_at",
            "joins": {
                "customer_name": {"model": Customer, "local": "customer_id", "remote": "id", "field": "name", "label": "客户名称"},
                "customer_industry": {"model": Customer, "local": "customer_id", "remote": "id", "field": "industry", "label": "客户行业"},
                "company_name": {"model": Company, "local": "company_id", "remote": "id", "field": "name", "label": "公司名称"},
            },
            "dims": {
                "customer_id": {"label": "客户", "fk": (Customer, "id", "name")},
                "status": {"label": "状态", "enum": {"DRAFT":"草稿","SUBMITTED":"已提交","EFFECTIVE":"已生效","PROCESSING":"生产中","PENDING_DELIVERY":"待发货","DELIVERED":"已发货","CLOSED":"已完结","CANCELLED":"已取消"}},
                "company_id": {"label": "公司主体", "fk": (Company, "id", "name")},
                "billing_type": {"label": "开票类型", "enum": {"SPECIAL_VAT":"专票","NORMAL":"普票","CASH":"现金"}},
                "sales_user_id": {"label": "销售", "fk": (User, "id", "name")},
                "delivery_status": {"label": "发货状态", "enum": {"PENDING":"待发货","PARTIAL":"部分发货","DELIVERED":"已发货"}},
            },
            "metrics": {
                "total_amount": {"label": "订单金额", "type": "currency"},
                "prepayment_amount": {"label": "预收金额", "type": "currency"},
                "prepayment_ratio": {"label": "预收比例", "type": "number"},
                "balance_amount": {"label": "余款", "type": "currency", "agg": "sum", "calc_expr": lambda m, c: func.sum(m.total_amount - m.prepayment_amount)},
                "id": {"label": "订单数", "type": "count"},
                "return_count": {"label": "退单次数", "type": "count"},
                "avg_order_amount": {"label": "平均订单金额", "type": "currency", "agg": "avg", "source_field": "total_amount"},
            },
            "filter_fields": {
                "customer_id": {"label": "客户", "type": "fk", "dataset": "orders", "fk_ref": "customer_id"},
                "company_id": {"label": "公司主体", "type": "fk", "dataset": "orders", "fk_ref": "company_id"},
                "status": {"label": "状态", "type": "enum", "options": ["DRAFT","SUBMITTED","EFFECTIVE","PROCESSING","PENDING_DELIVERY","DELIVERED","CLOSED","CANCELLED"]},
                "billing_type": {"label": "开票类型", "type": "enum", "options": ["SPECIAL_VAT","NORMAL","CASH"]},
                "sales_user_id": {"label": "销售", "type": "fk", "dataset": "orders", "fk_ref": "sales_user_id"},
                "delivery_status": {"label": "发货状态", "type": "enum", "options": ["PENDING","PARTIAL","DELIVERED"]},
                "created_at": {"label": "创建日期", "type": "date"},
                "total_amount": {"label": "订单金额", "type": "number"},
            },
        },
        "work_orders": {
            "model": WorkOrder, "label": "工单",
            "time_field": "created_at",
            "joins": {
                "order_name": {"model": Order, "local": "order_id", "remote": "id", "field": "order_no", "label": "订单号"},
            },
            "dims": {
                "workshop": {"label": "车间", "enum": {"车间A":"车间A","车间B":"车间B","车间C":"车间C"}},
                "status": {"label": "状态", "enum": {"DRAFT":"草稿","CONFIRMED":"已确认","IN_PROGRESS":"生产中","COMPLETED":"已完工"}},
                "order_id": {"label": "订单", "fk": (Order, "id", "order_no")},
            },
            "metrics": {
                "plan_qty": {"label": "计划数量", "type": "number"},
                "actual_qty": {"label": "完工数量", "type": "number"},
                "id": {"label": "工单数", "type": "count"},
                "outsource_cost": {"label": "委外成本", "type": "currency"},
                "avg_plan_qty": {"label": "平均计划数量", "type": "number", "agg": "avg", "source_field": "plan_qty"},
            },
            "filter_fields": {
                "order_id": {"label": "订单", "type": "fk", "dataset": "work_orders", "fk_ref": "order_id"},
                "workshop": {"label": "车间", "type": "enum", "options": ["车间A","车间B","车间C"]},
                "status": {"label": "状态", "type": "enum", "options": ["DRAFT","CONFIRMED","IN_PROGRESS","COMPLETED"]},
                "created_at": {"label": "创建日期", "type": "date"},
            },
        },
        "finance_docs": {
            "model": FinanceDoc, "label": "财务单据",
            "time_field": "created_at",
            "joins": {
                "customer_name": {"model": Customer, "local": "counterparty_id", "remote": "id", "field": "name", "label": "客户名称"},
                "customer_industry": {"model": Customer, "local": "counterparty_id", "remote": "id", "field": "industry", "label": "客户行业"},
            },
            "dims": {
                "doc_type": {"label": "单据类型", "enum": {"RECEIVABLE":"应收","PAYABLE":"应付","RECEIPT":"收款","PAYMENT":"付款","PAYROLL":"工资"}},
                "status": {"label": "状态", "enum": {"DRAFT":"草稿","OPEN":"在途","SETTLED":"已结算","CANCELLED":"已取消"}},
                "company_id": {"label": "公司主体", "fk": (Company, "id", "name")},
                "billing_type": {"label": "开票类型", "enum": {"SPECIAL_VAT":"专票","NORMAL":"普票","CASH":"现金"}},
                "counterparty_type": {"label": "往来类型", "enum": {"CUSTOMER":"客户","SUPPLIER":"供应商","EMPLOYEE":"员工"}},
                "counterparty_id": {"label": "往来单位", "fk": (Customer, "id", "name")},
            },
            "metrics": {
                "amount": {"label": "金额", "type": "currency"},
                "settled_amount": {"label": "已核销", "type": "currency"},
                "unsettled_amount": {"label": "未核销", "type": "currency", "agg": "sum", "calc_expr": lambda m, c: func.sum(m.amount - m.settled_amount)},
                "id": {"label": "单据数", "type": "count"},
                "avg_amount": {"label": "平均金额", "type": "currency", "agg": "avg", "source_field": "amount"},
            },
            "filter_fields": {
                "doc_type": {"label": "单据类型", "type": "enum", "options": ["RECEIVABLE","PAYABLE","RECEIPT","PAYMENT"]},
                "status": {"label": "状态", "type": "enum", "options": ["DRAFT","OPEN","SETTLED","CANCELLED"]},
                "company_id": {"label": "公司主体", "type": "fk", "fk_ref": "company_id"},
                "billing_type": {"label": "开票类型", "type": "enum", "options": ["SPECIAL_VAT","NORMAL","CASH"]},
                "counterparty_type": {"label": "往来类型", "type": "enum", "options": ["CUSTOMER","SUPPLIER","EMPLOYEE"]},
                "counterparty_id": {"label": "往来单位", "type": "fk", "fk_ref": "counterparty_id"},
                "created_at": {"label": "创建日期", "type": "date"},
                "amount": {"label": "金额", "type": "number"},
            },
        },
        "completions": {
            "model": Completion, "label": "完工单",
            "time_field": "created_at",
            "dims": {
                "status": {"label": "状态", "enum": {"DRAFT":"草稿","CONFIRMED":"已确认"}},
                "work_order_id": {"label": "工单", "fk": (WorkOrder, "id", "work_order_no")},
            },
            "metrics": {
                "qualified_qty": {"label": "合格数", "type": "number"},
                "defect_qty": {"label": "废品数", "type": "number"},
                "total_cost": {"label": "总成本", "type": "currency"},
                "id": {"label": "完工单数", "type": "count"},
                "avg_cost": {"label": "平均成本", "type": "currency", "agg": "avg", "source_field": "total_cost"},
            },
            "filter_fields": {
                "status": {"label": "状态", "type": "enum", "options": ["DRAFT","CONFIRMED"]},
                "work_order_id": {"label": "工单", "type": "fk", "fk_ref": "work_order_id"},
                "created_at": {"label": "创建日期", "type": "date"},
                "total_cost": {"label": "总成本", "type": "number"},
            },
        },
        "inventory_txns": {
            "model": InventoryTxn, "label": "库存流水",
            "time_field": "occurred_at",
            "dims": {
                "txn_type": {"label": "流水类型", "enum": {"IN":"入库","OUT":"出库","RETURN":"退库","ADJUST":"调整"}},
                "item_id": {"label": "物料", "fk": (InventoryItem, "id", "name")},
                "work_order_id": {"label": "工单", "fk": (WorkOrder, "id", "work_order_no")},
            },
            "metrics": {
                "quantity": {"label": "数量", "type": "number"},
                "amount": {"label": "金额", "type": "currency"},
                "id": {"label": "流水数", "type": "count"},
                "avg_unit_cost": {"label": "平均单价", "type": "currency", "agg": "avg", "source_field": "unit_cost"},
            },
            "filter_fields": {
                "txn_type": {"label": "流水类型", "type": "enum", "options": ["IN","OUT","RETURN","ADJUST"]},
                "item_id": {"label": "物料", "type": "fk", "fk_ref": "item_id"},
                "work_order_id": {"label": "工单", "type": "fk", "fk_ref": "work_order_id"},
                "occurred_at": {"label": "发生日期", "type": "date"},
                "quantity": {"label": "数量", "type": "number"},
                "amount": {"label": "金额", "type": "number"},
            },
        },
        "purchases": {
            "model": Purchase, "label": "采购单",
            "time_field": "created_at",
            "joins": {
                "supplier_name": {"model": Supplier, "local": "supplier_id", "remote": "id", "field": "name", "label": "供应商名称"},
            },
            "dims": {
                "supplier_id": {"label": "供应商", "fk": (Supplier, "id", "name")},
                "status": {"label": "状态", "enum": {"DRAFT":"草稿","ORDERED":"已下单","RECEIVED":"已收货","CLOSED":"已关闭"}},
            },
            "metrics": {
                "total_amount": {"label": "采购金额", "type": "currency"},
                "id": {"label": "采购单数", "type": "count"},
            },
            "filter_fields": {
                "supplier_id": {"label": "供应商", "type": "fk", "fk_ref": "supplier_id"},
                "status": {"label": "状态", "type": "enum", "options": ["DRAFT","ORDERED","RECEIVED","CLOSED"]},
                "created_at": {"label": "创建日期", "type": "date"},
                "total_amount": {"label": "采购金额", "type": "number"},
            },
        },
        "opportunities": {
            "model": Opportunity, "label": "商机",
            "time_field": "created_at",
            "dims": {
                "stage": {"label": "阶段", "enum": {"INITIAL":"初步接触","QUALIFICATION":"需求确认","PROPOSAL":"方案报价","NEGOTIATION":"商务谈判","WON":"赢单","LOST":"输单"}},
                "source": {"label": "来源", "enum": {"网络推广":"网络推广","展会":"展会","老客户介绍":"老客户介绍","电话营销":"电话营销"}},
                "customer_id": {"label": "客户", "fk": (Customer, "id", "name")},
                "owner_user_id": {"label": "销售", "fk": (User, "id", "name")},
            },
            "metrics": {
                "expected_amount": {"label": "预计金额", "type": "currency"},
                "id": {"label": "商机数", "type": "count"},
                "avg_expected_amount": {"label": "平均预计金额", "type": "currency", "agg": "avg", "source_field": "expected_amount"},
            },
            "filter_fields": {
                "stage": {"label": "阶段", "type": "enum", "options": ["INITIAL","QUALIFICATION","PROPOSAL","NEGOTIATION","WON","LOST"]},
                "source": {"label": "来源", "type": "enum", "options": ["网络推广","展会","老客户介绍","电话营销"]},
                "customer_id": {"label": "客户", "type": "fk", "fk_ref": "customer_id"},
                "owner_user_id": {"label": "销售", "type": "fk", "fk_ref": "owner_user_id"},
                "created_at": {"label": "创建日期", "type": "date"},
                "expected_amount": {"label": "预计金额", "type": "number"},
            },
        },
        "work_order_costs": {
            "model": WorkOrderCost, "label": "工单成本",
            "time_field": "occurred_at",
            "dims": {
                "cost_type": {"label": "成本类型", "enum": {"MATERIAL":"材料费","LABOR":"人工费","OVERHEAD":"制造费用","OUTSOURCE":"外协","REWORK":"返工"}},
                "work_order_id": {"label": "工单", "fk": (WorkOrder, "id", "work_order_no")},
            },
            "metrics": {
                "amount": {"label": "成本金额", "type": "currency"},
                "id": {"label": "成本记录数", "type": "count"},
                "avg_cost": {"label": "平均成本", "type": "currency", "agg": "avg", "source_field": "amount"},
            },
            "filter_fields": {
                "cost_type": {"label": "成本类型", "type": "enum", "options": ["MATERIAL","LABOR","OVERHEAD","OUTSOURCE"]},
                "work_order_id": {"label": "工单", "type": "fk", "fk_ref": "work_order_id"},
                "occurred_at": {"label": "发生日期", "type": "date"},
                "amount": {"label": "成本金额", "type": "number"},
            },
        },
    }


DATASETS = None
# 自动生成数据源缓存: 表结构签名变化时自动重建
_AUTO_CACHE = {"sig": None, "data": None}

_AUTO_SYS_PREFIX = ("ai_", "audit_", "notification_", "event_log", "agent_",
                    "alert_", "report_", "dicts", "permissions", "role_permissions",
                    "kpi_snapshots", "flow_", "users", "roles")

# 自动表中文名映射(方便LLM理解)
_AUTO_LABEL = {
    "customers": "客户", "suppliers": "供应商", "companies": "公司",
    "contracts": "合同", "purchases": "采购单", "purchase_requests": "采购申请",
    "purchase_items": "采购明细", "receiving_logs": "来货登记",
    "delivery_notes": "送货单", "delivery_note_items": "送货明细",
    "order_items": "订单明细", "inventory_items": "库存物料",
    "material_requisitions": "领料单", "sales_adjustments": "销售调价",
    "expense_claims": "费用报销", "payroll_runs": "工资结算",
    "payment_schedules": "付款计划", "accounts_chart": "会计科目",
    "finance_items": "财务明细", "work_processes": "工序",
    "customer_consign_log": "客户寄存记录", "completion_items": "完工明细",
    "work_order_costs": "工单成本", "opportunities": "商机",
}


def _import_all_models():
    """确保所有模型已注册到Base.registry"""
    import pkgutil
    import importlib
    import app.models as models_pkg
    for mod in pkgutil.iter_modules(models_pkg.__path__):
        importlib.import_module(f"{models_pkg.__name__}.{mod.name}")


def _auto_datasets() -> dict:
    """动态反射所有未手工配置的业务表，自动生成数据源。
    表新增/删除/改字段时，签名变化自动重建。"""
    from app.core.db import Base

    _import_all_models()

    # 计算表结构签名(表名+列名)，用于缓存失效判断
    insp = sa_inspect(engine)
    tables = sorted(insp.get_table_names())
    sig = ";".join(f"{t}({','.join(c['name'] for c in insp.get_columns(t))})" for t in tables)
    if _AUTO_CACHE["sig"] == sig and _AUTO_CACHE["data"]:
        return _AUTO_CACHE["data"]

    manual_keys = set(_datasets().keys())
    out = {}
    for mapper in Base.registry.mappers:
        model = mapper.class_
        tname = model.__tablename__
        if tname in manual_keys or tname.startswith(_AUTO_SYS_PREFIX):
            continue
        cols = model.__table__.columns
        dims, metrics, filters = {}, {}, {}
        time_field = None
        for c in cols:
            name = c.name
            is_pk = c.primary_key
            type_ = c.type
            if is_pk:
                metrics["id"] = {"label": f"{tname}数", "type": "count"}
                continue
            # 外键字段作为维度(不做翻译，显示原始值)
            if name.endswith("_id") and name != "id":
                dims[name] = {"label": name}
                continue
            if isinstance(type_, (DateTime, Date)):
                if time_field is None:
                    time_field = name
                filters[name] = {"label": name, "type": "date"}
                continue
            if isinstance(type_, (Integer, Numeric, Float)):
                metrics[name] = {"label": name, "type": "number"}
                filters[name] = {"label": name, "type": "number"}
                continue
            if isinstance(type_, (String, Text, Boolean)):
                dims[name] = {"label": name}
                filters[name] = {"label": name, "type": "text"}
                continue
            # JSON/大字段跳过
        if not time_field and "created_at" in [c.name for c in cols]:
            time_field = "created_at"
        out[tname] = {
            "model": model, "label": _AUTO_LABEL.get(tname, tname),
            "time_field": time_field,
            "dims": dims, "metrics": metrics, "filter_fields": filters,
        }

    _AUTO_CACHE["sig"] = sig
    _AUTO_CACHE["data"] = out
    return out


def _get_datasets():
    """合并手工配置数据源 + 动态反射数据源"""
    merged = dict(_datasets())
    for k, v in _auto_datasets().items():
        if k not in merged:
            merged[k] = v
    return merged


# ============ 外键翻译缓存 ============
_FK_CACHE = {}


def _load_fk_map(db: Session, model_class, fk_field: str, display_field: str) -> Dict[int, str]:
    cache_key = (model_class.__name__, fk_field)
    if cache_key in _FK_CACHE:
        return _FK_CACHE[cache_key]
    try:
        rows = db.query(getattr(model_class, fk_field), getattr(model_class, display_field)).all()
        mapping = {}
        for r in rows:
            fk_val = getattr(r, fk_field)
            if fk_val is not None:
                mapping[int(fk_val)] = str(getattr(r, display_field))
        _FK_CACHE[cache_key] = mapping
        return mapping
    except Exception:
        logger.warning(f"Failed to load FK map for {model_class.__name__}.{fk_field}")
        return {}


def _clear_fk_cache():
    global _FK_CACHE
    _FK_CACHE = {}


# ============ 时间维度SQL ============
def _time_expr(time_field: str, grain: str):
    if grain == "month":
        return func.strftime("%Y-%m", time_field)
    if grain == "quarter":
        year = func.strftime("%Y", time_field)
        m = cast(func.strftime("%m", time_field), Integer)
        q = cast((m - 1) / 3 + 1, String)
        return year + "-Q" + q
    if grain == "year":
        return func.strftime("%Y", time_field)
    if grain == "week":
        return func.strftime("%Y-W%W", time_field)
    return None


# ============ 维度解析 ============
def _resolve_dim(ds: dict, dim: str):
    if ":" in dim:
        field, grain = dim.split(":", 1)
        if field != ds["time_field"]:
            return None, None, None
        if not hasattr(ds["model"], field):
            return None, None, None
        expr = _time_expr(getattr(ds["model"], field), grain)
        return expr, f"{grain}({ds['time_field']})", {"type": "time"}
    if dim in ds["dims"]:
        if not hasattr(ds["model"], dim):
            return None, None, None
        dim_conf = ds["dims"][dim]
        label = dim_conf["label"] if isinstance(dim_conf, dict) else dim_conf
        return getattr(ds["model"], dim), label, dim_conf
    # 关联维度(跨表JOIN)
    if dim in ds.get("joins", {}):
        jc = ds["joins"][dim]
        if not hasattr(jc["model"], jc["field"]):
            return None, None, None
        expr = getattr(jc["model"], jc["field"])
        return expr, jc["label"], {"type": "join"}
    return None, None, None


# ============ 值翻译 ============
def _translate_val(db: Session, v, dim_conf) -> str:
    if v is None:
        return "(空)"
    s = str(v)
    if not isinstance(dim_conf, dict):
        return s
    if dim_conf.get("type") == "join":
        # 关联字段值已是显示值(名称/行业等)
        return s
    if "enum" in dim_conf:
        return dim_conf["enum"].get(s, s)
    if "fk" in dim_conf:
        model_class, fk_field, display_field = dim_conf["fk"]
        try:
            val_int = int(v)
        except (ValueError, TypeError):
            return s
        mapping = _load_fk_map(db, model_class, fk_field, display_field)
        return mapping.get(val_int, s)
    return s


# ============ 主查询构建 ============
# ============ 筛选解析 (支持显示值→原始值) ============
def _resolve_filter(db: Session, ds: dict, model, field: str, val):
    """将筛选值从显示值转为原始值。支持:
    1. 枚举维度: 中文名→英文key (如'草稿'→'DRAFT')
    2. 外键维度: 名称→ID (如'苏州五金制品有限公司'→3)
    3. 普通字段: 直接返回原值
    """
    if field in ds["dims"]:
        dim_conf = ds["dims"][field]
        if isinstance(dim_conf, dict):
            if "enum" in dim_conf:
                reverse_map = {v: k for k, v in dim_conf["enum"].items()}
                if isinstance(val, str) and val in reverse_map:
                    return reverse_map[val]
            if "fk" in dim_conf:
                fk_model, fk_field, display_field = dim_conf["fk"]
                mapping = _load_fk_map(db, fk_model, fk_field, display_field)
                if isinstance(val, str):
                    reverse_map = {str(v): k for k, v in mapping.items()}
                    if val in reverse_map:
                        return reverse_map[val]
                elif isinstance(val, list):
                    reverse_map = {str(v): k for k, v in mapping.items()}
                    return [reverse_map.get(str(v), v) for v in val]
    return val


def _apply_filters(db: Session, q, ds: dict, model, filters: list):
    """应用筛选条件，支持显示值→原始值翻译"""
    for f in (filters or []):
        field = f.get("field")
        op = f.get("op", "eq")
        val = f.get("value")
        if field is None or val is None or val == "":
            continue
        col = getattr(model, field, None)
        if col is None and field in ds.get("joins", {}):
            jc = ds["joins"][field]
            col = getattr(jc["model"], jc["field"], None)
        if col is None:
            continue
        
        # 处理like操作符 - 支持FK字段模糊查询
        if op == "like":
            if field in ds.get("dims", {}):
                dim_conf = ds["dims"][field]
                if isinstance(dim_conf, dict) and "fk" in dim_conf:
                    # FK字段模糊查询: 先在关联表中模糊匹配，得到ID列表
                    fk_model, fk_field, display_field = dim_conf["fk"]
                    like_val = f"%{val}%"
                    matched_ids = db.query(fk_model).filter(
                        getattr(fk_model, display_field).like(like_val)
                    ).all()
                    id_list = [getattr(m, fk_field) for m in matched_ids]
                    if id_list:
                        q = q.filter(col.in_(id_list))
                    else:
                        q = q.filter(col == -999999)  # 无匹配
                    continue
            # 普通字段like
            q = q.filter(col.like(f"%{val}%"))
            continue
        
        if op == "in" and isinstance(val, list):
            val = [_resolve_filter(db, ds, model, field, v) for v in val]
        elif op == "between" and isinstance(val, list):
            val = [_resolve_filter(db, ds, model, field, v) for v in val]
        else:
            val = _resolve_filter(db, ds, model, field, val)

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
        elif op == "between" and isinstance(val, list) and len(val) == 2:
            q = q.filter(col >= val[0], col <= val[1])
    return q


def build_pivot(db: Session, dataset: str, rows_dim: str, cols_dim: Optional[str] = None,
                metric: str = "id", agg: str = "count",
                filters: Optional[List[dict]] = None,
                role_filter=None, chart_type: Optional[str] = None) -> dict:
    """构建透视表+图表数据 (V3)"""
    _clear_fk_cache()
    datasets = _get_datasets()
    if dataset not in datasets:
        return {"error": f"未知数据源:{dataset}"}
    ds = datasets[dataset]
    model = ds["model"]

    r_expr, r_label, r_conf = (None, None, None)
    if rows_dim:
        r_expr, r_label, r_conf = _resolve_dim(ds, rows_dim)
        if r_expr is None:
            return {"error": f"无效行维度:{rows_dim}"}

    c_expr, c_label, c_conf = (None, None, None)
    if cols_dim:
        c_expr, c_label, c_conf = _resolve_dim(ds, cols_dim)
        if c_expr is None:
            return {"error": f"无效列维度:{cols_dim}"}

    # 找出筛选条件中的FK字段，作为附加维度显示
    extra_dims = []  # [(field, expr, label, conf), ...]
    filter_fields_cfg = ds.get("filter_fields", {})
    dims_cfg = ds.get("dims", {})
    if filters:
        for f in filters:
            fk_field = f.get("field")
            if not fk_field:
                continue
            # 检查是否是FK类型
            ff_cfg = filter_fields_cfg.get(fk_field, {})
            dim_cfg = dims_cfg.get(fk_field, {})
            is_fk = ff_cfg.get("type") == "fk" or "fk" in dim_cfg
            # 只添加非维度的FK字段（避免重复）
            if is_fk and fk_field != rows_dim and fk_field != cols_dim:
                if fk_field not in [ed[0] for ed in extra_dims]:
                    e_expr, e_label, e_conf = _resolve_dim(ds, fk_field)
                    if e_expr is not None:
                        extra_dims.append((fk_field, e_expr, e_label, e_conf))

    if metric not in ds["metrics"] and metric != "id":
        return {"error": f"无效指标:{metric}"}
    metric_def = ds["metrics"].get(metric, {"label": metric, "type": "count"})
    
    # 处理计算字段
    calc_expr = metric_def.get("calc_expr")
    
    # 确定实际聚合的字段（支持source_field映射）
    source_field = metric_def.get("source_field", metric)
    metric_col = None
    if not calc_expr:
        metric_col = getattr(model, source_field) if source_field != "id" else model.id
    
    # 确定实际使用的聚合方式（指标可预设agg）
    actual_agg = metric_def.get("agg", agg)
    
    if actual_agg == "count":
        # 计数聚合: 始终统计记录条数(id count), 而不是用户选的金额字段
        agg_expr = func.count(model.id)
    elif calc_expr:
        # 计算字段: 使用SQL表达式
        agg_expr = calc_expr(model, metric_col)
    elif actual_agg == "sum":
        agg_expr = func.sum(metric_col)
    elif actual_agg == "avg":
        agg_expr = func.avg(metric_col)
    else:
        return {"error": f"无效聚合:{agg}"}

    # 收集跨表JOIN(按join条件去重)
    joins_to_apply = []
    _seen_join = set()
    _used_dim_bases = []
    if rows_dim:
        _used_dim_bases.append(rows_dim.split(":")[0])
    if cols_dim:
        _used_dim_bases.append(cols_dim.split(":")[0])
    for fld, *_ in extra_dims:
        _used_dim_bases.append(fld)
    for f in (filters or []):
        if f.get("field"):
            _used_dim_bases.append(f["field"])
    for d in _used_dim_bases:
        jc = ds.get("joins", {}).get(d)
        if not jc:
            continue
        key = (jc["model"], jc["local"], jc["remote"])
        if key in _seen_join:
            continue
        _seen_join.add(key)
        joins_to_apply.append(jc)

    if r_expr is not None:
        q = db.query(r_expr.label("r_dim"))
        if c_expr is not None:
            q = q.add_columns(c_expr.label("c_dim"))
        # 添加extra_dims到查询中
        for i, (fld, e_expr, e_label, e_conf) in enumerate(extra_dims):
            q = q.add_columns(e_expr.label(f"extra_{i}"))
        q = q.add_columns(agg_expr.label("metric"))

        for jc in joins_to_apply:
            q = q.join(jc["model"], getattr(ds["model"], jc["local"]) == getattr(jc["model"], jc["remote"]))

        q = _apply_filters(db, q, ds, model, filters)

        if role_filter is not None:
            q = role_filter(q)

        q = q.group_by(r_expr)
        if c_expr is not None:
            q = q.group_by(c_expr)
        # 添加extra_dims到GROUP BY
        for i, (fld, e_expr, e_label, e_conf) in enumerate(extra_dims):
            q = q.group_by(e_expr)
        q = q.order_by(r_expr)

        rows = q.all()
    else:
        # 无维度: 全局聚合查询(如统计总数)
        q = db.query(agg_expr.label("metric"))
        for jc in joins_to_apply:
            q = q.join(jc["model"], getattr(ds["model"], jc["local"]) == getattr(jc["model"], jc["remote"]))
        q = _apply_filters(db, q, ds, model, filters)
        if role_filter is not None:
            q = role_filter(q)
        rows = q.all()

    # 构造透视表
    row_keys = []
    col_keys = []
    cells = {}
    extra_values = {}  # 保存每个row_key对应的extra字段值
    for r in rows:
        if r_expr is None:
            # 无维度全局聚合: 单行总计
            rk = "总计"
            if rk not in row_keys:
                row_keys.append(rk)
            cells[(rk, "__total__")] = float(r.metric or 0)
            continue
        rk = _translate_val(db, r.r_dim, r_conf)
        if rk not in row_keys:
            row_keys.append(rk)
        # 保存extra_dims的值
        if extra_dims:
            extra_val_list = []
            for i, (fld, e_expr, e_label, e_conf) in enumerate(extra_dims):
                raw_val = getattr(r, f"extra_{i}", None)
                translated = _translate_val(db, raw_val, e_conf)
                extra_val_list.append(translated)
            extra_values[rk] = extra_val_list
        if c_expr is not None:
            ck = _translate_val(db, r.c_dim, c_conf)
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
        # 添加extra_dims列
        if extra_dims and rk in extra_values:
            for i, (fld, e_expr, e_label, e_conf) in enumerate(extra_dims):
                row[f"extra_{i}"] = extra_values[rk][i]
        for ck in col_keys:
            row[ck] = cells.get((rk, ck), 0)
        table.append(row)

    # 图表类型判断 (默认使用柱状图，更通用)
    has_cross = cols_dim is not None
    single_series = c_expr is None or len(col_keys) <= 1
    is_time_dim = bool(rows_dim) and any(g in rows_dim for g in [":month", ":quarter", ":year", ":week"])

    if chart_type in ("bar", "line", "pie"):
        pass
    elif has_cross:
        chart_type = "bar"
    elif is_time_dim:
        chart_type = "line"
    else:
        chart_type = "bar"

    chart = {"x": row_keys, "series": [], "chart_type": chart_type}
    if c_expr is not None and len(col_keys) > 1:
        for ck in col_keys:
            chart["series"].append({
                "name": ck,
                "data": [cells.get((rk, ck), 0) for rk in row_keys],
            })
    else:
        ck = col_keys[0] if col_keys else "__total__"
        chart["series"].append({
            "name": metric_def.get("label", metric),
            "data": [cells.get((rk, ck), 0) for rk in row_keys],
        })

    total = sum(cells.values()) if cells else 0
    result = {
        "dataset": dataset, "dataset_label": ds["label"],
        "rows_dim": rows_dim, "rows_label": r_label,
        "cols_dim": cols_dim, "cols_label": c_label,
        "metric": metric, "metric_label": metric_def.get("label", metric),
        "metric_type": metric_def.get("type", "count"),
        "agg": agg,
        "row_keys": row_keys, "col_keys": col_keys,
        "table": table, "chart": chart,
        "summary": {"total": total, "count": len(row_keys)},
        # 添加extra_dims信息供前端渲染
        "extra_dims": [{"field": fld, "label": e_label, "index": i} for i, (fld, e_expr, e_label, e_conf) in enumerate(extra_dims)],
    }

    filter_cfg = ds.get("filter_fields", {})
    result["filter_fields"] = [{"key": k, **v} for k, v in filter_cfg.items()]

    return result


# ============ 数据源列表 (供前端) ============
def list_datasets() -> dict:
    """返回可用数据源/维度/指标/筛选,供前端选择"""
    ds = _get_datasets()
    out = {}
    for k, v in ds.items():
        dims_list = []
        for dk, dv in v["dims"].items():
            if isinstance(dv, dict):
                dims_list.append({
                    "key": dk, "label": dv["label"],
                    "has_fk": "fk" in dv, "has_enum": "enum" in dv,
                })
            else:
                dims_list.append({"key": dk, "label": dv})
        metrics_list = []
        for mk, mv in v["metrics"].items():
            if isinstance(mv, dict):
                metrics_list.append({"key": mk, "label": mv["label"], "type": mv.get("type", "count")})
            else:
                metrics_list.append({"key": mk, "label": mv})
        # 跨表JOIN维度(关联表字段,标记type=join)
        for jk, jv in v.get("joins", {}).items():
            dims_list.append({"key": jk, "label": jv["label"], "has_fk": False, "has_enum": False, "join": True})
        filter_list = []
        for fk, fv in v.get("filter_fields", {}).items():
            filter_list.append({"key": fk, **fv})
        time_field = v.get("time_field")
        time_dims = []
        if time_field:
            time_dims = [
                {"key": f"{time_field}:month", "label": "月"},
                {"key": f"{time_field}:quarter", "label": "季"},
                {"key": f"{time_field}:year", "label": "年"},
                {"key": f"{time_field}:week", "label": "周"},
            ]
        out[k] = {
            "label": v["label"],
            "time_field": time_field,
            "dims": dims_list,
            "time_dims": time_dims,
            "metrics": metrics_list,
            "aggs": [
                {"key": "count", "label": "计数"},
                {"key": "sum", "label": "求和"},
                {"key": "avg", "label": "平均"},
            ],
            "filter_fields": filter_list,
        }
    return out