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

def _bjt_str(dt):
    """UTC时间转北京时间字符串"""
    if not dt:
        return ""
    return (dt + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
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
            {"key": "analysis", "label": "AI 经营分析", "icon": "sparkles", "color": "cyan"},
            {"key": "screen", "label": "车间大屏", "icon": "tv", "color": "blue"},
        ],
    },
    "SALES": {
        "销售业务": [
            {"key": "orders", "label": "销售订单", "icon": "cart", "color": "blue"},
            {"key": "customers", "label": "客户档案", "icon": "users", "color": "orange"},
            {"key": "sales-adjustments", "label": "调价申请", "icon": "edit", "color": "cyan"},
            {"key": "receiving", "label": "来货登记", "icon": "cube", "color": "green"},
        ],
        "审批中心": [
            {"key": "approvals", "label": "审批中心", "icon": "check", "color": "orange"},
        ],
    },
    "GM": {
        "审批与管理": [
            {"key": "approvals", "label": "待我审批", "icon": "check", "color": "orange"},
            {"key": "analysis", "label": "AI 经营分析", "icon": "sparkles", "color": "cyan"},
            {"key": "users", "label": "用户管理", "icon": "users", "color": "blue"},
            {"key": "roles", "label": "角色管理", "icon": "shield", "color": "cyan"},
            {"key": "approval-flows", "label": "流程设计器", "icon": "flow", "color": "purple"},
        ],
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
            {"key": "screen", "label": "车间大屏", "icon": "tv", "color": "blue"},
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
            {"key": "workflow-list", "label": "业务流程", "icon": "flow", "color": "purple"},
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
            {"key": "receiving", "label": "来货登记核对", "icon": "cube", "color": "green"},
            {"key": "work-orders", "label": "加工工单", "icon": "wrench", "color": "purple"},
            {"key": "completions", "label": "完工确认", "icon": "check-circle", "color": "green"},
            {"key": "approvals", "label": "待审批", "icon": "check", "color": "orange"},
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
            {"key": "approvals", "label": "审批中心", "icon": "check", "color": "orange"},
        ],
    },
}

