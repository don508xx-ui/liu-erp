"""工作流引擎 - 流程定义/实例/任务,支持审批(approve)与流转(process)节点,表驱动可配"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role, get_user_role_code, resolve_role_ids, resolve_role_by_code
from app.core.audit import log_audit

BJT = timezone(timedelta(hours=8))
def bjt_now():
    return datetime.now(BJT).replace(tzinfo=None)

def _bjt_str(dt):
    if not dt:
        return ""
    return (dt + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
from app.models.system import User, Role
from app.models.approval import FlowDefinition, FlowInstance, FlowTask
from app.models.purchase import PurchaseRequest
from app.models.sales import ReceivingLog, SalesAdjustment
from app.models.workshop import Completion
from app.models.expense import ExpenseClaim
from app.models.notification import NotificationLog
from app.schemas import Resp
import json

router = APIRouter(prefix="/api/approvals", tags=["approval"])


def _parse_nodes(raw):
    if isinstance(raw, str):
        try:
            nodes = json.loads(raw) if raw else []
        except Exception:
            return []
    elif isinstance(raw, list):
        nodes = raw
    else:
        return []
    # 向后兼容: flow → process, item → approve
    for n in nodes:
        t = n.get("type", "")
        if t == "flow":
            n["type"] = "process"
        elif t == "item":
            n["type"] = "approve"
    return nodes


# ============ 流程定义 CRUD ============
class FlowDefIn(BaseModel):
    name: str
    biz_type: str
    nodes: List[dict]  # [{seq,name,type:approve|process,approver_role,condition?}]


@router.post("/definitions")
def create_def(body: FlowDefIn, user: User = Depends(require_role("ADMIN")),
               db: Session = Depends(get_db)):
    # 同业务类型旧版本自动停用
    old = db.query(FlowDefinition).filter(
        FlowDefinition.biz_type == body.biz_type, FlowDefinition.status == "ACTIVE"
    ).all()
    for o in old:
        o.status = "INACTIVE"
    fd = FlowDefinition(name=body.name, biz_type=body.biz_type, nodes=body.nodes, status="ACTIVE")
    db.add(fd)
    db.commit()
    return Resp.ok({"id": fd.id})


@router.get("/definitions")
def list_defs(biz_type: Optional[str] = None,
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(FlowDefinition).filter(FlowDefinition.status == "ACTIVE")
    if biz_type:
        q = q.filter(FlowDefinition.biz_type == biz_type)
    rows = q.all()
    result = []
    for f in rows:
        result.append({"id": f.id, "name": f.name, "biz_type": f.biz_type,
                       "nodes": _parse_nodes(f.nodes), "version": f.version})
    return {"code": 0, "data": result}


@router.put("/definitions/{fid}")
def update_def(fid: int, body: FlowDefIn, user: User = Depends(require_role("ADMIN")),
               db: Session = Depends(get_db)):
    fd = db.query(FlowDefinition).get(fid)
    if not fd:
        raise HTTPException(404, "流程定义不存在")
    fd.name = body.name
    fd.nodes = body.nodes
    fd.version = (fd.version or 1) + 1
    db.commit()
    return Resp.ok({"id": fid})


@router.delete("/definitions/{fid}")
def delete_def(fid: int, user: User = Depends(require_role("ADMIN")),
               db: Session = Depends(get_db)):
    fd = db.query(FlowDefinition).get(fid)
    if not fd:
        raise HTTPException(404, "流程定义不存在")
    fd.status = "INACTIVE"
    db.commit()
    return Resp.ok({"id": fid})


# ============ 节点分配工具 ============
def _find_assignee(db: Session, role_code: str, initiator_id: int = None) -> tuple:
    """按角色码找用户,SUBMITTER返回发起人自己。
    - 同角色多人时派给当前待办最少者(负载均衡,避免总压在一人)
    - 角色已停用/合并时通过 role_aliases 归一化到目标角色
    - 找不到人时降级派给首位ACTIVE管理员; 仍无则返回None由引擎自动通过"""
    if not role_code:
        return (None, None)
    if role_code == "SUBMITTER":
        return (initiator_id, None)
    role = resolve_role_by_code(db, role_code)
    if not role:
        return _fallback_admin(db)
    users = db.query(User).filter(User.role_id == role.id, User.status == "ACTIVE").all()
    if not users:
        return _fallback_admin(db)
    if len(users) == 1:
        return (users[0].id, role.id)
    # 负载均衡: 统计各用户当前PENDING任务数, 派给最少者
    best, best_n = None, None
    for u in users:
        n = db.query(FlowTask).filter(
            FlowTask.assignee_user_id == u.id, FlowTask.status == "PENDING"
        ).count()
        if best_n is None or n < best_n:
            best, best_n = u, n
    return (best.id if best else None, role.id)


def _fallback_admin(db: Session) -> tuple:
    """审批人缺失兜底: 派给首位ACTIVE管理员(对标飞书"审批人为空转管理员")"""
    admin_role = resolve_role_by_code(db, "ADMIN")
    if admin_role:
        u = db.query(User).filter(User.role_id == admin_role.id, User.status == "ACTIVE").first()
        if u:
            return (u.id, admin_role.id)
    u = db.query(User).filter(User.status == "ACTIVE").order_by(User.id).first()
    return (u.id, None) if u else (None, None)


def _node_done(db: Session, inst: FlowInstance, node: dict, initiator_id: int, comment: str = "流转自动推进"):
    """标记一个 process 节点为自动完成"""
    db.add(FlowTask(
        instance_id=inst.id, node_seq=node.get("seq", 1), node_name=node.get("name"),
        assignee_user_id=initiator_id, status="APPROVED", handled_at=bjt_now(),
        comment=comment,
    ))


def _eval_condition(expr: str, biz_data: dict) -> bool:
    """分支条件安全评估(ast白名单, 禁调用/属性/导入)。
    支持: 比较/逻辑/算术运算, 变量取自biz_data。评估失败视为不满足(安全侧)。"""
    import ast, operator
    if not expr or not str(expr).strip():
        return True
    _ops = {
        ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Gt: operator.gt,
        ast.GtE: operator.ge, ast.Lt: operator.lt, ast.LtE: operator.le,
        ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Mod: operator.mod,
        ast.And: lambda a, b: a and b, ast.Or: lambda a, b: a or b,
        ast.Not: operator.not_, ast.USub: operator.neg,
    }
    def _ev(node):
        if isinstance(node, ast.Expression):
            return _ev(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return (biz_data or {}).get(node.id)
        if isinstance(node, ast.Compare):
            left = _ev(node.left)
            for op, comp in zip(node.ops, node.comparators):
                fn = _ops.get(type(op))
                if fn is None:
                    raise ValueError("bad op")
                right = _ev(comp)
                try:
                    if not fn(left, right):
                        return False
                except TypeError:
                    return False
                left = right
            return True
        if isinstance(node, ast.BoolOp):
            vals = [_ev(v) for v in node.values]
            if isinstance(node.op, ast.And):
                return all(vals)
            return any(vals)
        if isinstance(node, ast.BinOp):
            fn = _ops.get(type(node.op))
            if fn is None:
                raise ValueError("bad binop")
            return fn(_ev(node.left), _ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ops:
            return _ops[type(node.op)](_ev(node.operand))
        raise ValueError("unsupported")
    try:
        return bool(_ev(ast.parse(str(expr), mode="eval")))
    except Exception:
        return False


def _advance(db: Session, inst: FlowInstance, fd: FlowDefinition, initiator_id: int):
    """推进流程:跳过 start/end/process/cc 节点(自动完成),到 approve 节点创建待办;全走完则终态"""
    nodes = _parse_nodes(fd.nodes)
    # 死循环保护: 最多推进比节点数多100次, 防止形成环的流程定义卡死服务
    max_iter = len(nodes) + 100
    iter_n = 0
    while inst.current_node_seq <= len(nodes) and iter_n < max_iter:
        iter_n += 1
        idx = inst.current_node_seq - 1
        node = nodes[idx]
        ntype = node.get("type", "approve")
        if ntype in ("start", "end", "process", "cc"):
            if ntype in ("process", "cc"):
                _node_done(db, inst, node, initiator_id,
                           "抄送通知已发送" if ntype == "cc" else "流转自动推进")
                if ntype == "cc":
                    _send_cc_notifications(db, inst, node, initiator_id)
            inst.current_node_seq += 1
        elif ntype == "branch":
            # 分支节点: condition满足才进入下一节点, 否则跳过紧随的一个节点
            _node_done(db, inst, node, initiator_id, "分支条件判断完成")
            cond = node.get("condition")
            ok = _eval_condition(cond, inst.get_biz_data())
            inst.current_node_seq += 1 if ok else 2
        elif ntype in ("approve", "item"):
            assignee_id, role_id = _find_assignee(db, node.get("approver_role"), initiator_id)
            if not assignee_id:
                # 极端兜底: 全系统无可用审批人, 自动通过并留痕, 避免流程卡死
                _node_done(db, inst, node, initiator_id, "审批人缺失自动通过")
                inst.current_node_seq += 1
                continue
            db.add(FlowTask(
                instance_id=inst.id, node_seq=node.get("seq", inst.current_node_seq),
                node_name=node.get("name"), assignee_user_id=assignee_id,
                role_id=role_id, status="PENDING",
            ))
            return
        else:
            inst.current_node_seq += 1
    inst.status = "APPROVED"
    inst.finished_at = bjt_now()
    _apply_approval_result(db, inst, True)


def _send_cc_notifications(db: Session, inst: FlowInstance, node: dict, initiator_id: int):
    """抄送节点:向所有抄送角色下的用户发送通知"""
    cc_roles = node.get("cc_roles", [])
    if not cc_roles:
        return
    brief = _biz_brief(db, inst.biz_type, inst.biz_id)
    brief_no = brief.get("no", "")
    brief_title = brief.get("title", "")
    node_name = node.get("name", "抄送通知")
    initiator_name = _user_name(db, initiator_id) or "系统"
    for role_code in cc_roles:
        role = resolve_role_by_code(db, role_code)
        if not role:
            continue
        users = db.query(User).filter(
            User.role_id == role.id, User.status == "ACTIVE"
        ).all()
        for u in users:
            db.add(NotificationLog(
                channel="INAPP", recipient=str(u.id),
                recipient_name=u.name,
                title=f"抄送:{brief_no}",
                body=f"{initiator_name} 抄送您「{node_name}」, {brief_title}",
                status="PENDING",
            ))


# ============ 启动流程 ============
class FlowReopenError(HTTPException):
    pass


def start_flow(db: Session, biz_type: str, biz_id: int, initiator: User,
               biz_data: dict = None, allow_reopen: bool = True) -> Optional[FlowInstance]:
    """启动流程实例。
    - allow_reopen=True(默认): 同biz_id+biz_type若有RUNNING实例则报错;允许REJECTED后重新发起。
    - allow_reopen=False: 严格模式,任何已存在实例都报错。
    """
    fd = db.query(FlowDefinition).filter(
        FlowDefinition.biz_type == biz_type, FlowDefinition.status == "ACTIVE"
    ).order_by(FlowDefinition.version.desc()).first()
    if not fd:
        return None
    # 重复发起限制: 同biz_id+biz_type已有RUNNING实例则禁止
    running = db.query(FlowInstance).filter(
        FlowInstance.biz_type == biz_type,
        FlowInstance.biz_id == biz_id,
        FlowInstance.status == "RUNNING",
    ).first()
    if running:
        raise HTTPException(400, "已有进行中的流程,不可重复发起")
    # 严格模式: 任何实例存在都禁止
    if not allow_reopen:
        exist = db.query(FlowInstance).filter(
            FlowInstance.biz_type == biz_type, FlowInstance.biz_id == biz_id
        ).first()
        if exist:
            raise HTTPException(400, "该业务已发起过流程")
    inst = FlowInstance(
        definition_id=fd.id, biz_type=biz_type, biz_id=biz_id,
        status="RUNNING", current_node_seq=1, initiator_user_id=initiator.id,
    )
    if biz_data:
        inst.set_biz_data(biz_data)
    db.add(inst)
    db.flush()
    _advance(db, inst, fd, initiator.id)
    db.flush()
    return inst


# ============ 处理任务 ============
def _do_handle_task(db: Session, t: FlowTask, inst: FlowInstance, action: str,
                    comment: str, form_data: Optional[dict], user: User) -> FlowInstance:
    """任务处理共用逻辑: 原子状态变更 + 留痕 + 流程推进/驳回落地"""
    before = t.status
    # 原子状态变更: 防止并发重复审批
    new_status = "APPROVED" if action == "approve" else "REJECTED"
    count = db.query(FlowTask).filter(
        FlowTask.id == t.id,
        FlowTask.status == "PENDING"
    ).update({"status": new_status}, synchronize_session=False)
    if count == 0:
        db.rollback()
        raise HTTPException(409, "任务已被其他人处理")

    # 刷新ORM对象与DB同步
    db.refresh(t)

    # 保存表单数据到任务
    if form_data:
        t.set_form_data(form_data)

    # 更新流程实例的biz_data（审批时可回写）
    if form_data and action == "approve":
        current_biz_data = inst.get_biz_data()
        current_biz_data.update(form_data)
        inst.set_biz_data(current_biz_data)

    t.comment = comment or ""
    t.handled_at = bjt_now()
    log_audit(db, user, "approve" if action == "approve" else "reject", "flow_task", t.id, before=before, after=t.status)
    db.flush()
    if action == "reject":
        inst.status = "REJECTED"
        inst.finished_at = bjt_now()
        _apply_approval_result(db, inst, False)
    else:
        fd = db.query(FlowDefinition).get(inst.definition_id)
        inst.current_node_seq = t.node_seq + 1
        _advance(db, inst, fd, inst.initiator_user_id)
    return inst


@router.post("/tasks/{tid}/handle")
def handle_task(tid: int, body: dict, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    t = db.query(FlowTask).get(tid)
    if not t:
        raise HTTPException(404, "任务不存在")
    if t.status != "PENDING":
        raise HTTPException(400, "任务已处理")
    # 权限校验: 仅任务经办人或ADMIN/GM可处理
    rc = get_user_role_code(user, db)
    if rc not in ("ADMIN", "GM") and t.assignee_user_id != user.id:
        raise HTTPException(403, "无权处理该任务")
    action = body.get("action")
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action必须approve/reject")
    inst = db.query(FlowInstance).get(t.instance_id)
    _do_handle_task(db, t, inst, action, body.get("comment", ""), body.get("form_data"), user)
    db.commit()
    return Resp.ok({"task_id": tid, "instance_status": inst.status})


def _current_pending_task(db: Session, inst: FlowInstance) -> Optional[FlowTask]:
    """取实例当前节点的待办任务"""
    return db.query(FlowTask).filter(
        FlowTask.instance_id == inst.id,
        FlowTask.node_seq == inst.current_node_seq,
        FlowTask.status == "PENDING",
    ).order_by(FlowTask.id.desc()).first()


@router.post("/instances/{iid}/approve")
def approve_instance(iid: int, body: dict, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """实例级快捷审批通过(FlowTrack用): 定位当前待办任务后走统一处理逻辑"""
    inst = db.query(FlowInstance).get(iid)
    if not inst or inst.status != "RUNNING":
        raise HTTPException(400, "流程不在进行中")
    t = _current_pending_task(db, inst)
    if not t:
        raise HTTPException(400, "当前节点无待办任务")
    rc = get_user_role_code(user, db)
    if rc not in ("ADMIN", "GM") and t.assignee_user_id != user.id:
        raise HTTPException(403, "无权处理该任务")
    _do_handle_task(db, t, inst, "approve", body.get("comment", ""), body.get("form_data"), user)
    db.commit()
    return Resp.ok({"task_id": t.id, "instance_status": inst.status})


@router.post("/instances/{iid}/reject")
def reject_instance(iid: int, body: dict, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """实例级快捷驳回(FlowTrack用): 驳回必须填意见(对标钉钉/飞书)"""
    inst = db.query(FlowInstance).get(iid)
    if not inst or inst.status != "RUNNING":
        raise HTTPException(400, "流程不在进行中")
    comment = (body.get("comment") or "").strip()
    if not comment:
        raise HTTPException(400, "驳回必须填写审批意见")
    t = _current_pending_task(db, inst)
    if not t:
        raise HTTPException(400, "当前节点无待办任务")
    rc = get_user_role_code(user, db)
    if rc not in ("ADMIN", "GM") and t.assignee_user_id != user.id:
        raise HTTPException(403, "无权处理该任务")
    _do_handle_task(db, t, inst, "reject", comment, body.get("form_data"), user)
    db.commit()
    return Resp.ok({"task_id": t.id, "instance_status": inst.status})


@router.post("/tasks/{tid}/transfer")
def transfer_task(tid: int, body: dict, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """转交任务 - 仅当前审批人或管理员可转交"""
    t = db.query(FlowTask).get(tid)
    if not t or t.status != "PENDING":
        raise HTTPException(400, "任务不可转交")
    # 权限校验: 只有当前审批人或管理员可转交
    rc = get_user_role_code(user, db)
    if t.assignee_user_id != user.id and rc not in ("ADMIN", "GM"):
        raise HTTPException(403, "仅当前审批人或管理员可转交任务")
    to_uid = body.get("to_user_id")
    to_user = db.query(User).get(to_uid) if to_uid else None
    if not to_user:
        raise HTTPException(400, "目标用户不存在")
    if to_user.status != "ACTIVE":
        raise HTTPException(400, "目标用户已被禁用")
    before = t.assignee_user_id
    t.assignee_user_id = to_user.id
    t.role_id = to_user.role_id
    log_audit(db, user, "transfer", "flow_task", tid, before=before, after=to_user.id, extra=body.get("comment", ""))
    db.commit()
    return Resp.ok({"task_id": tid, "assignee": to_user.id})


@router.post("/tasks/{tid}/urge")
def urge_task(tid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """催办"""
    t = db.query(FlowTask).get(tid)
    if not t or t.status != "PENDING":
        raise HTTPException(400, "任务不可催办")
    inst = db.query(FlowInstance).get(t.instance_id)
    brief = _biz_brief(db, inst.biz_type, inst.biz_id)
    node_name = t.node_name or "审批节点"
    brief_no = brief.get("no", "")
    brief_title = brief.get("title", "")
    db.add(NotificationLog(
        channel="INAPP", recipient=str(t.assignee_user_id),
        recipient_name=_user_name(db, t.assignee_user_id),
        title=f"催办提醒:{brief_no}",
        body=f"{user.name} 催您处理「{node_name}」, {brief_title}",
        status="PENDING",
    ))
    db.commit()
    return Resp.ok({"task_id": tid})


# ============ 业务结果落地(泛化注册表) ============
def _set_status(db: Session, model, biz_id: int, status: str, inst_id: int):
    obj = db.query(model).get(biz_id)
    if obj:
        obj.status = status
        if hasattr(obj, "approval_instance_id"):
            obj.approval_instance_id = inst_id


def _on_core_production_approved(db: Session, inst: FlowInstance, ok: bool):
    """核心生产流审批通过 - 订单审批通过后自动转工单(车间仅见生产信息),总经理抄送知晓"""
    from app.models.order import Order
    from app.models.customer import Customer
    from app.models.workshop import WorkOrder
    # 先尝试订单
    o = db.query(Order).get(inst.biz_id)
    if o:
        o.status = "APPROVED" if ok else "REJECTED"
        o.approval_instance_id = inst.id
        if ok:
            # 订单审批通过后变为EFFECTIVE状态
            o.status = "EFFECTIVE"
            o.effective_at = bjt_now()
            _auto_create_work_order(db, o)
        return
    # 再尝试采购申请
    from app.models.purchase import PurchaseRequest
    pr = db.query(PurchaseRequest).get(inst.biz_id)
    if pr:
        pr.status = "APPROVED" if ok else "REJECTED"
        pr.approval_instance_id = inst.id


def _auto_create_work_order(db: Session, o):
    """订单审批通过后, 自动生成加工单。
    - 车间无需知晓商务细节, 仅带生产信息(规格/工艺/数量/交期), 不含客户价格/商务条款。
    - 已为该订单生成过加工单则跳过, 避免重复。"""
    from app.models.order import OrderItem
    from app.models.customer import Customer
    from app.models.workshop import WorkOrder
    exist = db.query(WorkOrder).filter(WorkOrder.order_id == o.id).first()
    if exist:
        return
    items = db.query(OrderItem).filter(OrderItem.order_id == o.id).all()
    cust = db.query(Customer).get(o.customer_id) if o.customer_id else None
    if not items:
        # 无明细也能生成一张空工单, 保证流转闭环
        seq = db.query(WorkOrder).count() + 1
        db.add(WorkOrder(
            work_order_no=f"WO-{bjt_now().strftime('%Y%m%d')}-{seq:04d}",
            order_id=o.id, customer_id=o.customer_id,
            customer_name=cust.name if cust else "",
            product_spec="", process="", workshop="A",
            status="CREATED", plan_qty=0, remark="订单审批通过自动生成",
        ))
        db.flush()
        return
    # 以第一条明细为主生成工单(聚合数量)
    it = items[0]
    total_qty = float(sum((float(i.quantity or 0) for i in items), 0))
    seq = db.query(WorkOrder).count() + 1
    db.add(WorkOrder(
        work_order_no=f"WO-{bjt_now().strftime('%Y%m%d')}-{seq:04d}",
        order_id=o.id, order_item_id=it.id,
        customer_id=o.customer_id,
        customer_name=cust.name if cust else "",
        product_spec=it.part_spec or (it.part_name or ""),
        process=it.process_requirement or "",
        batch_no=f"BATCH-{bjt_now().strftime('%m%d')}-{seq:03d}",
        workshop="A", status="CREATED", plan_qty=total_qty,
        delivery_date=o.extra.get("delivery_date") if isinstance(o.extra, dict) else None,
        remark="订单审批通过自动转工单, 车间仅见生产信息",
    ))
    db.flush()


def _on_recv_approved(db: Session, inst: FlowInstance, ok: bool):
    """来货登记审批通过→更新ReceivingLog状态+同步Order生效"""
    from app.models.order import Order
    r = db.query(ReceivingLog).get(inst.biz_id)
    if not r:
        return
    r.status = "CONFIRMED" if ok else "REJECTED"
    r.approval_instance_id = inst.id
    if ok and r.order_id:
        o = db.query(Order).get(r.order_id)
        if o:
            o.status = "EFFECTIVE"


def _on_sales_adj_approved(db: Session, inst: FlowInstance, ok: bool):
    """调价申请审批通过→更新调价记录状态+同步更新订单total_amount"""
    from app.models.order import Order
    adj = db.query(SalesAdjustment).get(inst.biz_id)
    if not adj:
        return
    adj.status = "APPROVED" if ok else "REJECTED"
    adj.approval_instance_id = inst.id
    adj.approved_at = bjt_now()
    if ok:
        # 审批通过: 更新订单total_amount为调价后金额
        o = db.query(Order).get(adj.order_id)
        if o:
            o.total_amount = adj.adjusted_amount


BIZ_HANDLERS: Dict[str, Any] = {
    "PURCHASE_REQUEST": lambda db, inst, ok: _set_status(db, PurchaseRequest, inst.biz_id, "APPROVED" if ok else "REJECTED", inst.id),
    "PROCUREMENT":      lambda db, inst, ok: _set_status(db, PurchaseRequest, inst.biz_id, "APPROVED" if ok else "REJECTED", inst.id),
    "CORE_PRODUCTION":  lambda db, inst, ok: _on_core_production_approved(db, inst, ok),
    "RECEIVING":        lambda db, inst, ok: _on_recv_approved(db, inst, ok),
    "COMPLETION":       lambda db, inst, ok: _set_status(db, Completion, inst.biz_id, "CONFIRMED" if ok else "REJECTED", inst.id),
    "EXPENSE":          lambda db, inst, ok: _set_status(db, ExpenseClaim, inst.biz_id, "APPROVED" if ok else "REJECTED", inst.id),
    "SALES_ADJUSTMENT": lambda db, inst, ok: _on_sales_adj_approved(db, inst, ok),
}


def _apply_approval_result(db: Session, inst: FlowInstance, approved: bool):
    h = BIZ_HANDLERS.get(inst.biz_type)
    if h:
        h(db, inst, approved)


# ============ 业务单据摘要(审批卡片展示) ============
def _biz_brief(db: Session, biz_type: str, biz_id: int) -> dict:
    """返回 {no, title, route} 供审批列表/催办展示"""
    try:
        if biz_type in ("PURCHASE_REQUEST", "PROCUREMENT"):
            o = db.query(PurchaseRequest).get(biz_id)
            return {"no": getattr(o, "pr_no", None) or f"#{biz_id}", "title": f"采购申请", "route": "purchase-requests"}
        if biz_type == "CORE_PRODUCTION":
            # 核心生产流 - 先尝试订单,再尝试采购申请
            from app.models.order import Order
            from app.models.customer import Customer
            o = db.query(Order).get(biz_id)
            if o:
                cust = db.query(Customer).filter(Customer.id == o.customer_id).first()
                return {
                    "no": getattr(o, "order_no", None) or f"#{biz_id}",
                    "title": f"订单 {cust.name if cust else ''}",
                    "route": "orders"
                }
            pr = db.query(PurchaseRequest).get(biz_id)
            if pr:
                return {
                    "no": getattr(pr, "pr_no", None) or f"#{biz_id}",
                    "title": f"采购申请",
                    "route": "purchase-requests"
                }
            return {"no": f"#{biz_id}", "title": biz_type, "route": ""}
        if biz_type == "RECEIVING":
            o = db.query(ReceivingLog).get(biz_id)
            return {"no": getattr(o, "log_no", None) or f"#{biz_id}", "title": f"来货登记 {getattr(o, 'part_name', '')}", "route": "receiving"}
        if biz_type == "COMPLETION":
            o = db.query(Completion).get(biz_id)
            return {"no": getattr(o, "completion_no", None) or f"#{biz_id}", "title": "完工单", "route": "completions"}
        if biz_type == "EXPENSE":
            o = db.query(ExpenseClaim).get(biz_id)
            return {"no": getattr(o, "claim_no", None) or f"#{biz_id}", "title": f"费用报销 ¥{float(getattr(o, 'amount', 0) or 0):.0f}", "route": "expense"}
        if biz_type == "SALES_ADJUSTMENT":
            o = db.query(SalesAdjustment).get(biz_id)
            return {"no": getattr(o, "adj_no", None) or f"#{biz_id}", "title": "调价申请", "route": "adjustments"}
    except Exception:
        pass
    return {"no": f"#{biz_id}", "title": biz_type, "route": ""}


# ============ 流程实例时间轴(核心:可视化用) ============
def _can_view_instance(db: Session, user: User, inst) -> bool:
    """判断当前用户是否有权限查看流程实例: 发起人/任一环节经办人/ADMIN/GM"""
    if not inst:
        return True
    rc = get_user_role_code(user, db)
    if rc in ("ADMIN", "GM"):
        return True
    if inst.initiator_user_id == user.id:
        return True
    # 环节经办人(包括审批过的和抄送相关)
    tasks = db.query(FlowTask).filter(FlowTask.instance_id == inst.id).all()
    for t in tasks:
        if t.assignee_user_id == user.id:
            return True
    return False


@router.get("/instances/{biz_type}/{biz_id}")
def get_instance(biz_type: str, biz_id: int,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """返回流程实例+全部节点(已完成/当前/未来),供前端画流转轨迹"""
    inst = db.query(FlowInstance).filter(
        FlowInstance.biz_type == biz_type, FlowInstance.biz_id == biz_id
    ).order_by(FlowInstance.id.desc()).first()
    if not inst:
        return Resp.ok({"instance": None, "nodes": []})
    if not _can_view_instance(db, user, inst):
        raise HTTPException(403, "无权查看该流程实例")
    fd = db.query(FlowDefinition).get(inst.definition_id)
    nodes_def = _parse_nodes(fd.nodes) if fd else []
    # 按id升序: reopen新一轮任务id更大, task_map同seq后者覆盖前者, 时间轴只展示最新一轮
    tasks = db.query(FlowTask).filter(
        FlowTask.instance_id == inst.id, FlowTask.status != "REVOKED"
    ).order_by(FlowTask.id.asc()).all()
    task_map = {t.node_seq: t for t in tasks}
    result = []
    for idx, nd in enumerate(nodes_def):
        seq = nd.get("seq", idx + 1)
        t = task_map.get(seq)
        node = {
            "seq": seq, "name": nd.get("name", f"节点{seq}"),
            "type": nd.get("type", "approve"), "role": nd.get("approver_role", ""),
            "cc_roles": nd.get("cc_roles", []),
            "form_config": nd.get("form_config", None),
            "status": "pending", "assignee_name": None, "assignee_id": None,
            "handled_at": None, "comment": None, "duration": None,
            "form_data": None,
        }
        if t and t.status in ("APPROVED", "REJECTED"):
            node["status"] = "done" if t.status == "APPROVED" else "rejected"
            node["assignee_id"] = t.assignee_user_id
            node["assignee_name"] = _user_name(db, t.assignee_user_id)
            node["handled_at"] = t.handled_at.isoformat() if t.handled_at else None
            node["comment"] = t.comment
            node["form_data"] = t.get_form_data() if t.form_data else None
        elif t and t.status == "PENDING" and inst.status == "RUNNING" and seq == inst.current_node_seq:
            node["status"] = "current"
            node["assignee_id"] = t.assignee_user_id
            node["assignee_name"] = _user_name(db, t.assignee_user_id)
            node["duration"] = _duration(t.created_at)
            node["form_data"] = t.get_form_data() if t.form_data else None
        result.append(node)
    return Resp.ok({
        "instance": {
            "id": inst.id, "status": inst.status,
            "initiator_id": inst.initiator_user_id,
            "started_at": inst.started_at.isoformat() if inst.started_at else None,
            "finished_at": inst.finished_at.isoformat() if inst.finished_at else None,
            "biz_data": inst.get_biz_data(),
        },
        "nodes": result,
    })


@router.post("/instances/{biz_type}/{biz_id}/reopen")
def reopen_instance(biz_type: str, biz_id: int, body: dict = None,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """驳回后重新发起: 仅发起人/ADMIN/GM可在REJECTED状态下重启原流程。
    历史任务保留不删除(留痕), 新一轮任务id更大, 时间轴按seq取最新一轮展示。"""
    inst = db.query(FlowInstance).filter(
        FlowInstance.biz_type == biz_type, FlowInstance.biz_id == biz_id
    ).order_by(FlowInstance.id.desc()).first()
    if not inst:
        raise HTTPException(404, "流程实例不存在")
    if inst.status != "REJECTED":
        raise HTTPException(400, "仅被驳回的流程可以重新发起")
    rc = get_user_role_code(user, db)
    if rc not in ("ADMIN", "GM") and inst.initiator_user_id != user.id:
        raise HTTPException(403, "仅发起人或管理员可重新发起")
    fd = db.query(FlowDefinition).get(inst.definition_id)
    if not fd or fd.status != "ACTIVE":
        raise HTTPException(400, "流程已停用, 无法重新发起")
    new_biz = (body or {}).get("biz_data")
    if new_biz:
        inst.set_biz_data({**(inst.get_biz_data() or {}), **new_biz})
    # 将驳回轮次遗留的PENDING任务作废(正常不会有, 防御), 已完成/已驳回任务保留作留痕
    db.query(FlowTask).filter(
        FlowTask.instance_id == inst.id, FlowTask.status == "PENDING"
    ).update({"status": "REVOKED"}, synchronize_session=False)
    inst.status = "RUNNING"
    inst.current_node_seq = 1
    inst.finished_at = None
    db.flush()
    _advance(db, inst, fd, inst.initiator_user_id)
    db.commit()
    log_audit(db, user, "reopen", "flow_instance", inst.id,
              before={"status": "REJECTED"}, after={"status": "RUNNING"})
    return Resp.ok({"instance_id": inst.id, "status": inst.status})


def _user_name(db: Session, uid: Optional[int]) -> Optional[str]:
    if not uid:
        return None
    u = db.query(User).get(uid)
    return u.name if u else None


def _duration(start: datetime) -> str:
    if not start:
        return ""
    d = bjt_now() - start
    if d < timedelta(minutes=1):
        return "刚刚"
    if d < timedelta(hours=1):
        return f"{int(d.total_seconds() // 60)}分钟"
    if d < timedelta(days=1):
        return f"{int(d.total_seconds() // 3600)}小时"
    return f"{d.days}天"


# ============ 待办任务列表 ============
@router.get("/tasks/pending")
def my_tasks(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """待办任务: 严格按账号隔离，所有人(含ADMIN/GM)只能看到分配给自己或本角色的任务"""
    role_code = get_user_role_code(user, db)
    q = db.query(FlowTask).filter(FlowTask.status == "PENDING")
    # 严格按账号隔离(含ADMIN/GM)：只看分配给自己或本角色的任务，与工作台待办口径一致
    q = q.filter(
        (FlowTask.assignee_user_id == user.id) | (FlowTask.role_id == (user.role_id or -1))
    )
    rows = q.all()
    data = []
    for t in rows:
        inst = db.query(FlowInstance).get(t.instance_id)
        brief = _biz_brief(db, inst.biz_type, inst.biz_id) if inst else {"no": "", "title": "", "route": ""}
        data.append({
            "id": t.id, "instance_id": t.instance_id, "node_seq": t.node_seq,
            "node_name": t.node_name, "status": t.status, "created_at": t.created_at.isoformat() if t.created_at else None,
            "biz_type": inst.biz_type if inst else "", "biz_id": inst.biz_id if inst else None,
            "biz_no": brief.get("no", ""), "biz_title": brief.get("title", ""), "route": brief.get("route", ""),
            "duration": _duration(t.created_at),
        })
    return {"code": 0, "data": data}


# ============ 调价申请 ============
class PriceAdjustIn(BaseModel):
    order_id: int
    type: str  # DECREASE/INCREASE
    method: str  # FIXED/PERCENT
    amount: float = 0
    percent: float = 0
    reason: str
    new_amount: float


@router.post("/price-adjustment")
def create_price_adjustment(body: PriceAdjustIn, user: User = Depends(require_role("SALES", "ADMIN", "GM")),
                            db: Session = Depends(get_db)):
    """销售发起调价申请 - 自动启动SALES_ADJUSTMENT审批流"""
    from app.models.order import Order
    from app.core.number_gen import generate_number
    
    # 验证订单
    order = db.query(Order).get(body.order_id)
    if not order:
        raise HTTPException(404, "订单不存在")
    if order.status != "EFFECTIVE":
        raise HTTPException(400, "只能对已生效的订单发起调价申请")

    # 防重复: 同一订单不能有PENDING状态的调价申请
    existing = db.query(SalesAdjustment).filter(
        SalesAdjustment.order_id == body.order_id,
        SalesAdjustment.status == "PENDING"
    ).first()
    if existing:
        raise HTTPException(400, "该订单已有待审批的调价申请，请等待审批完成")
    
    # 创建调价记录 - 自动生成编号
    from decimal import Decimal
    adj_no = generate_number("SALES_ADJUSTMENT", db=db)
    new_amount_dec = Decimal(str(body.new_amount))
    original_amount_dec = Decimal(str(order.total_amount))
    adj = SalesAdjustment(
        adj_no=adj_no,
        order_id=body.order_id,
        original_amount=original_amount_dec,
        adjusted_amount=new_amount_dec,
        diff_amount=new_amount_dec - original_amount_dec,
        reason=body.reason,
        status="PENDING",
        initiator_user_id=user.id,
    )
    db.add(adj)
    db.flush()
    
    # 查找SALES_ADJUSTMENT流程定义
    fd = db.query(FlowDefinition).filter(
        FlowDefinition.biz_type == "SALES_ADJUSTMENT",
        FlowDefinition.status == "ACTIVE"
    ).first()
    
    if fd:
        # 启动审批流
        inst = FlowInstance(
            definition_id=fd.id,
            biz_type="SALES_ADJUSTMENT",
            biz_id=adj.id,
            initiator_user_id=user.id,
            status="RUNNING",
            current_node_seq=1,
        )
        db.add(inst)
        db.flush()
        
        # 更新调价记录的审批实例ID
        adj.approval_instance_id = inst.id
        
        # 推进流程
        _advance(db, inst, fd, user.id)
        
        log_audit(db, user, "PRICE_ADJUSTMENT_CREATE", "sales_adjustment", adj.id,
                 f"订单{order.order_no}: {order.total_amount} → {body.new_amount}")
    else:
        # 没有流程定义，直接标记为待处理
        log_audit(db, user, "PRICE_ADJUSTMENT_CREATE", "sales_adjustment", adj.id,
                 f"订单{order.order_no}: {order.total_amount} → {body.new_amount}")
    
    db.commit()
    return Resp.ok({"id": adj.id, "adj_no": adj_no})


# ============ 调价申请列表 ============
@router.get("/price-adjustments")
def list_price_adjustments(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取调价申请列表"""
    from app.models.order import Order
    q = db.query(SalesAdjustment).order_by(SalesAdjustment.created_at.desc())
    # 非管理员只能看自己发起的
    rc = get_user_role_code(user, db)
    if rc != "ADMIN":
        q = q.filter(SalesAdjustment.initiator_user_id == user.id)
    items = []
    for adj in q.all():
        order = db.query(Order).get(adj.order_id)
        initiator = db.query(User).get(adj.initiator_user_id)
        items.append({
            "id": adj.id,
            "adj_no": adj.adj_no,
            "order_no": order.order_no if order else "-",
            "original_amount": float(adj.original_amount),
            "adjusted_amount": float(adj.adjusted_amount),
            "diff_amount": float(adj.diff_amount) if adj.diff_amount else 0,
            "reason": adj.reason,
            "status": adj.status,
            "initiator": initiator.name if initiator else "-",
            "created_at": _bjt_str(adj.created_at),
            "approved_at": _bjt_str(adj.approved_at),
        })
    return Resp.ok(items)


