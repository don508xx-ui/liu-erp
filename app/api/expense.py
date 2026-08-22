"""费用报销API - 蛋哥报账流程"""
import os, uuid, json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role, get_user_role_code
from app.core.audit import log_audit
from app.core.event_bus import emit
from app.models.system import User
from app.models.expense import ExpenseClaim
from app.api.approvals import bjt_now
from app.schemas import Resp

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static", "uploads", "expense")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
MAX_SIZE = 10 * 1024 * 1024  # 10MB

router = APIRouter(prefix="/api/expenses", tags=["expense"])


class ExpenseItemIn(BaseModel):
    date: str
    category: str  # 交通/餐饮/办公用品/差旅/其他
    amount: float
    remark: Optional[str] = None


class ExpenseIn(BaseModel):
    claim_type: str  # TRAVEL/MEAL/OFFICE/TRANSPORT/OTHER
    company_id: Optional[int] = None
    items: List[ExpenseItemIn] = []
    description: Optional[str] = None
    remark: Optional[str] = None
    form_data: Optional[dict] = None  # 画布动态表单全字段(以画布设计为准,零硬编码)
    attachments: Optional[List[dict]] = None  # 提交时一次性带已上传的 att record 列表


def _seq(db):
    obj = db.query(ExpenseClaim).order_by(ExpenseClaim.id.desc()).first()
    n = (obj.id if obj else 0) + 1
    return f"EC-{bjt_now().strftime('%Y%m%d')}-{n:04d}"


@router.post("")
def create(body: ExpenseIn, user: User = Depends(require_role("SALES", "OPERATION", "FINANCE", "ADMIN")),
          db: Session = Depends(get_db)):
    """员工提交报销申请"""
    fd = body.form_data or {}
    # 画布字段为主: 提取核心列向下兼容, 全字段存extra
    claim_type = fd.get("expense_type") or body.claim_type
    total = fd.get("amount")
    if total is None:
        total = sum(it.amount for it in body.items)
    reason = fd.get("reason") or fd.get("attachment_note") or body.description
    occur_date = fd.get("occur_date")
    if fd.get("items"):
        items = fd["items"]
    elif items := body.items:
        items = [it.model_dump() for it in body.items]
    else:
        items = [{"date": occur_date, "category": claim_type, "amount": total,
                  "remark": reason}] if total else []
    ec = ExpenseClaim(
        claim_no=_seq(db), applicant_user_id=user.id,
        claim_type=claim_type, company_id=body.company_id,
        amount=total, status="DRAFT",
        items=items,
        description=reason, remark=fd.get("remark", body.remark),
        extra=fd or None,
    )
    # 一次性写入已上传的附件(此时 eid 已生成,可重跑查重把自己排除)
    if body.attachments:
        # 等 flush 后 eid 才有,这里先临时存原始,再 flush 后重写
        ec.attachments = body.attachments
    db.add(ec)
    db.flush()
    if ec.attachments:
        # eid 已生成,重跑每条 att 的查重(排除自己)
        new_atts = []
        for a in ec.attachments:
            dup = _check_duplicate(db, a.get("invoice_no") or "", exclude_eid=ec.id)
            a["duplicate_count"] = dup
            if dup > 0:
                a["risk_flag"] = "DUPLICATE"
                a["risk_reason"] = f"该发票号已在历史报销单出现 {dup} 次"
            elif not a.get("invoice_no"):
                a["risk_flag"] = "MISSING_NO"
                a["risk_reason"] = "未填发票号,无法查重"
            else:
                a["risk_flag"] = None
                a["risk_reason"] = None
            new_atts.append(a)
        ec.attachments = new_atts
    log_audit(db, user, "create", "expense_claim", ec.id, after={"no": ec.claim_no, "amount": float(total)})
    db.commit()
    return Resp.ok({"id": ec.id, "claim_no": ec.claim_no, "amount": float(total)})


