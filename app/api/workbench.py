"""工作台聚合接口"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import get_user_role_code
from app.models.system import User, Role
from app.models.approval import FlowDefinition, FlowInstance, FlowTask
from app.models.order import Order
from app.models.purchase import PurchaseRequest, Purchase
from app.models.workshop import WorkOrder, Completion
from app.models.sales import ReceivingLog, SalesAdjustment
from app.models.expense import ExpenseClaim
from app.models.inventory import InventoryItem
from app.models.finance import FinanceDoc
from app.schemas import Resp
import json

router = APIRouter(prefix="/api/workbench", tags=["workbench"])


# 角色→可见应用分组映射
APP_GROUPS = {
    "ADMIN": {
        "销售业务": [
            {"key": "orders", "label": "销售订单", "icon": "cart", "color": "blue"},
            {"key": "customers", "label": "客户档案", "icon": "users", "color": "orange"},
            {"key": "receiving", "label": "来货登记", "icon": "cube", "color": "green"},
            {"key": "sales-adjustments", "label": "调价申请", "icon": "edit", "color": "cyan"},
        ],
        "生产车间": [
            {"key": "work-orders", "label": "加工工单", "icon": "wrench", "color": "purple"},
            {"key": "completions", "label": "完工单", "icon": "check-circle", "color": "green"},
            {"key": "requisitions", "label": "领料出库", "icon": "box", "color": "orange"},
        ],
        "采购供应": [
            {"key": "purchase-requests", "label": "采购申请", "icon": "file", "color": "blue"},
            {"key": "purchases", "label": "采购订单", "icon": "truck", "color": "cyan"},
        ],
        "财务资金": [
            {"key": "finance", "label": "财务单据", "icon": "cash", "color": "green"},
            {"key": "receivables", "label": "应收管理", "icon": "wallet", "color": "orange"},
            {"key": "payroll", "label": "工资管理", "icon": "users", "color": "purple"},
            {"key": "expense", "label": "费用报销", "icon": "receipt", "color": "red"},
        ],
        "仓储管理": [
            {"key": "inventory", "label": "库存查询", "icon": "box", "color": "cyan"},
            {"key": "stock-moves", "label": "出入库流水", "icon": "arrow-swap", "color": "blue"},
        ],
        "系统管理": [
            {"key": "approval-flows", "label": "流程设计器", "icon": "flow", "color": "purple"},
            {"key": "approvals", "label": "审批中心", "icon": "check", "color": "orange"},
            {"key": "users", "label": "用户管理", "icon": "users", "color": "blue"},
            {"key": "roles", "label": "角色管理", "icon": "shield", "color": "cyan"},
            {"key": "ai-analysis", "label": "AI 经营分析", "icon": "sparkles", "color": "cyan"},
            {"key": "agent", "label": "AI 助手", "icon": "bot", "color": "green"},
            {"key": "screen", "label": "车间大屏", "icon": "tv", "color": "blue"},
        ],
    },
    "SALES": {
        "销售业务": [
            {"key": "orders", "label": "销售订单", "icon": "cart", "color": "blue"},
            {"key": "customers", "label": "客户档案", "icon": "users", "color": "orange"},
            {"key": "sales-adjustments", "label": "调价申请", "icon": "edit", "color": "cyan"},
        ],
        "查询统计": [
            {"key": "requisitions", "label": "领料查询", "icon": "cube", "color": "purple"},
            {"key": "finance", "label": "收款查询", "icon": "cash", "color": "green"},
        ],
    },
    "GM": {
        "审批与分析": [
            {"key": "approvals", "label": "待我审批", "icon": "check", "color": "orange"},
            {"key": "ai-analysis", "label": "AI 经营分析", "icon": "sparkles", "color": "cyan"},
        ],
        "经营总览": [
            {"key": "orders", "label": "销售订单", "icon": "cart", "color": "blue"},
            {"key": "finance", "label": "财务报表", "icon": "cash", "color": "green"},
            {"key": "screen", "label": "车间大屏", "icon": "tv", "color": "purple"},
            {"key": "expense", "label": "费用报销", "icon": "receipt", "color": "red"},
            {"key": "purchase-requests", "label": "采购申请", "icon": "file", "color": "blue"},
        ],
    },
    "FINANCE": {
        "财务核心": [
            {"key": "finance", "label": "财务单据", "icon": "cash", "color": "green"},
            {"key": "receivables", "label": "应收管理", "icon": "wallet", "color": "orange"},
            {"key": "approvals", "label": "待审批", "icon": "check", "color": "orange"},
            {"key": "payroll", "label": "工资管理", "icon": "users", "color": "purple"},
            {"key": "expense", "label": "费用报销", "icon": "receipt", "color": "red"},
        ],
        "查询": [
            {"key": "orders", "label": "订单查询", "icon": "cart", "color": "blue"},
            {"key": "purchases", "label": "采购订单", "icon": "truck", "color": "cyan"},
        ],
    },
    "OPERATION": {
        "运营业务": [
            {"key": "orders", "label": "销售订单", "icon": "cart", "color": "blue"},
            {"key": "receiving", "label": "来货登记核对", "icon": "cube", "color": "green"},
            {"key": "work-orders", "label": "加工工单", "icon": "wrench", "color": "purple"},
            {"key": "completions", "label": "完工确认", "icon": "check-circle", "color": "green"},
        ],
    },
    "WAREHOUSE": {
        "仓储业务": [
            {"key": "inventory", "label": "库存查询", "icon": "box", "color": "cyan"},
            {"key": "requisitions", "label": "领料出库", "icon": "arrow-right", "color": "orange"},
            {"key": "stock-moves", "label": "出入库流水", "icon": "arrow-swap", "color": "blue"},
            {"key": "purchases", "label": "采购收货", "icon": "truck", "color": "green"},
        ],
    },
    "MANAGER": {
        "车间生产": [
            {"key": "work-orders", "label": "加工工单", "icon": "wrench", "color": "purple"},
            {"key": "completions", "label": "完工上报", "icon": "check-circle", "color": "green"},
            {"key": "requisitions", "label": "领料申请", "icon": "cube", "color": "orange"},
        ],
    },
    "DEPARTMENT_HEAD": {
        "审批与管理": [
            {"key": "approvals", "label": "待我审批", "icon": "check", "color": "orange"},
            {"key": "expense", "label": "费用报销", "icon": "receipt", "color": "red"},
            {"key": "purchase-requests", "label": "采购申请", "icon": "file", "color": "blue"},
        ],
    },
    "PURCHASE": {
        "采购业务": [
            {"key": "purchase-requests", "label": "采购申请", "icon": "file", "color": "blue"},
            {"key": "purchases", "label": "采购订单", "icon": "truck", "color": "cyan"},
        ],
    },
}

# 工作流定义: biz_type -> {title, nodes_role顺序, icon_map}
WORKFLOW_DEFS = {
    "RECEIVING": {
        "title": "来货登记流程",
        "icon": "cube",
        "roles": ["", "OPERATION", "FINANCE", "OPERATION"],
        "route_map": {"OPERATION": "approvals", "FINANCE": "approvals", "": "receiving"},
    },
    "COMPLETION": {
        "title": "完工单确认",
        "icon": "check-circle",
        "roles": ["MANAGER", "MANAGER", "OPERATION"],
        "route_map": {"MANAGER": "approvals", "OPERATION": "approvals"},
    },
    "EXPENSE": {
        "title": "费用报销审批",
        "icon": "receipt",
        "roles": ["DEPARTMENT_HEAD", "FINANCE", "GM"],
        "route_map": {"DEPARTMENT_HEAD": "approvals", "FINANCE": "approvals", "GM": "approvals"},
    },
    "PURCHASE_REQUEST": {
        "title": "采购请求审批",
        "icon": "file",
        "roles": ["DEPARTMENT_HEAD", "FINANCE"],
        "route_map": {"DEPARTMENT_HEAD": "approvals", "FINANCE": "approvals"},
    },
    "SALES_ADJUSTMENT": {
        "title": "调价申请审批",
        "icon": "edit",
        "roles": ["GM"],
        "route_map": {"GM": "approvals"},
    },
    "procurement": {
        "title": "采购审批流",
        "icon": "truck",
    },
    "core_production": {
        "title": "核心生产流",
        "icon": "flow",
    },
}

# 工作流 → 可见角色白名单 (项目记忆硬约束)
# 仅用于在节点approver_role之外,额外保证业务相关角色一定能看到该工作流面板
WF_VISIBLE_ROLES = {
    "RECEIVING":        {"WAREHOUSE", "OPERATION", "FINANCE", "ADMIN", "GM", "SALES"},
    "COMPLETION":       {"MANAGER", "OPERATION", "ADMIN", "GM"},
    "EXPENSE":          {"DEPARTMENT_HEAD", "FINANCE", "GM", "ADMIN"},
    "PURCHASE_REQUEST": {"DEPARTMENT_HEAD", "FINANCE", "GM", "ADMIN", "PURCHASE"},
    "SALES_ADJUSTMENT": {"SALES", "GM", "ADMIN"},
    "procurement":      {"DEPARTMENT_HEAD", "FINANCE", "GM", "ADMIN", "PURCHASE"},
    # 核心生产流: 项目记忆明确规定可见给 warehouse/sales/operation/manager/finance/GM
    "core_production":  {"WAREHOUSE", "SALES", "OPERATION", "MANAGER", "FINANCE", "GM", "ADMIN"},
}

WF_NODE_ICONS = {
    "RECEIVING": ["log-in", "search", "cash", "archive"],
    "COMPLETION": ["send", "check", "archive"],
    "EXPENSE": ["user-check", "cash", "star"],
    "PURCHASE_REQUEST": ["user-check", "cash"],
    "SALES_ADJUSTMENT": ["star"],
}


def _get_todos(user: User, db: Session):
    """聚合各业务待办数量"""
    todos = []
    role = db.query(Role).filter(Role.id == user.role_id).first() if user.role_id else None
    rc = role.code if role else ""

    # 审批待办(FlowTask)
    pending = db.query(func.count(FlowTask.id)).filter(
        FlowTask.assignee_user_id == user.id, FlowTask.status == "PENDING"
    ).scalar() or 0
    if pending > 0:
        todos.append({"type": "approval", "text": f"有 {pending} 条审批待处理", "count": pending,
                      "route": "approvals", "color": "orange"})

    # 按角色业务待办
    if rc in ("WAREHOUSE", "ADMIN"):
        cnt = db.query(func.count(ReceivingLog.id)).filter(ReceivingLog.status == "PENDING").scalar() or 0
        if cnt:
            todos.append({"type": "receiving", "text": f"{cnt} 条来货待登记", "count": cnt,
                          "route": "receiving", "color": "blue"})
    if rc in ("OPERATION", "ADMIN"):
        cnt = db.query(func.count(ReceivingLog.id)).filter(ReceivingLog.status == "RECEIVED").scalar() or 0
        if cnt:
            todos.append({"type": "recv_check", "text": f"{cnt} 条来货待核对", "count": cnt,
                          "route": "receiving", "color": "orange"})
        cnt = db.query(func.count(Completion.id)).filter(Completion.status == "SUBMITTED").scalar() or 0
        if cnt:
            todos.append({"type": "completion", "text": f"{cnt} 条完工待确认", "count": cnt,
                          "route": "completions", "color": "purple"})
    if rc in ("FINANCE", "ADMIN"):
        cnt = db.query(func.count(ReceivingLog.id)).filter(ReceivingLog.status == "CHECKED").scalar() or 0
        if cnt:
            todos.append({"type": "recv_fin", "text": f"{cnt} 条来货待入账", "count": cnt,
                          "route": "finance", "color": "green"})
    if rc == "MANAGER":
        cnt = db.query(func.count(WorkOrder.id)).filter(WorkOrder.status == "RELEASED").scalar() or 0
        if cnt:
            todos.append({"type": "wo", "text": f"{cnt} 个工单待加工", "count": cnt,
                          "route": "work-orders", "color": "purple"})
    return todos


def _get_kpis(db: Session):
    """经营概览KPI"""
    today = datetime.now().date()
    month_start = today.replace(day=1)

    today_orders = db.query(func.count(Order.id)).filter(
        func.date(Order.created_at) == today.isoformat()
    ).scalar() or 0
    month_sales = db.query(func.coalesce(func.sum(Order.total_amount), 0)).filter(
        Order.created_at >= month_start.isoformat()
    ).scalar() or 0
    pending_ap = db.query(func.count(FlowTask.id)).filter(FlowTask.status == "PENDING").scalar() or 0
    low_stock = db.query(func.count(InventoryItem.id)).filter(InventoryItem.stock_qty <= InventoryItem.safety_qty).scalar() or 0

    return [
        {"key": "today_orders", "label": "今日订单", "value": today_orders, "color": "blue"},
        {"key": "month_sales", "label": "本月销售额", "value": f"¥{month_sales:,.0f}", "color": "green"},
        {"key": "pending_ap", "label": "待审批", "value": pending_ap, "color": "orange"},
        {"key": "low_stock", "label": "低库存告警", "value": low_stock, "color": "red"},
    ]


def _get_workflow_steps(user: User, db: Session):
    """按角色返回可见工作流及各节点待办数"""
    from app.api.approvals import _parse_nodes as _parse_flow_nodes

    role = db.query(Role).filter(Role.id == user.role_id).first() if user.role_id else None
    rc = role.code if role else ""
    is_admin = rc == "ADMIN"

    flows = db.query(FlowDefinition).filter(FlowDefinition.status == "ACTIVE").all()
    result = []

    NODE_TYPE_ICONS = {
        "start": "play", "end": "stop", "process": "arrow-right",
        "cc": "mail", "branch": "fork", "approve": "check", "item": "check",
    }

    for fd in flows:
        wf_meta = WORKFLOW_DEFS.get(fd.biz_type)
        nodes = _parse_flow_nodes(fd.nodes)

        # 判断该用户是否参与此工作流:
        # 1) 节点approver_role == 本人角色 或 SUBMITTER
        # 2) biz_type在WF_VISIBLE_ROLES白名单中包含本人角色(解决SALES看不到core_production问题)
        user_roles_in_wf = set()
        for n in nodes:
            ar = n.get("approver_role", "")
            if ar == rc or ar == "SUBMITTER":
                user_roles_in_wf.add(ar)
        visible_whitelist = WF_VISIBLE_ROLES.get(fd.biz_type, set())
        visible_by_whitelist = rc in visible_whitelist
        if not is_admin and not user_roles_in_wf and not visible_by_whitelist:
            continue

        # 构建工作流节点可视化数据
        wf_nodes = []
        for i, n in enumerate(nodes):
            ar = n.get("approver_role", "")
            ntype = n.get("type", "approve")
            nname = n.get("name", "") or f"节点{i+1}"

            icon = NODE_TYPE_ICONS.get(ntype, "circle")

            # 统计该节点待办数(仅approve/item类型统计待办)
            count = 0
            if ntype in ("approve", "item") and (is_admin or ar == rc or ar == "SUBMITTER"):
                count = db.query(func.count(FlowTask.id)).join(FlowInstance).filter(
                    FlowTask.node_seq == n.get("seq", i + 1),
                    FlowTask.status == "PENDING",
                    FlowInstance.definition_id == fd.id,
                    FlowInstance.status == "RUNNING",
                ).scalar() or 0
                if ar == "SUBMITTER" and not is_admin:
                    count = db.query(func.count(FlowTask.id)).join(FlowInstance).filter(
                        FlowTask.node_seq == n.get("seq", i + 1),
                        FlowTask.status == "PENDING",
                        FlowInstance.definition_id == fd.id,
                        FlowInstance.status == "RUNNING",
                        FlowInstance.initiator_user_id == user.id,
                    ).scalar() or 0

            # 节点状态判定
            if ntype in ("process", "cc", "start", "end"):
                status = "auto"  # 自动节点,无需操作
            elif count > 0:
                status = "active"  # 有待办
            else:
                status = "pending"  # 待处理

            route = None
            if count > 0 and ntype in ("approve", "item"):
                route = "approvals"

            wf_nodes.append({
                "name": nname, "icon": icon, "status": status,
                "count": count if count > 0 else None, "route": route,
                "ntype": ntype,
            })

        result.append({
            "title": wf_meta["title"] if wf_meta else fd.name,
            "biz_type": fd.biz_type,
            "definition_id": fd.id,
            "nodes": wf_nodes,
        })

    return result


@router.get("")
def workbench(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    role = db.query(Role).filter(Role.id == user.role_id).first() if user.role_id else None
    rc = role.code if role else "ADMIN"

    apps = APP_GROUPS.get(rc, APP_GROUPS["ADMIN"])
    # AI分析仅总经理和Admin可见
    if rc not in ("ADMIN", "GM"):
        for gname, glist in list(apps.items()):
            apps[gname] = [a for a in glist if a["key"] != "ai-analysis"]
            if not apps[gname]:
                del apps[gname]

    return Resp.ok({
        "todos": _get_todos(user, db),
        "apps": apps,
        "workflow_steps": _get_workflow_steps(user, db),
        "kpis": _get_kpis(db),
    })


@router.get("/done")
def done_items(page: int = 1, size: int = 10,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """最近已办/完成事项"""
    items = []
    # 已审批的任务
    tasks = db.query(FlowTask).filter(
        FlowTask.assignee_user_id == user.id, FlowTask.status.in_(["APPROVED", "REJECTED"])
    ).order_by(FlowTask.handled_at.desc()).limit(size).all()

    for t in tasks:
        inst = db.query(FlowInstance).get(t.instance_id)
        fd = db.query(FlowDefinition).get(inst.definition_id) if inst else None
        action = "审批通过" if t.status == "APPROVED" else "审批驳回"
        color = "green" if t.status == "APPROVED" else "red"
        items.append({
            "id": f"ft-{t.id}", "title": f"{action} {fd.name if fd else ''} #{inst.biz_id if inst else ''}",
            "time": t.handled_at.strftime("%m-%d %H:%M") if t.handled_at else "",
            "color": color, "type": "approval",
        })

    # 补足示例数据如果不够
    if len(items) < size:
        items.extend([
            {"id": "s1", "title": "订单处理完成", "time": "10:30", "color": "blue", "type": "system"},
            {"id": "s2", "title": "工单已下达", "time": "09:15", "color": "purple", "type": "system"},
        ][:size - len(items)])

    return Resp.ok(items)
