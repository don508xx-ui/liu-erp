"""月度盘点 - 账面→实盘→封账(自动调账流水+凭证)"""
from typing import Optional, List
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role
from app.core.audit import log_audit
from app.api.approvals import bjt_now
from app.models.system import User
from app.models.inventory import InventoryItem, InventoryTxn
from app.models.stock_check import StockCheck
from app.schemas import Resp

router = APIRouter(prefix="/api/stock-check", tags=["stock-check"])


def _ledger(db: Session, code: str, name: str, type_: str, direction: str):
    from app.models.finance import Account
    acc = db.query(Account).filter(Account.code == code).first()
    if not acc:
        acc = Account(code=code, name=name, type=type_, direction=direction,
                      is_required=1, level=1, status="ACTIVE")
        db.add(acc)
        db.flush()
    return acc


def _post_voucher(db: Session, odate, summary: str, entries: list, user_id: int):
    from app.core.voucher_service import create_voucher, post_voucher
    v = create_voucher(db, {
        "period": odate.strftime("%Y-%m"), "voucher_date": odate,
        "summary": summary, "entries": entries,
    }, creator_id=user_id)
    post_voucher(db, v.id)
    return v


def _stock_account(db: Session, category: str):
    """按物料类别返回库存科目:原材料/库存商品"""
    if category == "FINISHED_GOOD":
        return _ledger(db, "1405", "库存商品", "ASSET", "DEBIT")
    return _ledger(db, "1403", "原材料", "ASSET", "DEBIT")


@router.post("")
def create_check(user: User = Depends(require_role("MANAGER", "OPERATION", "FINANCE", "ADMIN")),
                 db: Session = Depends(get_db)):
    """创建盘点单: 拉所有 InventoryItem 当前库存作为账面 → DRAFT"""
    items_q = db.query(InventoryItem).filter(InventoryItem.status == "ACTIVE").all()
    now = bjt_now()
    period = now.strftime("%Y-%m")
    seq = db.query(StockCheck).filter(StockCheck.period == period).count() + 1
    items = [{
        "item_id": it.id, "item_name": it.name,
        "spec": it.spec, "unit": it.unit, "category": it.category,
        "unit_cost": float(it.unit_cost or 0),
        "book_qty": float(it.stock_qty or 0),
        "actual_qty": None, "diff_qty": None, "remark": "",
    } for it in items_q]
    sc = StockCheck(
        check_no=f"SC-{now.strftime('%Y%m')}-{seq:03d}",
        period=period, check_date=now, status="DRAFT",
        operator_user_id=user.id, operator_name=user.name,
        items=items, total_diff_amount=0,
    )
    db.add(sc)
    db.flush()
    log_audit(db, user, "create", "stock_check", sc.id, after={"check_no": sc.check_no})
    db.commit()
    return Resp.ok({"id": sc.id, "check_no": sc.check_no})


@router.get("")
def list_checks(user: User = Depends(require_role("MANAGER", "OPERATION", "FINANCE", "ADMIN")),
                db: Session = Depends(get_db)):
    rows = db.query(StockCheck).order_by(StockCheck.id.desc()).all()
    return {"code": 0, "data": [{
        "id": s.id, "check_no": s.check_no, "period": s.period,
        "check_date": s.check_date.isoformat() if s.check_date else None,
        "status": s.status, "operator_name": s.operator_name,
        "total_diff_amount": float(s.total_diff_amount or 0),
        "voucher_no": s.voucher_no,
        "item_count": len(s.items or []),
        "closed_at": s.closed_at.isoformat() if s.closed_at else None,
    } for s in rows]}


@router.get("/{cid}")
def get_check(cid: int, user: User = Depends(require_role("MANAGER", "OPERATION", "FINANCE", "ADMIN")),
             db: Session = Depends(get_db)):
    s = db.query(StockCheck).get(cid)
    if not s:
        raise HTTPException(404, "盘点单不存在")
    return Resp.ok({
        "id": s.id, "check_no": s.check_no, "period": s.period,
        "check_date": s.check_date.isoformat() if s.check_date else None,
        "status": s.status, "operator_user_id": s.operator_user_id,
        "operator_name": s.operator_name, "items": s.items or [],
        "total_diff_amount": float(s.total_diff_amount or 0),
        "voucher_no": s.voucher_no, "remark": s.remark,
        "closed_at": s.closed_at.isoformat() if s.closed_at else None,
    })


class ItemUpdate(BaseModel):
    item_id: int
    actual_qty: float
    remark: Optional[str] = None