@router.post("/{eid}/submit")
def submit(eid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """提交报销申请(申请人提交)"""
    ec = db.query(ExpenseClaim).get(eid)
    if not ec:
        raise HTTPException(404, "报销单不存在")
    if ec.applicant_user_id != user.id:
        raise HTTPException(403, "仅申请人可提交")
    if ec.status not in ("DRAFT", "REJECTED"):
        raise HTTPException(400, f"状态{ec.status}不可提交")
    ec.status = "SUBMITTED"
    log_audit(db, user, "state_change", "expense_claim", eid, before="DRAFT", after="SUBMITTED")
    from app.api.approvals import start_flow
    inst = start_flow(db, "EXPENSE", eid, user)
    if inst:
        ec.approval_instance_id = inst.id
    db.commit()
    return Resp.ok({"id": eid, "status": "SUBMITTED"})


@router.post("/{eid}/approve")
def approve(eid: int, user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
           db: Session = Depends(get_db)):
    """审批报销单 - 金额>5000需GM终审,≤5000财务可终审"""
    ec = db.query(ExpenseClaim).get(eid)
    if not ec:
        raise HTTPException(404, "报销单不存在")
    if ec.status not in ("SUBMITTED", "APPROVED"):
        raise HTTPException(400, f"状态{ec.status}不可审批")

    role_code = get_user_role_code(user, db)
    amount = float(ec.amount or 0)

    # GM/ADMIN可以直接终审(从SUBMITTED或APPROVED到PAID)
    if role_code in ("GM", "ADMIN"):
        ec.status = "PAID"
        ec.approved_by_user_id = user.id
        ec.approved_at = bjt_now()
        log_audit(db, user, "approve", "expense_claim", eid, before="SUBMITTED/APPROVED", after="PAID")
        db.flush()
        emit(db, "expense.paid", "expense_claim", eid,
             {"claim_no": ec.claim_no, "amount": amount, "applicant_id": ec.applicant_user_id}, user)
        db.commit()
        return Resp.ok({"id": eid, "status": "PAID"})

    # 财务审批: SUBMITTED状态
    if ec.status == "SUBMITTED":
        if amount > 5000:
            # 大额报销(>5000): 财务只能初审,需GM终审
            ec.status = "APPROVED"
            log_audit(db, user, "approve", "expense_claim", eid, before="SUBMITTED", after="APPROVED")
            db.commit()
            return Resp.ok({"id": eid, "status": "APPROVED"})
        else:
            # 小额报销(≤5000): 财务可直接终审
            ec.status = "PAID"
            ec.approved_by_user_id = user.id
            ec.approved_at = bjt_now()
            log_audit(db, user, "approve", "expense_claim", eid, before="SUBMITTED", after="PAID")
            db.flush()
            emit(db, "expense.paid", "expense_claim", eid,
                 {"claim_no": ec.claim_no, "amount": amount, "applicant_id": ec.applicant_user_id}, user)
            db.commit()
            return Resp.ok({"id": eid, "status": "PAID"})

    # 财务尝试终审APPROVED状态(无权限,需GM)
    raise HTTPException(403, f"金额{amount}元需总经理终审")


@router.post("/{eid}/reject")
def reject(eid: int, body: dict, user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
          db: Session = Depends(get_db)):
    """驳回报销单"""
    ec = db.query(ExpenseClaim).get(eid)
    if not ec:
        raise HTTPException(404, "报销单不存在")
    if ec.status not in ("SUBMITTED", "APPROVED"):
        raise HTTPException(400, f"状态{ec.status}不可驳回")
    before = ec.status
    ec.status = "REJECTED"
    ec.remark = (ec.remark or "") + f"\n驳回原因: {body.get('reason', '')}"
    log_audit(db, user, "reject", "expense_claim", eid, before=before, after="REJECTED")
    db.commit()
    return Resp.ok({"id": eid, "status": "REJECTED"})


@router.get("")
def list_(applicant_id: Optional[int] = None, status: Optional[str] = None,
         page: int = 1, size: int = 20,
         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """查询报销单 - 非豁免角色只看自己的，applicant_id不可越权"""
    q = db.query(ExpenseClaim)
    role_code = get_user_role_code(user, db)
    EXEMPT = ("FINANCE", "ADMIN", "GM")
    if role_code not in EXEMPT:
        # 非豁免角色强制只看自己，忽略applicant_id参数防越权
        q = q.filter(ExpenseClaim.applicant_user_id == user.id)
    elif applicant_id:
        q = q.filter(ExpenseClaim.applicant_user_id == applicant_id)
    if status:
        q = q.filter(ExpenseClaim.status == status)
    total = q.count()
    rows = q.order_by(ExpenseClaim.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [_to_dict(db, e) for e in rows]}


@router.get("/{eid}")
def get(eid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ec = db.query(ExpenseClaim).get(eid)
    if not ec:
        raise HTTPException(404, "报销单不存在")
    role_code = get_user_role_code(user, db)
    if role_code not in ("FINANCE", "ADMIN", "GM") and ec.applicant_user_id != user.id:
        raise HTTPException(403, "无权查看他人报销单")
    return Resp.ok(_to_dict(db, ec, with_items=True))


def _to_dict(db, ec: ExpenseClaim, with_items=False) -> dict:
    applicant = db.query(User).filter(User.id == ec.applicant_user_id).first()
    approver = db.query(User).filter(User.id == ec.approved_by_user_id).first() if ec.approved_by_user_id else None
    d = {
        "id": ec.id, "claim_no": ec.claim_no,
        "applicant_user_id": ec.applicant_user_id,
        "applicant_name": applicant.name if applicant else "",
        "claim_type": ec.claim_type, "amount": float(ec.amount or 0),
        "status": ec.status, "company_id": ec.company_id,
        "approved_by_name": approver.name if approver else "",
        "approved_at": ec.approved_at.isoformat() if ec.approved_at else None,
        "description": ec.description, "remark": ec.remark,
        "attachments": ec.attachments or [],
        "created_at": ec.created_at.isoformat() if ec.created_at else None,
    }
    if with_items:
        d["items"] = ec.items
    return d


def _check_duplicate(db, invoice_no: str, exclude_eid: int = None) -> int:
    """扫描所有报销单的 attachments,统计同一发票号出现次数(不含自己)"""
    if not invoice_no:
        return 0
    cnt = 0
    rows = db.query(ExpenseClaim).filter(ExpenseClaim.attachments.isnot(None)).all()
    for r in rows:
        if exclude_eid and r.id == exclude_eid:
            continue
        for a in (r.attachments or []):
            if (a.get("invoice_no") or "").strip() == invoice_no.strip():
                cnt += 1
    return cnt


def _save_att_record(db, filename: str, mime: str, size: int, content: bytes,
                     invoice_no: str, invoice_code: str, invoice_amount,
                     invoice_date: str, issuer: str, exclude_eid: int = None):
    """通用: 把已上传字节保存为正式文件,组装一条 att record(含查重)"""
    ext = (filename or "f.bin").rsplit(".", 1)[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "webp", "pdf"):
        ext = "jpg"
    save_name = f"{bjt_now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}.{ext}"
    save_path = os.path.join(UPLOAD_DIR, save_name)
    with open(save_path, "wb") as f:
        f.write(content)
    url = f"/static/uploads/expense/{save_name}"
    dup = _check_duplicate(db, invoice_no, exclude_eid=exclude_eid)
    return {
        "aid": uuid.uuid4().hex[:12],
        "filename": filename or save_name,
        "url": url, "mime": mime, "size": size,
        "invoice_no": (invoice_no or "").strip(),
        "invoice_code": (invoice_code or "").strip(),
        "invoice_amount": float(invoice_amount) if invoice_amount else None,
        "invoice_date": invoice_date or None,
        "issuer": (issuer or "").strip(),
        "duplicate_count": dup,
        "risk_flag": "DUPLICATE" if dup > 0 else ("MISSING_NO" if not invoice_no else None),
        "risk_reason": f"该发票号已在历史报销单出现 {dup} 次" if dup > 0 else ("未填发票号,无法查重" if not invoice_no else None),
        "uploaded_at": bjt_now().isoformat(),
    }


@router.post("/tmp-attachment")
async def upload_tmp_attachment(
    file: UploadFile = File(...),
    invoice_no: str = Form(""),
    invoice_code: str = Form(""),
    invoice_amount: str = Form(""),
    invoice_date: str = Form(""),
    issuer: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建报销单前临时上传附件 - 文件先存到正式目录,返回完整 att record,前端把它放进 body.attachments 数组,
    submit 创建报销单时一次性写入 ec.attachments(此时才正式与 eid 关联)"""
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(400, "文件超过10MB限制")
    if file.content_type not in ALLOWED_MIME:
        raise HTTPException(400, "仅支持 jpg/png/webp/pdf")
    # 临时无 eid,查重时不排除任何单子(只要历史里出现过就提示)
    att = _save_att_record(db, file.filename, file.content_type, len(content), content,
                           invoice_no, invoice_code, invoice_amount, invoice_date, issuer,
                           exclude_eid=None)
    # 不持久化到 DB,只把 att record 返回前端;前端把整数组跟着创建 body 一起 POST
    return Resp.ok(att)


@router.put("/{eid}/attachments/{aid}")
def update_attachment_meta(
    eid: int, aid: str, body: dict,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    """更新附件元数据(发票号/金额/开票方) - 改了发票号要重跑查重"""
    ec = db.query(ExpenseClaim).get(eid)
    if not ec:
        raise HTTPException(404, "报销单不存在")
    atts = ec.attachments or []
    target = next((a for a in atts if a.get("aid") == aid), None)
    if not target:
        raise HTTPException(404, "附件不存在")
    for k in ("invoice_no", "invoice_code", "issuer", "invoice_date"):
        if k in body:
            target[k] = (str(body[k]) or "").strip() if k != "invoice_date" else body[k]
    if "invoice_amount" in body:
        try: target["invoice_amount"] = float(body["invoice_amount"]) if body["invoice_amount"] else None
        except: pass
    # 重跑查重
    dup = _check_duplicate(db, target.get("invoice_no") or "", exclude_eid=eid)
    target["duplicate_count"] = dup
    if dup > 0:
        target["risk_flag"] = "DUPLICATE"
        target["risk_reason"] = f"该发票号已在历史报销单出现 {dup} 次"
    elif not target.get("invoice_no"):
        target["risk_flag"] = "MISSING_NO"
        target["risk_reason"] = "未填发票号,无法查重"
    else:
        target["risk_flag"] = None
        target["risk_reason"] = None
    ec.attachments = atts
    db.commit()
    return Resp.ok(target)


@router.delete("/{eid}/attachments/{aid}")
def delete_attachment(
    eid: int, aid: str,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    """删除附件 - 同时删文件"""
    ec = db.query(ExpenseClaim).get(eid)
    if not ec:
        raise HTTPException(404, "报销单不存在")
    atts = ec.attachments or []
    target = next((a for a in atts if a.get("aid") == aid), None)
    if not target:
        raise HTTPException(404, "附件不存在")
    # 删文件
    try:
        if target.get("url"):
            fp = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "static", "uploads", "expense", target["url"].rsplit("/", 1)[-1])
            if os.path.exists(fp):
                os.remove(fp)
    except Exception:
        pass
    ec.attachments = [a for a in atts if a.get("aid") != aid]
    db.commit()
    return Resp.ok({"aid": aid, "deleted": True})