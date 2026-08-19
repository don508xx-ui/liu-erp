from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.models.system import User, Role, RoleAlias, Permission, RolePermission
from app.core.db import get_db
from app.core.auth import get_current_user


# 角色 → 数据scope 映射(订单可见性矩阵)
# orders: own(仅自己) / all_read(全可见只读) / all(全可见) / read(只读全可见)
ROLE_SCOPE = {
    "SALES": {"orders": "own", "customers": "own", "opportunities": "own", "contracts": "own"},
    "OPERATION": {"orders": "all_read", "work_orders": "all", "inventory": "all", "requisitions": "all", "completions": "all"},
    "FINANCE": {"orders": "all_read", "finance": "all", "purchases": "read", "payroll": "all", "contracts": "all_read"},
    "GM": {"*": "read"},  # 总经理全只读
    "MANAGER": {"work_orders": "own_workshop"},  # 车间厂长
    "AGENT": {"alert_rules": "all", "report_templates": "all", "analysis": "read"},
}


def require_role(*roles):
    def dep(user: User = Depends(get_current_user)):
        if not user.role_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无角色")
        from sqlalchemy.orm import object_session
        sess = object_session(user)
        role = sess.query(Role).filter(Role.id == user.role_id).first()
        if not role:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "角色不存在")
        if role.code in ("ADMIN", "GM"):  # 超管/总经理
            return user
        if role.code not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"需要角色:{roles}")
        return user
    return dep


def require_permission(perm_code: str):
    def dep(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        if not user.role_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无角色")
        role = db.query(Role).filter(Role.id == user.role_id).first()
        if not role:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "角色不存在")
        if role.code in ("ADMIN", "GM"):  # 超管/总经理放行所有权限
            return user
        rp = db.query(RolePermission).join(Permission).filter(
            RolePermission.role_id == role.id,
            Permission.code == perm_code,
        ).first()
        if not rp:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"无权限:{perm_code}")
        return user
    return dep


def get_user_role_code(user: User, db: Session) -> str:
    if not user.role_id:
        return ""
    role = db.query(Role).filter(Role.id == user.role_id).first()
    return role.code if role else ""


def resolve_role_ids(db: Session, role_id) -> list:
    """角色归一化: 返回该角色id + 所有通过 role_aliases 合并到它的旧角色id。
    历史待办里 role_id 可能是旧角色, 查询时一并纳入, 保证角色合并后老待办不丢失。"""
    ids = {role_id}
    role = db.query(Role).filter(Role.id == role_id).first() if role_id else None
    if role:
        for a in db.query(RoleAlias).filter(RoleAlias.target_code == role.code).all():
            old = db.query(Role).filter(Role.code == a.alias_code).first()
            if old:
                ids.add(old.id)
    return list(ids)


def resolve_role_by_code(db: Session, role_code: str):
    """按角色code取角色; 若该角色已停用/不存在, 通过 role_aliases 归一化到目标角色。"""
    role = db.query(Role).filter(Role.code == role_code, Role.status == "ACTIVE").first()
    if role:
        return role
    a = db.query(RoleAlias).filter(RoleAlias.alias_code == role_code).first()
    if a:
        return db.query(Role).filter(Role.code == a.target_code, Role.status == "ACTIVE").first()
    return None


def get_user_scope(user: User, db: Session) -> dict:
    code = get_user_role_code(user, db)
    return ROLE_SCOPE.get(code, {})


def can_see_customer_name(user: User, db: Session, customer) -> bool:
    """客户名脱敏判定:ADMIN/GM/owner可见真名,其他角色见简称"""
    code = get_user_role_code(user, db)
    if code in ("ADMIN", "GM"):
        return True
    # owner销售可见自己客户
    owner_id = getattr(customer, "owner_user_id", None) or getattr(customer, "sales_user_id", None)
    if owner_id == user.id:
        return True
    return False


def mask_customer(user: User, db: Session, customer) -> dict:
    """返回脱敏后的客户字段:不可见真名时name=short_code"""
    show = can_see_customer_name(user, db, customer)
    return {
        "id": customer.id, "code": customer.code,
        "name": customer.name if show else (customer.short_code or customer.code),
        "real_name": customer.name if show else None,
        "short_code": getattr(customer, "short_code", None),
        "tax_no": customer.tax_no if show else None,
        "address": customer.address if show else None,
        "contact_name": customer.contact_name if show else None,
        "contact_phone": customer.contact_phone if show else None,
        "industry": customer.industry, "settlement_cycle": customer.settlement_cycle,
        "bank_name": customer.bank_name if show else None,
        "bank_account": customer.bank_account if show else None,
        "default_company_id": getattr(customer, "default_company_id", None),
        "status": customer.status, "remark": customer.remark if show else None,
    }


def apply_scope_filter(user: User, db: Session, query, module: str):
    """按角色数据范围过滤查询。返回过滤后的query。"""
    role = db.query(Role).filter(Role.id == user.role_id).first() if user.role_id else None
    if not role or role.code in ("ADMIN", "GM"):
        return query
    scope = ROLE_SCOPE.get(role.code, {})
    rule = scope.get(module, scope.get("*", None))
    if rule is None:
        return query  # 无限制
    if rule == "read":
        return query  # 只读但可看全部
    if rule == "own":
        # 只能看自己创建的
        ent = query.column_descriptions[0]['entity']
        if hasattr(ent, 'sales_user_id'):
            return query.filter(ent.sales_user_id == user.id)
        if hasattr(ent, 'owner_user_id'):
            return query.filter(ent.owner_user_id == user.id)
        if hasattr(ent, 'operator_user_id'):
            return query.filter(ent.operator_user_id == user.id)
        return query
    if rule == "own_workshop":
        if hasattr(query.column_descriptions[0]['entity'], 'workshop'):
            return query.filter(query.column_descriptions[0]['entity'].workshop == user.name)
        return query
    if rule == "own_orders":
        # 财务查看自己销售的订单
        from app.models.order import Order
        return query.filter(query.column_descriptions[0]['entity'].related_id.in_(
            db.query(Order.id).filter(Order.sales_user_id == user.id)
        ))
    if rule == "all":
        return query
    return query
