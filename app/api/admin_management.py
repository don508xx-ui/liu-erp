"""管理员: 用户+角色管理 (仅ADMIN角色)
角色/用户 增删改均考虑所有关联引用, 不使用重置/清库等粗暴手段:
- 删除角色 = 停用/合并到目标角色, 同步迁移关联(用户/待办/流程定义节点), 历史完成数据保留审计
- 删除用户 = 软删除(停用), 保留审计与历史引用; 有待办时禁止
- 修改用户角色 = 同步该用户未处理待办的 role_id
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, object_session
from pydantic import BaseModel
from typing import Optional, List
import json

from app.core.db import get_db
from app.core.auth import get_current_user, hash_password
from app.core.audit import log_audit
from app.models.system import User, Role, RoleAlias
from app.models.approval import FlowTask, FlowDefinition
from app.schemas import Resp, PageResp

router = APIRouter(prefix="/api/admin", tags=["admin"])

# 角色页面权限保守默认值 (与main.py保持一致; 唯一真源)
ROLE_PAGES_DEFAULT = {
    "ADMIN": "*",
    "GM": "*",
    "SALES": ["dashboard","workflow-list","orders","approvals","customers","my-todos","my-done","sales-adjustments"],
    "FINANCE": ["dashboard","workflow-list","finance","approvals","my-todos","my-done","expense","payroll","receivables","purchases","vouchers","reports","accounts","ai-finance","acceptances"],
    "MANAGER": ["dashboard","workflow-list","work-orders","inventory","my-todos","my-done","completions","screen"],
    "OPERATION": ["dashboard","workflow-list","work-orders","inventory","my-todos","my-done","stock-moves","purchases","purchase-requests","approvals","completions"],
    "DEPARTMENT_HEAD": ["dashboard","workflow-list","approvals","my-todos","my-done","expense","purchase-requests"],
}

def _resolve_pages(role_code: str, role_pages, user_pages=None):
    """统一权限解析: 用户级 > 角色级(含DB空值回退保守默认) > 最后仅留dashboard。
    永不返回None或空list触发外部fallback导致权限泄露。"""
    if user_pages:
        return user_pages
    if role_pages:  # truthy: non-empty list 或 "*"
        return role_pages
    fb = ROLE_PAGES_DEFAULT.get(role_code)
    if fb:
        return fb
    return ["dashboard"]


def _admin_only(user: User) -> None:
    """管理员API共用: 仅ADMIN或GM可访问(GM为超级管理员)"""
    if not user.role_id:
        raise HTTPException(403, "无角色")
    sess = object_session(user)
    role = sess.query(Role).filter(Role.id == user.role_id).first() if sess else None
    if not role:
        raise HTTPException(403, "角色不存在")
    if role.code not in ("ADMIN", "GM"):
        raise HTTPException(403, "仅系统管理员可操作")


# ========== 角色管理 ==========

@router.get("/roles")
def list_roles(db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    _admin_only(user)
    roles = db.query(Role).order_by(Role.id.asc()).all()
    return Resp.ok([{
        "id": r.id, "code": r.code, "name": r.name,
        "description": getattr(r, "description", None) or "",
        "pages": getattr(r, "pages", None) or [],
        "status": getattr(r, "status", None) or "ACTIVE",
    } for r in roles])


# 所有可用页面清单(供权限编辑器使用)
ALL_PAGES = [
    {"key": "dashboard", "label": "工作台", "group": "核心"},
    {"key": "my-todos", "label": "我的待办", "group": "核心"},
    {"key": "my-done", "label": "我的已办", "group": "核心"},
    {"key": "workflow-list", "label": "业务流程列表", "group": "核心"},
    {"key": "approvals", "label": "审批中心", "group": "核心"},
    {"key": "analysis", "label": "经营分析", "group": "核心"},
    {"key": "orders", "label": "销售订单", "group": "销售"},
    {"key": "customers", "label": "客户档案", "group": "销售"},
    {"key": "sales-adjustments", "label": "调价申请", "group": "销售"},
    {"key": "sample-request", "label": "打样申请", "group": "销售"},
    {"key": "inventory", "label": "库存查询", "group": "仓储"},
    {"key": "stock-moves", "label": "出入库流水", "group": "仓储"},
    {"key": "purchases", "label": "采购订单", "group": "采购"},
    {"key": "purchase-requests", "label": "采购申请", "group": "采购"},
    {"key": "pr", "label": "采购单", "group": "采购"},
    {"key": "work-orders", "label": "加工工单", "group": "生产"},
    {"key": "completions", "label": "完工单", "group": "生产"},
    {"key": "requisitions", "label": "领料出库", "group": "生产"},
    {"key": "finance", "label": "财务单据", "group": "财务"},
    {"key": "receivables", "label": "应收管理", "group": "财务"},
    {"key": "payroll", "label": "工资管理", "group": "财务"},
    {"key": "expense", "label": "费用报销", "group": "财务"},
    {"key": "vouchers", "label": "凭证管理", "group": "财务"},
    {"key": "reports", "label": "财务报表", "group": "财务"},
    {"key": "accounts", "label": "会计科目", "group": "财务"},
    {"key": "acceptances", "label": "承兑汇票", "group": "财务"},
    {"key": "ai-analysis", "label": "AI经营分析", "group": "分析"},
    {"key": "screen", "label": "车间大屏", "group": "其他"},
    {"key": "flow-design", "label": "流程设计", "group": "管理"},
    {"key": "users", "label": "用户管理", "group": "管理"},
    {"key": "roles", "label": "角色权限", "group": "管理"},
    {"key": "number-rules", "label": "编号规则", "group": "管理"},
]


@router.get("/page-catalog")
def get_page_catalog(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """所有页面目录(供权限编辑器使用, 仅管理员可访问)"""
    _admin_only(user, db)
    return Resp.ok(ALL_PAGES)


@router.get("/roles/{code}/pages")
def get_role_pages(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取指定角色的页面权限(需登录, 防止未授权枚举权限结构)"""
    r = db.query(Role).filter(Role.code == code.upper()).first()
    if not r:
        return Resp.ok([])
    return Resp.ok(getattr(r, "pages", None) or [])


