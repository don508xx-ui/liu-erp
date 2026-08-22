"""销售域API V2 - 公司主体/合同/商机/打样申请/送货单/调价申请
合并6个router到单文件,降低文件数。每个router独立prefix。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role, apply_scope_filter, mask_customer, get_user_role_code
from app.core.audit import log_audit
from app.core.event_bus import emit
from app.models.system import User
from app.models.customer import Customer
from app.models.order import Order, OrderItem
from app.models.sales import (
    Company, Contract, Opportunity, SampleRequest,
    DeliveryNote, DeliveryNoteItem, SalesAdjustment,
)
from app.api.approvals import bjt_now
from app.schemas import Resp

# 公共:序号生成 - 使用自增ID避免并发冲突
def _seq(db, model, prefix):
    obj = db.query(model).order_by(model.id.desc()).first()
    n = (obj.id if obj else 0) + 1
    return f"{prefix}-{bjt_now().strftime('%Y%m%d')}-{n:04d}"


# ============ Company ============
company_router = APIRouter(prefix="/api/companies", tags=["company"])


@company_router.get("")
def list_companies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Company).filter(Company.status == "ACTIVE").order_by(Company.id).all()
    return Resp.ok([_company_dict(c) for c in rows])


@company_router.get("/{cid}")
def get_company(cid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = db.query(Company).filter(Company.id == cid).first()
    if not c:
        raise HTTPException(404, "公司不存在")
    return Resp.ok(_company_dict(c))


class CompanyIn(BaseModel):
    code: str
    name: str
    short_name: Optional[str] = None
    tax_type: str  # GENERAL/SMALL
    tax_no: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    remark: Optional[str] = None


@company_router.post("")
def create_company(body: CompanyIn, user: User = Depends(require_role("ADMIN")),
                   db: Session = Depends(get_db)):
    if db.query(Company).filter(Company.code == body.code).first():
        raise HTTPException(400, "公司编码已存在")
    c = Company(**body.model_dump(), status="ACTIVE")
    db.add(c); db.flush()
    log_audit(db, user, "create", "company", c.id, after=body.model_dump())
    db.commit()
    return Resp.ok({"id": c.id})


@company_router.put("/{cid}")
def update_company(cid: int, body: CompanyIn, user: User = Depends(require_role("ADMIN")),
                   db: Session = Depends(get_db)):
    c = db.query(Company).filter(Company.id == cid).first()
    if not c:
        raise HTTPException(404, "公司不存在")
    before = _company_dict(c)
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    log_audit(db, user, "update", "company", cid, before=before, after=_company_dict(c))
    db.commit()
    return Resp.ok({"id": cid})


def _company_dict(c: Company) -> dict:
    return {
        "id": c.id, "code": c.code, "name": c.name, "short_name": c.short_name,
        "tax_type": c.tax_type, "tax_no": c.tax_no, "bank_name": c.bank_name,
        "bank_account": c.bank_account, "address": c.address, "phone": c.phone,
        "status": c.status, "remark": c.remark,
    }


# ============ Contract ============
contract_router = APIRouter(prefix="/api/contracts", tags=["contract"])


class ContractIn(BaseModel):
    customer_id: int
    company_id: Optional[int] = None
    amount: float = 0
    signed_date: Optional[str] = None
    effective_date: Optional[str] = None
    expire_date: Optional[str] = None
    payment_terms: Optional[str] = None
    remark: Optional[str] = None
    extra: Optional[dict] = None


@contract_router.post("")
def create_contract(body: ContractIn, user: User = Depends(require_role("SALES", "ADMIN")),
                    db: Session = Depends(get_db)):
    if not db.query(Customer).filter(Customer.id == body.customer_id).first():
        raise HTTPException(400, "客户不存在")
    no = _seq(db, Contract, "CT")
    c = Contract(
        contract_no=no, customer_id=body.customer_id, company_id=body.company_id,
        amount=body.amount, status="EFFECTIVE", owner_user_id=user.id,
        payment_terms=body.payment_terms, remark=body.remark, extra=body.extra,
        signed_date=_parse_dt(body.signed_date), effective_date=_parse_dt(body.effective_date),
        expire_date=_parse_dt(body.expire_date),
    )
    db.add(c); db.flush()
    log_audit(db, user, "create", "contract", c.id, after={"no": no})
    db.commit()
    return Resp.ok({"id": c.id, "contract_no": no})


@contract_router.get("")
def list_contracts(keyword: Optional[str] = None, status: Optional[str] = None,
                   page: int = 1, size: int = 20,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Contract)
    q = apply_scope_filter(user, db, q, "contracts")
    if status:
        q = q.filter(Contract.status == status)
    if keyword:
        q = q.filter(Contract.contract_no.contains(keyword))
    total = q.count()
    rows = q.order_by(Contract.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [_contract_dict(db, user, c) for c in rows]}


@contract_router.get("/{cid}")
def get_contract(cid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = db.query(Contract).filter(Contract.id == cid).first()
    if not c:
        raise HTTPException(404, "合同不存在")
    return Resp.ok(_contract_dict(db, user, c))


@contract_router.post("/{cid}/close")
def close_contract(cid: int, user: User = Depends(require_role("SALES", "ADMIN")),
                   db: Session = Depends(get_db)):
    c = db.query(Contract).filter(Contract.id == cid).first()
    if not c:
        raise HTTPException(404, "合同不存在")
    before = c.status
    c.status = "CLOSED"
    log_audit(db, user, "state_change", "contract", cid, before=before, after=c.status)
    db.commit()
    return Resp.ok({"id": cid, "status": c.status})


def _contract_dict(db, user, c: Contract) -> dict:
    cust = db.query(Customer).filter(Customer.id == c.customer_id).first()
    cust_d = mask_customer(user, db, cust) if cust else None
    return {
        "id": c.id, "contract_no": c.contract_no, "customer_id": c.customer_id,
        "customer_name": cust_d["name"] if cust_d else "",
        "company_id": c.company_id, "amount": float(c.amount or 0),
        "signed_date": c.signed_date.isoformat() if c.signed_date else None,
        "status": c.status, "owner_user_id": c.owner_user_id,
        "payment_terms": c.payment_terms, "remark": c.remark,
    }


# ============ Opportunity ============
oppo_router = APIRouter(prefix="/api/opportunities", tags=["opportunity"])


class OppoIn(BaseModel):
    customer_id: Optional[int] = None  # 转化后关联客户,新建商机时可不填
    title: str
    customer_name: Optional[str] = None  # 客户公司名称(线索)
    contact_person: Optional[str] = None
    contact_phone: Optional[str] = None
    company_address: Optional[str] = None
    industry: Optional[str] = None
    expected_amount: float = 0
    stage: str = "LEAD"
    expected_close_date: Optional[str] = None
    delivery_date: Optional[str] = None  # 交期(客户要求交付日期)
    source: Optional[str] = None
    remark: Optional[str] = None


@oppo_router.post("")
def create_oppo(body: OppoIn, user: User = Depends(require_role("SALES", "ADMIN")),
                db: Session = Depends(get_db)):
    no = _seq(db, Opportunity, "OPP")
    o = Opportunity(
        oppo_no=no, customer_id=body.customer_id, title=body.title,
        customer_name=body.customer_name, contact_person=body.contact_person,
        contact_phone=body.contact_phone, company_address=body.company_address,
        industry=body.industry,
        expected_amount=body.expected_amount, stage=body.stage,
        expected_close_date=_parse_dt(body.expected_close_date),
        delivery_date=_parse_dt(body.delivery_date),
        source=body.source, owner_user_id=user.id, remark=body.remark,
    )
    db.add(o); db.flush()
    log_audit(db, user, "create", "opportunity", o.id, after={"no": no})
    db.commit()
    return Resp.ok({"id": o.id, "oppo_no": no})


@oppo_router.get("")
def list_oppo(keyword: Optional[str] = None, stage: Optional[str] = None,
              page: int = 1, size: int = 20,
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Opportunity)
    q = apply_scope_filter(user, db, q, "opportunities")
    if stage:
        q = q.filter(Opportunity.stage == stage)
    if keyword:
        q = q.filter(Opportunity.title.contains(keyword) | Opportunity.oppo_no.contains(keyword))
    total = q.count()
    rows = q.order_by(Opportunity.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [_oppo_dict(db, user, o) for o in rows]}


@oppo_router.get("/{oid}")
def get_oppo(oid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    o = db.query(Opportunity).filter(Opportunity.id == oid).first()
    if not o:
        raise HTTPException(404, "商机不存在")
    return Resp.ok(_oppo_dict(db, user, o))


class StageIn(BaseModel):
    stage: str
    loss_reason: Optional[str] = None
    won_order_id: Optional[int] = None


@oppo_router.put("/{oid}/stage")
def change_stage(oid: int, body: StageIn, user: User = Depends(require_role("SALES", "ADMIN")),
                 db: Session = Depends(get_db)):
    o = db.query(Opportunity).filter(Opportunity.id == oid).first()
    if not o:
        raise HTTPException(404, "商机不存在")
    before = o.stage
    o.stage = body.stage
    o.updated_at = bjt_now()
    if body.stage == "LOST":
        o.loss_reason = body.loss_reason
    if body.stage == "WON" and body.won_order_id:
        o.won_order_id = body.won_order_id
    log_audit(db, user, "state_change", "opportunity", oid, before=before, after=o.stage)
    db.commit()
    return Resp.ok({"id": oid, "stage": o.stage})


def _oppo_dict(db, user, o: Opportunity) -> dict:
    cust = db.query(Customer).filter(Customer.id == o.customer_id).first()
    cust_d = mask_customer(user, db, cust) if cust else None
    return {
        "id": o.id, "oppo_no": o.oppo_no, "customer_id": o.customer_id,
        "customer_name": o.customer_name or (cust_d["name"] if cust_d else ""),
        "contact_person": o.contact_person,
        "contact_phone": o.contact_phone,
        "company_address": o.company_address,
        "industry": o.industry,
        "title": o.title, "expected_amount": float(o.expected_amount or 0),
        "stage": o.stage, "source": o.source, "owner_user_id": o.owner_user_id,
        "won_order_id": o.won_order_id, "loss_reason": o.loss_reason,
        "expected_close_date": o.expected_close_date.isoformat() if o.expected_close_date else None,
        "delivery_date": o.delivery_date.isoformat() if o.delivery_date else None,
        "remark": o.remark,
    }


# ============ 商机转客户 ============
@oppo_router.post("/{oid}/convert")
def convert_oppo(oid: int, user: User = Depends(require_role("SALES", "ADMIN")),
                 db: Session = Depends(get_db)):
    """商机成交后转为客户档案"""
    o = db.query(Opportunity).filter(Opportunity.id == oid).first()
    if not o:
        raise HTTPException(404, "商机不存在")
    if o.stage != "WON":
        raise HTTPException(400, "仅已成交商机可转为客户")
    if o.customer_id:
        raise HTTPException(400, "该商机已转换为客户")
    if not o.customer_name:
        raise HTTPException(400, "商机缺少客户名称,无法转换")
    # 自动生成客户编码
    last = db.query(Customer).order_by(Customer.id.desc()).first()
    code = f"C{(last.id + 1) if last else 1:04d}"
    c = Customer(
        code=code, name=o.customer_name,
        address=o.company_address or "", contact_name=o.contact_person or "",
        contact_phone=o.contact_phone or "", industry=o.industry or "",
        status="ACTIVE",
    )
    db.add(c); db.flush()
    o.customer_id = c.id
    log_audit(db, user, "convert", "opportunity", oid, after={"customer_id": c.id, "customer_name": o.customer_name})
    db.commit()
    return Resp.ok({"id": c.id, "code": c.code, "name": c.name})


# ============ SampleRequest(打样申请) ============
sample_router = APIRouter(prefix="/api/sample-requests", tags=["sample-request"])


class SampleIn(BaseModel):
    customer_id: int
    contact_person: Optional[str] = None
    contact_phone: Optional[str] = None
    email: Optional[str] = None
    company_address: Optional[str] = None
    sample_reason: Optional[str] = None
    sample_reason_other: Optional[str] = None
    part_name: Optional[str] = None
    material: Optional[str] = None
    size_desc: Optional[str] = None
    qty: float = 0
    sample_provided_by: Optional[str] = None
    expected_date: Optional[str] = None
    coating_tech_req: Optional[str] = None
    spray_process: Optional[str] = None
    spray_process_other: Optional[str] = None
    coating_material: Optional[str] = None
    coating_thickness: Optional[str] = None
    hardness_req: Optional[str] = None
    bond_strength_req: Optional[str] = None
    surface_roughness_req: Optional[str] = None
    other_performance_req: Optional[str] = None
    drawings: Optional[str] = None
    drawings_other: Optional[str] = None
    is_charged: Optional[str] = None
    estimated_cost: Optional[float] = None
    cost_remark: Optional[str] = None
    remark: Optional[str] = None


@sample_router.post("")
def create_sample(body: SampleIn, user: User = Depends(require_role("SALES", "ADMIN")),
                  db: Session = Depends(get_db)):
    if not db.query(Customer).filter(Customer.id == body.customer_id).first():
        raise HTTPException(400, "客户不存在")
    dy_no = _seq(db, SampleRequest, "DY")
    ed = None
    if body.expected_date:
        try:
            ed = datetime.strptime(body.expected_date, "%Y-%m-%d")
        except: pass
    r = SampleRequest(
        log_no=dy_no, customer_id=body.customer_id,
        contact_person=body.contact_person, contact_phone=body.contact_phone,
        email=body.email, company_address=body.company_address,
        sample_reason=body.sample_reason, sample_reason_other=body.sample_reason_other,
        part_name=body.part_name, material=body.material, size_desc=body.size_desc,
        qty=body.qty, sample_provided_by=body.sample_provided_by,
        expected_date=ed, coating_tech_req=body.coating_tech_req,
        spray_process=body.spray_process, spray_process_other=body.spray_process_other,
        coating_material=body.coating_material, coating_thickness=body.coating_thickness,
        hardness_req=body.hardness_req, bond_strength_req=body.bond_strength_req,
        surface_roughness_req=body.surface_roughness_req,
        other_performance_req=body.other_performance_req,
        drawings=body.drawings, drawings_other=body.drawings_other,
        is_charged=body.is_charged, estimated_cost=body.estimated_cost,
        cost_remark=body.cost_remark, remark=body.remark,
        status="DRAFT", created_by_user_id=user.id,
    )
    db.add(r); db.flush()
    log_audit(db, user, "create", "sample_request", r.id, after={"no": dy_no})
    from app.api.approvals import start_flow
    inst = start_flow(db, "SAMPLE_REQUEST", r.id, user)
    if inst:
        r.approval_instance_id = inst.id
        r.status = "PENDING"
    db.commit()
    return Resp.ok({"id": r.id, "log_no": dy_no})


@sample_router.get("")
def list_sample(keyword: Optional[str] = None, page: int = 1, size: int = 20,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(SampleRequest)
    if keyword:
        q = q.filter(SampleRequest.log_no.contains(keyword) | SampleRequest.part_name.contains(keyword))
    total = q.count()
    rows = q.order_by(SampleRequest.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [_sample_dict(db, user, r) for r in rows]}


@sample_router.get("/{rid}")
def get_sample(rid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = db.query(SampleRequest).filter(SampleRequest.id == rid).first()
    if not r:
        raise HTTPException(404, "打样申请不存在")
    return Resp.ok(_sample_dict(db, user, r))


def _sample_dict(db, user, r: SampleRequest) -> dict:
    cust = db.query(Customer).filter(Customer.id == r.customer_id).first()
    cust_d = mask_customer(user, db, cust) if cust else None
    return {
        "id": r.id, "log_no": r.log_no, "order_id": r.order_id,
        "customer_id": r.customer_id, "customer_name": cust_d["name"] if cust_d else "",
        "contact_person": r.contact_person, "contact_phone": r.contact_phone,
        "email": r.email, "company_address": r.company_address,
        "sample_reason": r.sample_reason, "sample_reason_other": r.sample_reason_other,
        "part_name": r.part_name, "material": r.material, "size_desc": r.size_desc,
        "qty": float(r.qty or 0), "sample_provided_by": r.sample_provided_by,
        "expected_date": r.expected_date.isoformat()[:10] if r.expected_date else None,
        "coating_tech_req": r.coating_tech_req,
        "spray_process": r.spray_process, "spray_process_other": r.spray_process_other,
        "coating_material": r.coating_material, "coating_thickness": r.coating_thickness,
        "hardness_req": r.hardness_req, "bond_strength_req": r.bond_strength_req,
        "surface_roughness_req": r.surface_roughness_req,
        "other_performance_req": r.other_performance_req,
        "drawings": r.drawings, "drawings_other": r.drawings_other,
        "is_charged": r.is_charged, "estimated_cost": float(r.estimated_cost or 0),
        "cost_remark": r.cost_remark,
        "status": r.status, "created_by_user_id": r.created_by_user_id,
        "approval_instance_id": r.approval_instance_id, "remark": r.remark,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


# ============ DeliveryNote ============
deli_router = APIRouter(prefix="/api/deliveries", tags=["delivery"])


class DeliItemIn(BaseModel):
    order_item_id: Optional[int] = None
    part_name: str
    part_spec: Optional[str] = None
    qty: float
    unit: str
    unit_price: float
    remark: Optional[str] = None


class DeliIn(BaseModel):
    order_id: int
    items: List[DeliItemIn]
    delivery_address: Optional[str] = None
    remark: Optional[str] = None


@deli_router.post("")
def create_deli(body: DeliIn, user: User = Depends(require_role("SALES", "OPERATION", "ADMIN")),
                db: Session = Depends(get_db)):
    o = db.query(Order).filter(Order.id == body.order_id).first()
    if not o:
        raise HTTPException(404, "订单不存在")
    no = _seq(db, DeliveryNote, "DN")
    total_qty = sum(it.qty for it in body.items)
    total_amt = sum(it.qty * it.unit_price for it in body.items)
    d = DeliveryNote(
        delivery_no=no, order_id=body.order_id, company_id=o.company_id,
        customer_id=o.customer_id, status="PENDING",
        total_qty=total_qty, total_amount=total_amt,
        delivery_address=body.delivery_address, remark=body.remark,
    )
    db.add(d); db.flush()
    for it in body.items:
        db.add(DeliveryNoteItem(
            delivery_note_id=d.id, order_item_id=it.order_item_id,
            part_name=it.part_name, part_spec=it.part_spec, qty=it.qty,
            unit=it.unit, unit_price=it.unit_price, amount=it.qty * it.unit_price,
            remark=it.remark,
        ))
    log_audit(db, user, "create", "delivery", d.id, after={"no": no})
    db.commit()
    return Resp.ok({"id": d.id, "delivery_no": no})


@deli_router.get("")
def list_deli(keyword: Optional[str] = None, status: Optional[str] = None,
              page: int = 1, size: int = 20,
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(DeliveryNote)
    if status:
        q = q.filter(DeliveryNote.status == status)
    if keyword:
        q = q.filter(DeliveryNote.delivery_no.contains(keyword))
    total = q.count()
    rows = q.order_by(DeliveryNote.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [_deli_dict(db, user, d) for d in rows]}


@deli_router.get("/{did}")
def get_deli(did: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    d = db.query(DeliveryNote).filter(DeliveryNote.id == did).first()
    if not d:
        raise HTTPException(404, "送货单不存在")
    return Resp.ok(_deli_dict(db, user, d, with_items=True))


@deli_router.post("/{did}/ship")
def ship_deli(did: int, user: User = Depends(require_role("SALES", "ADMIN")),
              db: Session = Depends(get_db)):
    """销售最后确认发货 - 触发order.delivered事件(业财钩子在阶段4接)"""
    d = db.query(DeliveryNote).filter(DeliveryNote.id == did).first()
    if not d:
        raise HTTPException(404, "送货单不存在")
    if d.status != "PENDING":
        raise HTTPException(400, f"送货单状态{d.status}不可发货")
    o = db.query(Order).filter(Order.id == d.order_id).first()
    # 仅订单owner销售或ADMIN可确认发货
    role_code = get_user_role_code(user, db)
    if role_code not in ("ADMIN",) and o and o.sales_user_id != user.id:
        raise HTTPException(403, "仅订单经手销售可确认发货")
    before = d.status
    d.status = "SHIPPED"
    d.shipped_at = bjt_now()
    d.shipped_by_user_id = user.id
    if o:
        o.delivery_status = "DELIVERED"
        o.delivered_at = d.shipped_at
        if o.status == "PENDING_DELIVERY":
            o.status = "DELIVERED"
    log_audit(db, user, "state_change", "delivery", did, before=before, after=d.status)
    db.flush()
    emit(db, "order.delivered", "delivery", did,
         {"order_id": d.order_id, "delivery_no": d.delivery_no,
          "amount": float(d.total_amount or 0)}, user)
    db.commit()
    return Resp.ok({"id": did, "status": d.status})


def _deli_dict(db, user, d: DeliveryNote, with_items=False) -> dict:
    cust = db.query(Customer).filter(Customer.id == d.customer_id).first()
    cust_d = mask_customer(user, db, cust) if cust else None
    o = db.query(Order).filter(Order.id == d.order_id).first()
    out = {
        "id": d.id, "delivery_no": d.delivery_no, "order_id": d.order_id,
        "order_no": o.order_no if o else "", "company_id": d.company_id,
        "customer_id": d.customer_id, "customer_name": cust_d["name"] if cust_d else "",
        "status": d.status, "total_qty": float(d.total_qty or 0),
        "total_amount": float(d.total_amount or 0),
        "shipped_at": d.shipped_at.isoformat() if d.shipped_at else None,
        "shipped_by_user_id": d.shipped_by_user_id,
        "delivery_address": d.delivery_address, "remark": d.remark,
    }
    if with_items:
        out["items"] = [{
            "id": it.id, "order_item_id": it.order_item_id, "part_name": it.part_name,
            "part_spec": it.part_spec, "qty": float(it.qty or 0), "unit": it.unit,
            "unit_price": float(it.unit_price or 0), "amount": float(it.amount or 0),
        } for it in d.items]
    return out


# ============ SalesAdjustment ============
adj_router = APIRouter(prefix="/api/adjustments", tags=["adjustment"])


class AdjIn(BaseModel):
    order_id: int
    original_amount: float
    adjusted_amount: float
    reason: str
    remark: Optional[str] = None


@adj_router.post("")
def create_adj(body: AdjIn, user: User = Depends(require_role("SALES", "ADMIN")),
               db: Session = Depends(get_db)):
    """调价申请创建 - 已废弃,统一走 /api/approvals/price-adjustment 入口。
    此处保留转调以兼容旧调用方,但传递完整校验逻辑。"""
    from app.api.approvals import PriceAdjustIn, create_price_adjustment
    # 转调统一入口(内部含订单状态校验、防重复校验、流程启动)
    pa_in = PriceAdjustIn(
        order_id=body.order_id,
        type="DECREASE" if body.adjusted_amount < body.original_amount else "INCREASE",
        method="FIXED",
        amount=0,
        percent=0,
        reason=body.reason or "",
        new_amount=body.adjusted_amount,
    )
    return create_price_adjustment(pa_in, user, db)


@adj_router.get("")
def list_adj(keyword: Optional[str] = None, status: Optional[str] = None,
             page: int = 1, size: int = 20,
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(SalesAdjustment)
    # 销售只看自己发起的调价
    role_code = get_user_role_code(user, db)
    if role_code == "SALES":
        q = q.filter(SalesAdjustment.initiator_user_id == user.id)
    if status:
        q = q.filter(SalesAdjustment.status == status)
    if keyword:
        q = q.filter(SalesAdjustment.adj_no.contains(keyword))
    total = q.count()
    rows = q.order_by(SalesAdjustment.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [_adj_dict(db, user, a) for a in rows]}


@adj_router.get("/{aid}")
def get_adj(aid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = db.query(SalesAdjustment).filter(SalesAdjustment.id == aid).first()
    if not a:
        raise HTTPException(404, "调价申请不存在")
    return Resp.ok(_adj_dict(db, user, a))


@adj_router.post("/{aid}/approve")
def approve_adj(aid: int, user: User = Depends(require_role("GM", "ADMIN")),
                db: Session = Depends(get_db)):
    """GM必审 - 通过后触发adjustment.approved事件(阶段4钩子调整应收)"""
    a = db.query(SalesAdjustment).filter(SalesAdjustment.id == aid).first()
    if not a:
        raise HTTPException(404, "调价申请不存在")
    if a.status != "PENDING":
        raise HTTPException(400, f"调价状态{a.status}不可审批")
    before = a.status
    a.status = "APPROVED"
    a.approved_at = bjt_now()
    a.approved_by_user_id = user.id
    log_audit(db, user, "approve", "adjustment", aid, before=before, after=a.status)
    db.flush()
    emit(db, "adjustment.approved", "adjustment", aid,
         {"order_id": a.order_id, "original": float(a.original_amount or 0),
          "adjusted": float(a.adjusted_amount or 0),
          "diff": float(a.diff_amount or 0)}, user)
    db.commit()
    return Resp.ok({"id": aid, "status": a.status})


@adj_router.post("/{aid}/reject")
def reject_adj(aid: int, user: User = Depends(require_role("GM", "ADMIN")),
               db: Session = Depends(get_db)):
    a = db.query(SalesAdjustment).filter(SalesAdjustment.id == aid).first()
    if not a:
        raise HTTPException(404, "调价申请不存在")
    if a.status != "PENDING":
        raise HTTPException(400, f"调价状态{a.status}不可驳回")
    before = a.status
    a.status = "REJECTED"
    log_audit(db, user, "reject", "adjustment", aid, before=before, after=a.status)
    db.commit()
    return Resp.ok({"id": aid, "status": a.status})


def _adj_dict(db, user, a: SalesAdjustment) -> dict:
    o = db.query(Order).filter(Order.id == a.order_id).first()
    return {
        "id": a.id, "adj_no": a.adj_no, "order_id": a.order_id,
        "order_no": o.order_no if o else "",
        "original_amount": float(a.original_amount or 0),
        "adjusted_amount": float(a.adjusted_amount or 0),
        "diff_amount": float(a.diff_amount or 0),
        "reason": a.reason, "status": a.status,
        "initiator_user_id": a.initiator_user_id,
        "approved_by_user_id": a.approved_by_user_id,
        "approved_at": a.approved_at.isoformat() if a.approved_at else None,
        "remark": a.remark,
    }


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None