# 工作流定义: biz_type -> {title, icon, roles(节点审批角色顺序,与main.py流程定义一致)}
WORKFLOW_DEFS = {
    "RECEIVING": {
        "title": "来货登记流程",
        "icon": "cube",
        "roles": ["WAREHOUSE", "OPERATION", "FINANCE", "OPERATION"],
        "route_map": {"OPERATION": "approvals", "FINANCE": "approvals", "WAREHOUSE": "approvals"},
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
        "roles": ["DEPARTMENT_HEAD", "FINANCE", "GM"],
        "route_map": {"DEPARTMENT_HEAD": "approvals", "FINANCE": "approvals", "GM": "approvals"},
    },
    "SALES_ADJUSTMENT": {
        "title": "调价申请审批",
        "icon": "edit",
        "roles": ["GM"],
        "route_map": {"GM": "approvals"},
    },
    "PROCUREMENT": {
        "title": "采购审批流",
        "icon": "truck",
        "roles": ["DEPARTMENT_HEAD", "FINANCE", "GM"],
    },
    "CORE_PRODUCTION": {
        "title": "核心生产流",
        "icon": "flow",
        "roles": ["DEPARTMENT_HEAD", "FINANCE", "GM", "WAREHOUSE", "OPERATION", "FINANCE",
                  "MANAGER", "MANAGER", "MANAGER", "OPERATION", "OPERATION"],
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
    "PROCUREMENT":      {"DEPARTMENT_HEAD", "FINANCE", "GM", "ADMIN", "PURCHASE"},
    # 核心生产流: 采购需要发起流程,部门主管需要审批,其他角色需要查看进度
    "CORE_PRODUCTION":  {"WAREHOUSE", "SALES", "OPERATION", "MANAGER", "FINANCE", "GM", "ADMIN", "PURCHASE", "DEPARTMENT_HEAD"},
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
    is_admin = rc in ("ADMIN", "GM")

    # 审批待办(FlowTask) - ADMIN/GM看全部，其他角色只看分配给自己的
    q_pending = db.query(func.count(FlowTask.id)).filter(FlowTask.status == "PENDING")
    if not is_admin:
        q_pending = q_pending.filter(
            (FlowTask.assignee_user_id == user.id) | (FlowTask.role_id == user.role_id)
        )
    pending = q_pending.scalar() or 0
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
    """返回流程实例列表 - 显示每个单据的流程推进状态"""
    from app.api.approvals import _parse_nodes as _parse_flow_nodes, _biz_brief as _get_biz_brief

    role = db.query(Role).filter(Role.id == user.role_id).first() if user.role_id else None
    rc = role.code if role else ""
    is_admin = rc == "ADMIN"

    # 管理员不显示业务流程面板 - 管理员职责是设计/管理流程，不是执行流程
    if is_admin:
        return []

    NODE_TYPE_ICONS = {
        "start": "play", "end": "stop", "process": "arrow-right",
        "cc": "mail", "branch": "fork", "approve": "check", "item": "check",
    }

    # 查询当前用户可见的所有流程实例
    # 1. 用户作为发起人的流程实例
    my_instances = db.query(FlowInstance).filter(
        FlowInstance.initiator_user_id == user.id
    ).all()
    
    # 2. 用户有待办任务的流程实例 (不限任务状态)
    my_tasks = db.query(FlowTask).filter(
        (FlowTask.assignee_user_id == user.id) | (FlowTask.role_id == user.role_id)
    ).all()
    task_instance_ids = set(t.instance_id for t in my_tasks)
    
    # 3. 根据角色白名单可见的流程实例
    # 查询所有流程实例（不限状态），检查业务类型是否在当前角色的白名单中
    wl_instances = []
    if rc:
        visible_types = [bt for bt, roles in WF_VISIBLE_ROLES.items() if rc in roles]
        if visible_types:
            wl_instances = db.query(FlowInstance).filter(
                FlowInstance.biz_type.in_(visible_types)
            ).order_by(FlowInstance.started_at.desc()).limit(50).all()
    
    # 合并所有可见的流程实例
    visible_instances = set()
    for inst in my_instances:
        visible_instances.add(inst.id)
    for tid in task_instance_ids:
        visible_instances.add(tid)
    for inst in wl_instances:
        visible_instances.add(inst.id)
    
    if not visible_instances:
        return []
    
    # 查询所有可见的流程实例（限制数量）
    instances = db.query(FlowInstance).filter(FlowInstance.id.in_(visible_instances)).order_by(FlowInstance.started_at.desc()).limit(20).all()
    
    result = []
    for inst in instances:
        # 获取流程定义
        fd = db.query(FlowDefinition).get(inst.definition_id)
        if not fd:
            continue
        
        # 获取流程定义的节点
        nodes_def = _parse_flow_nodes(fd.nodes)
        
        # 获取该实例的所有任务
        tasks = db.query(FlowTask).filter(FlowTask.instance_id == inst.id).all()
        task_map = {t.node_seq: t for t in tasks}
        
        # 获取业务单据信息
        brief = _get_biz_brief(db, inst.biz_type, inst.biz_id)
        
        # 构建节点显示数据 - 收集审批历史
        wf_nodes = []
        approve_history = []  # 所有已完成节点的审批记录
        
        for i, n in enumerate(nodes_def):
            seq = n.get("seq", i + 1)
            ntype = n.get("type", "approve")
            nname = n.get("name", "") or f"节点{i+1}"
            ar = n.get("approver_role", "")
            
            icon = NODE_TYPE_ICONS.get(ntype, "circle")
            task = task_map.get(seq)
            
            # 判断节点状态
            if task and task.status in ("APPROVED", "REJECTED"):
                status = "done" if task.status == "APPROVED" else "rejected"
                icon = "check"
            elif task and task.status == "PENDING" and inst.status == "RUNNING" and seq == inst.current_node_seq:
                if ar == rc or ar == "SUBMITTER" or is_admin:
                    status = "active"
                else:
                    status = "current"
                icon = NODE_TYPE_ICONS.get(ntype, "circle")  # 当前节点保留类型图标
            else:
                status = "pending"
                icon = "circle"  # 待处理节点使用通用图标，不显示 check
            
            # 获取节点详情
            count = 0
            assignee_name = ""
            approved_at = None
            comment = ""
            if task:
                if task.assignee_user_id:
                    assignee = db.query(User).get(task.assignee_user_id)
                    if assignee:
                        assignee_name = assignee.name
                if task.status in ("APPROVED", "REJECTED") and task.handled_at:
                    approved_at = task.handled_at.isoformat() if task.handled_at else None
                if task.comment:
                    comment = task.comment
            
            # 收集已完成节点到审批历史
            node_approve_info = None
            if task and task.status in ("APPROVED", "REJECTED"):
                node_approve_info = {
                    "seq": seq,
                    "name": nname,
                    "status": "通过" if task.status == "APPROVED" else "驳回",
                    "assignee": assignee_name,
                    "approved_at": approved_at,
                    "comment": comment,
                }
                approve_history.append(node_approve_info)
            
            # 构建当前节点可见的审批历史（截至当前节点）
            current_history = list(approve_history)
            # 驳回后不再显示后续节点的历史
            if task and task.status == "REJECTED":
                pass  # 保留已有历史
            
            route = None
            if count > 0:
                route = "approvals"
            
            wf_nodes.append({
                "name": nname, "icon": icon, "status": status,
                "count": count if count > 0 else None, "route": route,
                "ntype": ntype, "seq": seq,
                "task_id": task.id if task else None,
                "assignee": assignee_name,
                "assignee_role": ar,  # 审批角色
                "approved_at": approved_at,
                "comment": comment,
                "status_text": {"done": "已完成", "rejected": "已驳回", "active": "待我处理", "current": "进行中", "pending": "待处理"}.get(status, ""),
                "approve_history": current_history,  # 截至当前节点的审批历史
            })
        
        result.append({
            "title": brief.get("title", fd.name),
            "biz_type": fd.biz_type,
            "definition_id": fd.id,
            "instance_id": inst.id,
            "biz_no": brief.get("no", f"#{inst.biz_id}"),
            "biz_id": inst.biz_id,
            "status": inst.status,
            "nodes": wf_nodes,
            "started_at": inst.started_at.isoformat() if inst.started_at else None,
        })
    
    # 排序：有待办的排在前面，然后按创建时间倒序(最新的在上)
    result.sort(key=lambda x: (
        -sum(1 for n in x["nodes"] if n.get("count")),
        -(x["instance_id"] or 0)
    ))
    
    return result


@router.get("")
def workbench(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    role = db.query(Role).filter(Role.id == user.role_id).first() if user.role_id else None
    rc = role.code if role else "ADMIN"

    apps = APP_GROUPS.get(rc, APP_GROUPS["ADMIN"])
    # 数据库动态权限过滤: 根据 role.pages 过滤工作台入口
    allowed_pages = getattr(role, "pages", None) if role else None
    is_super = rc in ("ADMIN", "GM") or (isinstance(allowed_pages, list) and "*" in allowed_pages)
    if not is_super and isinstance(allowed_pages, list) and allowed_pages:
        allowed = set(allowed_pages)
        new_apps = {}
        for gname, glist in apps.items():
            filtered = [a for a in glist if a["key"] in allowed]
            if filtered:
                new_apps[gname] = filtered
        apps = new_apps
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


@router.get("/todos")
def todo_items(page: int = 1, size: int = 20,
               keyword: str = "", tag: str = "",
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """我的待办 - 列出PENDING审批任务明细"""
    from app.api.approvals import _biz_brief as _get_biz_brief
    role = db.query(Role).filter(Role.id == user.role_id).first() if user.role_id else None
    rc = role.code if role else ""
    is_admin = rc in ("ADMIN", "GM")

    q = db.query(FlowTask).filter(FlowTask.status == "PENDING")
    if not is_admin:
        q = q.filter(
            (FlowTask.assignee_user_id == user.id) | (FlowTask.role_id == (user.role_id or -1))
        )
    tasks = q.order_by(FlowTask.created_at.desc()).all()

    items = []
    for t in tasks:
        inst = db.query(FlowInstance).get(t.instance_id)
        brief = _get_biz_brief(db, inst.biz_type, inst.biz_id) if inst else {"no": "", "title": "", "route": ""}
        biz_type_label = _biz_type_label(inst.biz_type if inst else "")
        biz_no = brief.get("no", "")
        title = f"{biz_type_label} {biz_no or ('#' + str(inst.biz_id if inst else ''))}"
        sub = f"节点: {t.node_name or ('节点' + str(t.node_seq))}"
        if t.status == "PENDING" and inst and inst.status == "RUNNING":
            sub += " · 待您处理"
        items.append({
            "id": f"ft-{t.id}",
            "type_label": biz_type_label or "审批",
            "title": title,
            "sub": sub,
            "time": _bjt_str(t.created_at),
            "color": "orange",
            "tag": "待处理",
            "route": brief.get("route") or "approvals",
            "type": "approval",
            "biz_no": biz_no,
            "instance_id": inst.id if inst else None,
            "task_id": t.id,
        })

    if keyword:
        kw = keyword.lower()
        items = [x for x in items if kw in (x.get("title", "") + x.get("sub", "") + x.get("type_label", "")).lower()]
    if tag:
        items = [x for x in items if x.get("type_label") == tag]

    tag_types = list(set(x.get("type_label") for x in items if x.get("type_label")))
    total = len(items)
    start = (page - 1) * size
    paged_items = items[start:start + size]
    return Resp.ok({"items": paged_items, "total": total, "tag_types": tag_types})


@router.get("/done")
def done_items(page: int = 1, size: int = 20,
               keyword: str = "", tag: str = "",
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """已办/完成事项 - 所有历史"""
    from app.api.approvals import _biz_brief as _get_biz_brief
    items = []

    # 1. 用户审批过的任务（作为审批人）
    tasks = db.query(FlowTask).filter(
        FlowTask.assignee_user_id == user.id,
        FlowTask.status.in_(["APPROVED", "REJECTED"])
    ).order_by(FlowTask.handled_at.desc()).all()

    for t in tasks:
        inst = db.query(FlowInstance).get(t.instance_id)
        fd = db.query(FlowDefinition).get(inst.definition_id) if inst else None
        action = "审批通过" if t.status == "APPROVED" else "审批驳回"
        color = "green" if t.status == "APPROVED" else "red"
        biz_type_label = _biz_type_label(fd.biz_type if fd else "")
        brief = _get_biz_brief(db, inst.biz_type, inst.biz_id) if inst else {"no": "", "title": "", "route": ""}
        biz_no = brief.get("no", "")
        title = f"{action} {biz_type_label} {biz_no or ('#' + str(inst.biz_id if inst else ''))}"
        items.append({
            "id": f"ft-{t.id}",
            "type_label": biz_type_label or "审批",
            "title": title,
            "sub": f"节点: {t.node_name}",
            "time": _bjt_str(t.handled_at),
            "color": color,
            "tag": action,
            "route": brief.get("route") or "approvals",
            "type": "approval",
            "biz_no": biz_no,
            "instance_id": inst.id if inst else None,
        })

    # 2. 用户发起的所有流程实例（作为发起人，包括进行中的）
    instances = db.query(FlowInstance).filter(
        FlowInstance.initiator_user_id == user.id,
    ).order_by(FlowInstance.started_at.desc()).all()

    for inst in instances:
        fd = db.query(FlowDefinition).get(inst.definition_id)
        if inst.status == "APPROVED":
            action = "流程完成"
            color = "green"
            status_tag = "已通过"
        elif inst.status == "REJECTED":
            action = "流程驳回"
            color = "red"
            status_tag = "已驳回"
        else:
            action = "流程进行中"
            color = "blue"
            status_tag = "进行中"
        biz_type_label = _biz_type_label(inst.biz_type)
        brief = _get_biz_brief(db, inst.biz_type, inst.biz_id)
        biz_no = brief.get("no", "")
        title = f"{action} {biz_type_label} {biz_no or ('#' + str(inst.biz_id))}"
        started = _bjt_str(inst.started_at)
        finished = _bjt_str(inst.finished_at)
        sub_text = f"发起于 {started}"
        if finished:
            sub_text += f" · 完成于 {finished}"
        if inst.status == "RUNNING":
            sub_text += f" · 当前节点: {inst.current_node_seq}"
        items.append({
            "id": f"fi-{inst.id}",
            "type_label": biz_type_label or "流程",
            "title": title,
            "sub": sub_text,
            "time": finished or started,
            "color": color,
            "tag": status_tag,
            "route": brief.get("route") or "approvals",
            "type": "instance",
            "biz_no": biz_no,
            "instance_id": inst.id,
        })

    # 3. 排序 - 按时间倒序
    items.sort(key=lambda x: str(x.get("time", "")), reverse=True)

    # 4. 过滤
    if keyword:
        kw = keyword.lower()
        items = [x for x in items if kw in (x.get("title", "") + x.get("sub", "") + x.get("type_label", "")).lower()]
    if tag:
        items = [x for x in items if x.get("type_label") == tag]

    # 5. 统计标签类型
    tag_types = list(set(x.get("type_label") for x in items if x.get("type_label")))

    total = len(items)
    start = (page - 1) * size
    paged_items = items[start:start + size]

    return Resp.ok({"items": paged_items, "total": total, "tag_types": tag_types})


def _biz_type_label(biz_type: str) -> str:
    """业务类型中文标签"""
    labels = {
        "PURCHASE": "采购单",
        "EXPENSE": "报销单",
        "ORDER_RETURN": "退单",
        "PAYROLL": "工资单",
        "RECEIVING": "来货登记",
        "COMPLETION": "完工单",
        "CORE_PRODUCTION": "生产流程",
        "SALES": "销售流程",
        "SALES_ADJUSTMENT": "调价申请",
    }
    return labels.get(biz_type, biz_type or "审批")


@router.get("/workflow-steps")
def workflow_steps_paginated(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=50),
    keyword: str = None,
    status: str = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """返回流程实例列表（分页）"""
    all_steps = _get_workflow_steps(user, db)
    
    # 过滤
    if keyword:
        kw = keyword.lower()
        all_steps = [s for s in all_steps if kw in (s.get("biz_no", "") + s.get("title", "")).lower()]
    if status:
        all_steps = [s for s in all_steps if s.get("status") == status]
    
    total = len(all_steps)
    start = (page - 1) * size
    items = all_steps[start:start + size]
    
    return Resp.ok({"items": items, "total": total})
