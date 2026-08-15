"""工作流引擎 - 流程定义/实例/任务,支持审批(approve)与流转(process)节点,表驱动可配"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role, get_user_role_code
from app.core.audit import log_audit
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
def create_def(body: FlowDefIn, user: User = Depends(require_role("ADMIN", "GM")),
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
def list_defs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(FlowDefinition).filter(FlowDefinition.status == "ACTIVE").all()
    result = []
    for f in rows:
        result.append({"id": f.id, "name": f.name, "biz_type": f.biz_type,
                       "nodes": _parse_nodes(f.nodes), "version": f.version})
    return {"code": 0, "data": result}


@router.put("/definitions/{fid}")
def update_def(fid: int, body: FlowDefIn, user: User = Depends(require_role("ADMIN", "GM")),
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
def delete_def(fid: int, user: User = Depends(require_role("ADMIN", "GM")),
               db: Session = Depends(get_db)):
    fd = db.query(FlowDefinition).get(fid)
    if not fd:
        raise HTTPException(404, "流程定义不存在")
    fd.status = "INACTIVE"
    db.commit()
    return Resp.ok({"id": fid})


# ============ 节点分配工具 ============
def _find_assignee(db: Session, role_code: str, initiator_id: int = None) -> tuple:
    """按角色码找用户,SUBMITTER返回发起人自己"""
    if not role_code:
        return (None, None)
    if role_code == "SUBMITTER":
        return (initiator_id, None)
    role = db.query(Role).filter(Role.code == role_code).first()
    if not role:
        return (None, None)
    u = db.query(User).filter(User.role_id == role.id, User.status == "ACTIVE").first()
    return (u.id if u else None, role.id)


def _node_done(db: Session, inst: FlowInstance, node: dict, initiator_id: int, comment: str = "流转自动推进"):
    """标记一个 process 节点为自动完成"""
    db.add(FlowTask(
        instance_id=inst.id, node_seq=node.get("seq", 1), node_name=node.get("name"),
        assignee_user_id=initiator_id, status="APPROVED", handled_at=datetime.utcnow(),
        comment=comment,
    ))


def _advance(db: Session, inst: FlowInstance, fd: FlowDefinition, initiator_id: int):
    """推进流程:跳过 start/end/process/cc 节点(自动完成),到 approve 节点创建待办;全走完则终态"""
    nodes = _parse_nodes(fd.nodes)
    while inst.current_node_seq <= len(nodes):
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
        elif ntype in ("approve", "item"):
            assignee_id, role_id = _find_assignee(db, node.get("approver_role"), initiator_id)
            db.add(FlowTask(
                instance_id=inst.id, node_seq=node.get("seq", inst.current_node_seq),
                node_name=node.get("name"), assignee_user_id=assignee_id,
                role_id=role_id, status="PENDING",
            ))
            return
        else:
            inst.current_node_seq += 1
    inst.status = "APPROVED"
    inst.finished_at = datetime.utcnow()
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
        users = db.query(User).join(Role, User.role_id == Role.id).filter(
            Role.code == role_code, User.status == "ACTIVE"
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
def start_flow(db: Session, biz_type: str, biz_id: int, initiator: User) -> Optional[FlowInstance]:
    fd = db.query(FlowDefinition).filter(
        FlowDefinition.biz_type == biz_type, FlowDefinition.status == "ACTIVE"
    ).order_by(FlowDefinition.version.desc()).first()
    if not fd:
        return None
    inst = FlowInstance(
        definition_id=fd.id, biz_type=biz_type, biz_id=biz_id,
        status="RUNNING", current_node_seq=1, initiator_user_id=initiator.id,
    )
    db.add(inst)
    db.flush()
    _advance(db, inst, fd, initiator.id)
    db.flush()
    return inst


# ============ 处理任务 ============
@router.post("/tasks/{tid}/handle")
def handle_task(tid: int, body: dict, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    t = db.query(FlowTask).get(tid)
    if not t:
        raise HTTPException(404, "任务不存在")
    if t.status != "PENDING":
        raise HTTPException(400, "任务已处理")
    action = body.get("action")
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action必须approve/reject")
    inst = db.query(FlowInstance).get(t.instance_id)
    before = t.status
    t.status = "APPROVED" if action == "approve" else "REJECTED"
    t.comment = body.get("comment", "")
    t.handled_at = datetime.utcnow()
    log_audit(db, user, "approve" if action == "approve" else "reject", "flow_task", tid, before=before, after=t.status)
    db.flush()
    if action == "reject":
        inst.status = "REJECTED"
        inst.finished_at = datetime.utcnow()
        _apply_approval_result(db, inst, False)
    else:
        fd = db.query(FlowDefinition).get(inst.definition_id)
        inst.current_node_seq = t.node_seq + 1
        _advance(db, inst, fd, inst.initiator_user_id)
    db.commit()
    return Resp.ok({"task_id": tid, "instance_status": inst.status})


@router.post("/tasks/{tid}/transfer")
def transfer_task(tid: int, body: dict, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """转交任务"""
    t = db.query(FlowTask).get(tid)
    if not t or t.status != "PENDING":
        raise HTTPException(400, "任务不可转交")
    to_uid = body.get("to_user_id")
    to_user = db.query(User).get(to_uid) if to_uid else None
    if not to_user:
        raise HTTPException(400, "目标用户不存在")
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


BIZ_HANDLERS: Dict[str, Any] = {
    "PURCHASE_REQUEST": lambda db, inst, ok: _set_status(db, PurchaseRequest, inst.biz_id, "APPROVED" if ok else "REJECTED", inst.id),
    "PROCUREMENT":      lambda db, inst, ok: _set_status(db, PurchaseRequest, inst.biz_id, "APPROVED" if ok else "REJECTED", inst.id),
    "CORE_PRODUCTION":  lambda db, inst, ok: _set_status(db, PurchaseRequest, inst.biz_id, "APPROVED" if ok else "REJECTED", inst.id),
    "RECEIVING":        lambda db, inst, ok: _on_recv_approved(db, inst, ok),
    "COMPLETION":       lambda db, inst, ok: _set_status(db, Completion, inst.biz_id, "CONFIRMED" if ok else "REJECTED", inst.id),
    "EXPENSE":          lambda db, inst, ok: _set_status(db, ExpenseClaim, inst.biz_id, "APPROVED" if ok else "REJECTED", inst.id),
    "SALES_ADJUSTMENT": lambda db, inst, ok: _set_status(db, SalesAdjustment, inst.biz_id, "APPROVED" if ok else "REJECTED", inst.id),
}


def _apply_approval_result(db: Session, inst: FlowInstance, approved: bool):
    h = BIZ_HANDLERS.get(inst.biz_type)
    if h:
        h(db, inst, approved)


# ============ 业务单据摘要(审批卡片展示) ============
def _biz_brief(db: Session, biz_type: str, biz_id: int) -> dict:
    """返回 {no, title, route} 供审批列表/催办展示"""
    try:
        if biz_type in ("PURCHASE_REQUEST", "PROCUREMENT", "CORE_PRODUCTION"):
            o = db.query(PurchaseRequest).get(biz_id)
            return {"no": getattr(o, "pr_no", None) or f"#{biz_id}", "title": f"采购申请", "route": "pr"}
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
@router.get("/instances/{biz_type}/{biz_id}")
def get_instance(biz_type: str, biz_id: int,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """返回流程实例+全部节点(已完成/当前/未来),供前端画流转轨迹"""
    inst = db.query(FlowInstance).filter(
        FlowInstance.biz_type == biz_type, FlowInstance.biz_id == biz_id
    ).order_by(FlowInstance.id.desc()).first()
    if not inst:
        return Resp.ok({"instance": None, "nodes": []})
    fd = db.query(FlowDefinition).get(inst.definition_id)
    nodes_def = _parse_nodes(fd.nodes) if fd else []
    tasks = db.query(FlowTask).filter(FlowTask.instance_id == inst.id).all()
    task_map = {t.node_seq: t for t in tasks}
    result = []
    for idx, nd in enumerate(nodes_def):
        seq = nd.get("seq", idx + 1)
        t = task_map.get(seq)
        node = {
            "seq": seq, "name": nd.get("name", f"节点{seq}"),
            "type": nd.get("type", "approve"), "role": nd.get("approver_role", ""),
            "cc_roles": nd.get("cc_roles", []),
            "status": "pending", "assignee_name": None, "assignee_id": None,
            "handled_at": None, "comment": None, "duration": None,
        }
        if t and t.status in ("APPROVED", "REJECTED"):
            node["status"] = "done" if t.status == "APPROVED" else "rejected"
            node["assignee_id"] = t.assignee_user_id
            node["assignee_name"] = _user_name(db, t.assignee_user_id)
            node["handled_at"] = t.handled_at.isoformat() if t.handled_at else None
            node["comment"] = t.comment
        elif t and t.status == "PENDING" and inst.status == "RUNNING" and seq == inst.current_node_seq:
            node["status"] = "current"
            node["assignee_id"] = t.assignee_user_id
            node["assignee_name"] = _user_name(db, t.assignee_user_id)
            node["duration"] = _duration(t.created_at)
        result.append(node)
    return Resp.ok({
        "instance": {"id": inst.id, "status": inst.status,
                     "initiator_id": inst.initiator_user_id,
                     "started_at": inst.started_at.isoformat() if inst.started_at else None,
                     "finished_at": inst.finished_at.isoformat() if inst.finished_at else None},
        "nodes": result,
    })


def _user_name(db: Session, uid: Optional[int]) -> Optional[str]:
    if not uid:
        return None
    u = db.query(User).get(uid)
    return u.name if u else None


def _duration(start: datetime) -> str:
    if not start:
        return ""
    d = datetime.utcnow() - start
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
    role_code = get_user_role_code(user, db)
    q = db.query(FlowTask).filter(FlowTask.status == "PENDING")
    # ADMIN可看全部待办(管理视角),其他角色只看分配给自己的
    if role_code != "ADMIN":
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