@router.put("/{cid}/item")
def update_item(cid: int, body: ItemUpdate,
                user: User = Depends(require_role("MANAGER", "OPERATION", "FINANCE", "ADMIN")),
                db: Session = Depends(get_db)):
    """更新某行实盘数量, 自动算 diff_qty"""
    from sqlalchemy.orm.attributes import flag_modified
    s = db.query(StockCheck).get(cid)
    if not s:
        raise HTTPException(404, "盘点单不存在")
    if s.status != "DRAFT":
        raise HTTPException(400, f"盘点单状态 {s.status}, 不可修改")
    items = list(s.items or [])
    found = False
    diff_qty = None
    for idx, it in enumerate(items):
        if it.get("item_id") == body.item_id:
            # 替换为新 dict, 触发 SQLAlchemy JSON 列变更检测
            new_it = dict(it)
            new_it["actual_qty"] = float(body.actual_qty)
            new_it["diff_qty"] = round(float(body.actual_qty) - float(new_it.get("book_qty") or 0), 3)
            if body.remark is not None:
                new_it["remark"] = body.remark
            items[idx] = new_it
            diff_qty = new_it["diff_qty"]
            found = True
            break
    if not found:
        raise HTTPException(400, f"行项目 item_id={body.item_id} 不在盘点单中")
    s.items = items
    flag_modified(s, "items")
    db.flush()
    db.commit()
    return Resp.ok({"item_id": body.item_id, "diff_qty": diff_qty})


@router.post("/{cid}/close")
def close_check(cid: int,
               user: User = Depends(require_role("MANAGER", "OPERATION", "FINANCE", "ADMIN")),
               db: Session = Depends(get_db)):
    """封账: 对每行差异生成 InventoryTxn ADJUST + 凭证(待处理财产损溢 vs 原材料/库存商品) + CLOSED"""
    s = db.query(StockCheck).get(cid)
    if not s:
        raise HTTPException(404, "盘点单不存在")
    if s.status != "DRAFT":
        raise HTTPException(400, f"盘点单状态 {s.status}, 不可封账")
    now = bjt_now()
    items = list(s.items or [])
    # 1. 调库存 + 库存流水
    pending_loss = _ledger(db, "1901", "待处理财产损溢", "ASSET", "DEBIT")
    voucher_entries = []
    total_diff_amount = 0.0
    seq = 0
    for it in items:
        diff = it.get("diff_qty")
        if diff is None:
            # 未录入实盘, 视为无差异
            continue
        diff = float(diff)
        if abs(diff) < 0.0005:
            continue
        item_id = it.get("item_id")
        inv = db.query(InventoryItem).get(item_id) if item_id else None
        if not inv:
            continue
        unit_cost = float(it.get("unit_cost") or inv.unit_cost or 0)
        amount = round(abs(diff) * unit_cost, 2)
        # 调账面库存
        inv.stock_qty = float(inv.stock_qty or 0) + diff
        seq += 1
        db.add(InventoryTxn(
            txn_no=f"TXN-ADJ-{now.strftime('%Y%m%d')}-{s.id:04d}-{seq:02d}",
            txn_type="ADJUST",
            item_id=inv.id,
            quantity=abs(diff),
            unit_cost=unit_cost,
            amount=amount,
            ref_doc_type="STOCK_CHECK",
            ref_doc_id=s.id,
            operator_user_id=user.id,
            occurred_at=now,
            remark=f"盘点{s.check_no}{'盘盈' if diff > 0 else '盘亏'}{abs(diff)}{inv.unit or ''}",
        ))
        stock_acc = _stock_account(db, it.get("category") or inv.category)
        if diff > 0:
            # 盘盈: 借 原材料/库存商品 贷 待处理财产损溢
            voucher_entries.append({"account_id": stock_acc.id, "summary": f"盘盈-{inv.name}",
                                     "debit": amount, "credit": 0})
            voucher_entries.append({"account_id": pending_loss.id, "summary": f"盘盈-{inv.name}",
                                     "debit": 0, "credit": amount})
        else:
            # 盘亏: 借 待处理财产损溢 贷 原材料/库存商品
            voucher_entries.append({"account_id": pending_loss.id, "summary": f"盘亏-{inv.name}",
                                     "debit": amount, "credit": 0})
            voucher_entries.append({"account_id": stock_acc.id, "summary": f"盘亏-{inv.name}",
                                     "debit": 0, "credit": amount})
        total_diff_amount += amount
    # 2. 凭证(有差异才生成)
    voucher_no = None
    if voucher_entries:
        v = _post_voucher(db, now, f"盘点差异-{s.check_no}", voucher_entries, user.id)
        voucher_no = v.voucher_no
    # 3. 封账
    s.status = "CLOSED"
    s.closed_at = now
    s.total_diff_amount = round(total_diff_amount, 2)
    s.voucher_no = voucher_no
    log_audit(db, user, "close", "stock_check", s.id,
              after={"check_no": s.check_no, "total_diff_amount": s.total_diff_amount,
                     "voucher_no": voucher_no})
    db.commit()
    return Resp.ok({"id": s.id, "status": s.status, "voucher_no": voucher_no,
                    "total_diff_amount": float(s.total_diff_amount or 0)})