# ============ 编号规则（只读查看，无需管理员配置）============

@router.get("/number-rules")
def list_number_rules(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取所有编号规则（只读查看）"""
    from app.models.approval import NumberRule
    from app.core.number_gen import BIZ_TYPE_TO_PREFIX
    rules = db.query(NumberRule).order_by(NumberRule.biz_type).all()
    # 业务类型中文映射
    biz_type_labels = {
        "SALES_ADJUSTMENT": "调价申请",
        "EXPENSE": "费用报销",
        "PURCHASE_REQUEST": "采购申请",
        "PROCUREMENT": "采购单",
        "COMPLETION": "完工确认",
        "CORE_PRODUCTION": "核心生产",
        "PAYROLL": "工资单",
        "RECEIVING": "收货单",
    }
    result = []
    for r in rules:
        now = datetime.utcnow().strftime(r.date_format)
        result.append({
            "id": r.id,
            "biz_type": r.biz_type,
            "biz_type_label": biz_type_labels.get(r.biz_type, r.biz_type),
            "prefix": r.prefix,
            "seq_length": r.seq_length,
            "reset_cycle": r.reset_cycle,
            "date_format": r.date_format,
            "current_seq": r.current_seq,
            "next_number": f"{r.prefix}-{now}-{str(r.current_seq + 1).zfill(r.seq_length)}",
            "updated_at": _bjt_str(r.updated_at),
        })
    return Resp.ok(result)


# ============ 流程定义接口 ============

@router.get("/flow-definitions")
def list_flow_definitions(biz_type: str = None, user: User = Depends(get_current_user), 
                          db: Session = Depends(get_db)):
    """获取流程定义列表"""
    from app.models.approval import FlowDefinition
    query = db.query(FlowDefinition)
    if biz_type:
        query = query.filter(FlowDefinition.biz_type == biz_type)
    definitions = query.filter(FlowDefinition.status == "ACTIVE").order_by(FlowDefinition.id).all()
    return Resp.ok([{
        "id": d.id,
        "name": d.name,
        "biz_type": d.biz_type,
        "nodes": d.nodes,
        "version": d.version,
        "created_at": _bjt_str(d.created_at),
    } for d in definitions])