@router.get("/my-pages")
def get_my_pages(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取当前用户的页面权限: 优先用户级pages, 为空则回退角色级pages, 再空回退保守默认。
    永不返回空数组 — 避免前端fallback到硬编码导致权限泄露。"""
    role = db.query(Role).filter(Role.id == user.role_id).first() if user.role_id else None
    rc = role.code if role else ""
    user_pages = getattr(user, "pages", None)
    role_pages = getattr(role, "pages", None) if role else None
    resolved = _resolve_pages(rc, role_pages, user_pages)
    # "*" 展开返回 (前端处理 "*" 也可以, 但数组更直观)
    if resolved == "*" or (isinstance(resolved, list) and "*" in resolved):
        return Resp.ok("*")
    return Resp.ok(resolved) if isinstance(resolved, list) else Resp.ok([resolved])


class RoleCreate(BaseModel):
    code: str
    name: str
    description: Optional[str] = ""


@router.post("/roles")
def create_role(body: RoleCreate,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    _admin_only(user)
    if db.query(Role).filter(Role.code == body.code.upper()).first():
        raise HTTPException(400, f"角色编码 {body.code} 已存在")
    r = Role(code=body.code.upper(), name=body.name, description=body.description)
    db.add(r); db.commit(); db.refresh(r)
    return Resp.ok({"id": r.id, "code": r.code, "name": r.name})


class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    pages: Optional[List[str]] = None


@router.put("/roles/{rid}")
def update_role(rid: int, body: RoleUpdate,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    _admin_only(user)
    r = db.query(Role).filter(Role.id == rid).first()
    if not r: raise HTTPException(404, "角色不存在")
    if body.name is not None: r.name = body.name
    if body.description is not None: r.description = body.description
    if body.pages is not None: r.pages = body.pages
    db.commit()
    return Resp.ok({"id": r.id})


def _parse_nodes(raw):
    """解析流程定义nodes(兼容str/list)"""
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw else []
        except Exception:
            return []
    return raw or []


def _role_refs(db: Session, r: Role) -> dict:
    """统计角色所有关联引用: 用户/未处理待办/启用的流程定义节点"""
    users = db.query(User).filter(User.role_id == r.id).count()
    pending_tasks = db.query(FlowTask).filter(
        FlowTask.role_id == r.id, FlowTask.status == "PENDING"
    ).count()
    flow_defs = 0
    for f in db.query(FlowDefinition).filter(FlowDefinition.status == "ACTIVE").all():
        for n in _parse_nodes(f.nodes):
            if n.get("approver_role") == r.code or r.code in (n.get("cc_roles") or []):
                flow_defs += 1
                break
    return {"users": users, "pending_tasks": pending_tasks, "flow_defs": flow_defs,
            "total": users + pending_tasks + flow_defs}


@router.get("/roles/{rid}/refs")
def get_role_refs(rid: int, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """删除前查询角色的关联引用, 供前端展示合并确认"""
    _admin_only(user)
    r = db.query(Role).filter(Role.id == rid).first()
    if not r:
        raise HTTPException(404, "角色不存在")
    return Resp.ok(_role_refs(db, r))


@router.delete("/roles/{rid}")
def delete_role(rid: int, merge_to: Optional[str] = None,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    """删除角色。无引用直接删; 有引用必须指定 merge_to 合并到目标角色,
    并同步迁移 用户/未处理待办/流程定义节点, 历史已完成数据保留原角色(审计)。"""
    _admin_only(user)
    r = db.query(Role).filter(Role.id == rid).first()
    if not r:
        raise HTTPException(404, "角色不存在")
    if getattr(r, "code", "") in ("ADMIN", "GM"):
        raise HTTPException(400, "系统保留角色不可删除")
    refs = _role_refs(db, r)

    # 无任何引用: 直接物理删除
    if refs["total"] == 0:
        db.delete(r)
        db.commit()
        log_audit(db, user, "delete_role", "role", rid, before=r.code)
        return Resp.ok({"merged": False})

    # 有引用必须合并
    if not merge_to:
        raise HTTPException(
            400,
            f"该角色仍有 {refs['total']} 处关联(用户{refs['users']}/待办{refs['pending_tasks']}/流程{refs['flow_defs']}), "
            f"请先指定合并目标角色"
        )
    target = db.query(Role).filter(
        Role.code == merge_to.upper(), Role.status == "ACTIVE"
    ).first()
    if not target or target.id == r.id:
        raise HTTPException(400, "合并目标角色不存在或无效")
    if getattr(target, "code", "") == "ADMIN":
        raise HTTPException(400, "不能合并到系统管理员角色")

    # 1. 用户归属迁移
    db.query(User).filter(User.role_id == r.id).update({"role_id": target.id})
    # 2. 未处理待办的 role_id 迁移(历史已完成任务保留原role_id=审计快照)
    db.query(FlowTask).filter(
        FlowTask.role_id == r.id, FlowTask.status == "PENDING"
    ).update({"role_id": target.id})
    # 3. 启用流程定义节点的 approver_role/cc_roles 引用替换
    #    注意: 必须构造新对象赋值, 直接改原list引用SQLAlchemy不会识别为变化而不落库
    import copy
    for f in db.query(FlowDefinition).filter(FlowDefinition.status == "ACTIVE").all():
        nodes = copy.deepcopy(_parse_nodes(f.nodes))
        changed = False
        for n in nodes:
            if n.get("approver_role") == r.code:
                n["approver_role"] = target.code
                changed = True
            if r.code in (n.get("cc_roles") or []):
                n["cc_roles"] = [target.code if c == r.code else c for c in n.get("cc_roles")]
                changed = True
        if changed:
            f.nodes = nodes
    # 4. 角色合并映射(兜底: 老流程定义/历史实例运行时按别名归一化到目标角色)
    if not db.query(RoleAlias).filter(RoleAlias.alias_code == r.code).first():
        db.add(RoleAlias(alias_code=r.code, target_code=target.code))
    db.delete(r)
    db.commit()
    log_audit(db, user, "merge_role", "role", rid,
              before={"code": r.code, "refs": refs}, after={"target": target.code})
    return Resp.ok({"merged": True, "target": target.code})


# ========== 用户管理 ==========

@router.get("/users")
def list_users(page: int = 1, size: int = 50, keyword: str = "",
               role_id: Optional[int] = None,
               db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    _admin_only(user)
    q = db.query(User)
    if keyword:
        q = q.filter((User.username.like(f"%{keyword}%")) | (User.name.like(f"%{keyword}%")))
    if role_id:
        q = q.filter(User.role_id == role_id)
    total = q.count()
    rows = q.order_by(User.id.desc()).offset((page - 1) * size).limit(size).all()
    role_map = {r.id: {"id": r.id, "code": r.code, "name": r.name} for r in db.query(Role).all()}
    return PageResp(
        total=total,
        data=[{
            "id": u.id,
            "username": u.username,
            "real_name": getattr(u, "real_name", None) or u.name or "",
            "name": u.name or "",
            "role": role_map.get(u.role_id),
            "status": u.status or "ACTIVE",
            "pages": getattr(u, "pages", None) or [],
            "created_at": u.created_at.isoformat() if getattr(u, "created_at", None) else "",
        } for u in rows]
    )


class UserCreate(BaseModel):
    username: str
    real_name: str
    role_id: int
    password: str = "123456"
    status: Optional[str] = "ACTIVE"


@router.post("/users")
def create_user(body: UserCreate,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    _admin_only(user)
    if len(body.username) < 2: raise HTTPException(400, "账号至少2位")
    if len(body.password) < 4: raise HTTPException(400, "密码至少4位")
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(400, f"账号 {body.username} 已存在")
    role = db.query(Role).filter(Role.id == body.role_id, Role.status == "ACTIVE").first()
    if not role: raise HTTPException(400, "所选角色不存在或已停用")
    u = User(
        username=body.username,
        password_hash=hash_password(body.password),
        name=body.real_name,
        role_id=body.role_id,
        status=body.status or "ACTIVE",
    )
    db.add(u); db.commit(); db.refresh(u)
    return Resp.ok({"id": u.id, "username": u.username})


class UserUpdate(BaseModel):
    real_name: Optional[str] = None
    role_id: Optional[int] = None
    status: Optional[str] = None
    password: Optional[str] = None
    pages: Optional[List[str]] = None


@router.put("/users/{uid}")
def update_user(uid: int, body: UserUpdate,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    _admin_only(user)
    u = db.query(User).filter(User.id == uid).first()
    if not u: raise HTTPException(404, "用户不存在")
    if body.real_name is not None: u.name = body.real_name
    if body.role_id is not None:
        new_role = db.query(Role).filter(Role.id == body.role_id, Role.status == "ACTIVE").first()
        if not new_role:
            raise HTTPException(400, "角色不存在或已停用")
        if new_role.id != u.role_id:
            before_role = u.role_id
            u.role_id = new_role.id
            moved = db.query(FlowTask).filter(
                FlowTask.assignee_user_id == u.id,
                FlowTask.status == "PENDING",
            ).update({"role_id": new_role.id})
            log_audit(db, user, "change_user_role", "user", uid,
                      before={"role_id": before_role}, after={"role_id": new_role.id, "pending_tasks_moved": moved})
    if body.status is not None: u.status = body.status
    if body.password:
        if len(body.password) < 4: raise HTTPException(400, "密码至少4位")
        u.password_hash = hash_password(body.password)
    if body.pages is not None: u.pages = body.pages
    db.commit()
    return Resp.ok({"id": u.id})


@router.delete("/users/{uid}")
def delete_user(uid: int,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    """删除用户 = 软删除(停用), 保留审计与历史单据引用。
    名下有待办时禁止删除, 需先转交。"""
    _admin_only(user)
    if uid == user.id:
        raise HTTPException(400, "不能删除自己")
    u = db.query(User).filter(User.id == uid).first()
    if not u: raise HTTPException(404, "用户不存在")
    pending = db.query(FlowTask).filter(
        FlowTask.assignee_user_id == u.id, FlowTask.status == "PENDING"
    ).count()
    if pending > 0:
        raise HTTPException(400, f"该用户仍有 {pending} 条待办任务, 请先转交后再停用")
    u.status = "DISABLED"
    db.commit()
    log_audit(db, user, "disable_user", "user", uid, before="ACTIVE", after="DISABLED")
    return Resp.ok({"status": "DISABLED"})
