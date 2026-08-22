from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role
from app.core.audit import log_audit
from app.core.event_bus import emit
from app.models.system import User
from app.models.finance import FinanceDoc, WorkOrderCost, Account, PayrollRun, FinanceItem
from app.models.fund import FundAccount, FundFlow
from app.models.order import Order
from app.models.workshop import WorkOrder, Completion
from app.models.sales import Company
from app.api.approvals import bjt_now, start_flow
from app.schemas import Resp

router = APIRouter(prefix="/api/finance", tags=["finance"])


# ============ 财务看板 (GM/管理员) ============
PERIOD_PRESETS = {
    "week": "本周", "month": "本月", "quarter": "本季度",
    "half": "本半年", "year": "本年",
    "last_week": "上周", "last_month": "上月",
    "last_quarter": "上季度", "last_year": "去年",
}
EXPENSE_CATEGORIES = ["工资", "融资成本", "气体", "耗材", "食堂", "佣金", "提成", "其他"]


def _resolve_range(period: str, start: Optional[str], end: Optional[str]):
    """解析时间范围 → (start_date, end_date). 内置固话周期 + 自定义日期"""
    from datetime import timedelta as td
    if start and end:  # 自定义优先
        return datetime.strptime(start[:10], "%Y-%m-%d"), datetime.strptime(end[:10], "%Y-%m-%d") + td(days=1)
    now = bjt_now()
    y, m, d = now.year, now.month, now.day
    weekday = now.weekday()
    if period == "week":  # 周一至今天
        s = (now - td(days=weekday)).replace(hour=0, minute=0, second=0, microsecond=0)
        return s, now + td(days=1)
    if period == "last_week":
        s = (now - td(days=weekday + 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        return s, s + td(days=7)
    if period in ("quarter", "last_quarter"):
        qm = ((m - 1) // 3) * 3 + 1
        s = datetime(y, qm, 1)
        if period == "quarter":
            return s, now + td(days=1)
        # 上季度
        if qm >= 4:
            s = datetime(y - 1, 10, 1)
        else:
            s = datetime(y, qm - 3, 1)
        return s, s + td(days=91)
    if period == "half":
        s = datetime(y, 7 if m >= 7 else 1, 1)
        return s, now + td(days=1)
    if period == "last_year":
        return datetime(y - 1, 1, 1), datetime(y, 1, 1)
    if period == "last_month":
        e = (now.replace(day=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        s = (e - _timedelta(days=1)).replace(day=1).replace(hour=0, minute=0, second=0, microsecond=0)
        return s, e
    if period == "month":
        s = datetime(y, m, 1)
    else:  # year
        s = datetime(y, 1, 1)
    return s, now + _timedelta(days=1)


from datetime import timedelta as _timedelta


@router.get("/dashboard")
def finance_dashboard(period: str = "month", start: Optional[str] = None, end: Optional[str] = None,
                      user: User = Depends(require_role("FINANCE", "GM", "ADMIN")), db: Session = Depends(get_db)):
    """资金收支财务看板 - 按周/月/季/半年/年/自定义聚合"""
    from sqlalchemy import func
    from app.models.fund import FundAccount, FundFlow
    from app.models.sales import Company
    s, e = _resolve_range(period, start, end)

    # 1. 账户期初/期间收支/期末
    accs = db.query(FundAccount).filter(FundAccount.enabled == 1).all()
    cmap = {c.id: c.name for c in db.query(Company).all()}
    flows = db.query(FundFlow).filter(FundFlow.occur_date >= s, FundFlow.occur_date < e).all()
    account_rows = []
    fund_begin_total = 0.0
    for a in accs:
        # 期初 = 该账户启用时初始余额 + 期间之前的所有流水净额
        from sqlalchemy import case as sqlcase
        prior = db.query(func.coalesce(func.sum(FundFlow.amount * sqlcase((FundFlow.direction == "IN", 1), else_=-1)), 0)
                         ).filter(FundFlow.fund_account_id == a.id, FundFlow.occur_date < s).scalar() or 0
        begin = float(a.opening_balance or 0) + float(prior or 0)
        inn = sum(float(f.amount) for f in flows if f.fund_account_id == a.id and f.direction == "IN")
        out = sum(float(f.amount) for f in flows if f.fund_account_id == a.id and f.direction == "OUT")
        account_rows.append({
            "id": a.id, "code": a.code, "name": a.name,
            "company_name": (cmap.get(a.company_id) or "集团"),
            "begin": round(begin, 2), "income": round(inn, 2), "expense": round(out, 2),
            "end": round(begin + inn - out, 2),
        })
        fund_begin_total += begin

    total_in = sum(float(f.amount) for f in flows if f.direction == "IN")
    total_out = sum(float(f.amount) for f in flows if f.direction == "OUT")
    end_total = fund_begin_total + total_in - total_out

    # 2. 应收/应付余额(截至现在)
    def _bal(doc_type):
        r = db.query(
            func.coalesce(func.sum(FinanceDoc.amount), 0),
            func.coalesce(func.sum(FinanceDoc.settled_amount), 0),
        ).filter(FinanceDoc.doc_type == doc_type, FinanceDoc.status.in_(["OPEN", "DRAFT"])).first()
        return round(float(r[0] or 0) - float(r[1] or 0), 2)
    ar_bal = _bal("RECEIVABLE")
    ap_bal = _bal("PAYABLE")

    # 3. 趋势(按粒度)
    bucket = None
    if period in ("week", "last_week"):
        n_days = (e - s).days
        bucket = n_days or 7
    in_trend, out_trend, labels = [], [], []
    if period == "year" or (period == "custom" and (e - s).days > 365):
        # 按月
        months = sorted({f"{f.occur_date.year}-{f.occur_date.month:02d}" for f in flows})
        for mk in months:
            yy, mm = map(int, mk.split("-"))
            mn = datetime(yy, mm, 1)
        import itertools
        groups = {}
        for f in flows:
            k = f"{f.occur_date.year}-{f.occur_date.month:02d}"
            groups.setdefault(k, [0.0, 0.0])
            groups[k][0 if f.direction == "IN" else 1] += float(f.amount)
        for mk in sorted(groups):
            labels.append(mk)
            in_trend.append(round(groups[mk][0], 2))
            out_trend.append(round(groups[mk][1], 2))
    else:
        # 按日(周/短区间)
        from collections import OrderedDict
        groups = OrderedDict()
        dd = s
        while dd < e:
            groups[dd.strftime("%m-%d")] = [0.0, 0.0]
            dd += _timedelta(days=1)
        for f in flows:
            k = f.occur_date.strftime("%m-%d")
            if k in groups:
                groups[k][0 if f.direction == "IN" else 1] += float(f.amount)
        labels = list(groups.keys())
        in_trend = [round(v[0], 2) for v in groups.values()]
        out_trend = [round(v[1], 2) for v in groups.values()]

    # 4. 支出结构
    exp = {}
    for f in flows:
        if f.direction == "OUT":
            c = f.expense_category or "其他"
            exp[c] = exp.get(c, 0) + float(f.amount)
    expense_breakdown = [{"name": k, "value": round(v, 2)} for k, v in exp.items()]

    # 5. 账龄
    now = bjt_now()

    def _aging(doc_type):
        ag = {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
        for r in db.query(FinanceDoc).filter(FinanceDoc.doc_type == doc_type,
                                             FinanceDoc.status.in_(["OPEN", "DRAFT"])).all():
            rem = float(r.amount or 0) - float(r.settled_amount or 0)
            if rem <= 0:
                continue
            days = (now - (r.account_date or now)).days
            if days <= 30: ag["0-30"] += rem
            elif days <= 60: ag["31-60"] += rem
            elif days <= 90: ag["61-90"] += rem
            else: ag["90+"] += rem
        return {k: round(v, 2) for k, v in ag.items()}
    ar_aging = _aging("RECEIVABLE")
    ap_aging = _aging("PAYABLE")

    # 6. 双公司对比
    company_compare = []
    for cid, cname in cmap.items():
        fi = sum(float(f.amount) for f in flows if f.direction == "IN" and f.fund_account_id in {a.id for a in accs if a.company_id == cid})
        fo = sum(float(f.amount) for f in flows if f.direction == "OUT" and f.fund_account_id in {a.id for a in accs if a.company_id == cid})
        company_compare.append({"company": cname, "income": round(fi, 2), "expense": round(fo, 2),
                                "net": round(fi - fo, 2)})

    # 现金实时余额(备用金/借款速查)
    cash_acc = next((r for r in account_rows if r["code"] == "CASH"), None)

    return {"code": 0, "data": {
        "range": {"period": period, "label": PERIOD_PRESETS.get(period, "自定义"),
                  "start": s.isoformat(), "end": (e - _timedelta(days=1)).isoformat()},
        "kpis": {"fund_total": round(end_total, 2), "fund_begin": round(fund_begin_total, 2),
                 "income": round(total_in, 2), "expense": round(total_out, 2),
                 "net": round(total_in - total_out, 2),
                 "cash_balance": (cash_acc["end"] if cash_acc else None),
                 "ar_balance": ar_bal, "ap_balance": ap_bal},
        "account_rows": account_rows,
        "trend": {"labels": labels, "income": in_trend, "expense": out_trend},
        "expense_breakdown": expense_breakdown,
        "aging": {"ar": ar_aging, "ap": ap_aging},
        "company_compare": company_compare,
    }}


@router.get("/fund-flows")
def fund_flows(fund_account_id: Optional[int] = None, page: int = 1, size: int = 20,
               user: User = Depends(require_role("FINANCE", "GM", "ADMIN")), db: Session = Depends(get_db)):
    from app.models.fund import FundFlow
    q = db.query(FundFlow)
    if fund_account_id:
        q = q.filter(FundFlow.fund_account_id == fund_account_id)
    total = q.count()
    rows = q.order_by(FundFlow.occur_date.desc(), FundFlow.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"code": 0, "total": total, "data": [{
        "id": r.id, "fund_account_id": r.fund_account_id, "direction": r.direction,
        "amount": float(r.amount or 0), "expense_category": r.expense_category,
        "counterparty": r.counterparty,
        "occur_date": r.occur_date.isoformat() if r.occur_date else None,
        "summary": r.summary, "source_type": r.source_type,
    } for r in rows]}


class ReceiptIn(BaseModel):
    order_id: int
    amount: float
    pay_method: str = "TELEGRAPHIC"  # ACCEPTANCE承兑/TELEGRAPHIC电汇/CASH现金
    company_id: Optional[int] = None  # 收款主体(承兑/电汇时选择:峰业精密机械/东莞加工厂)
    receipt_date: Optional[str] = None  # 收款日期(记账日,缺省当天)
    remark: Optional[str] = None


# 收款登记(仅财务,触发receipt.created核销应收,并按方式生成记账凭证)
@router.post("/receipts")
def create_receipt(body: ReceiptIn, user: User = Depends(require_role("FINANCE")),
                   db: Session = Depends(get_db)):
    o = db.query(Order).get(body.order_id)
    if not o:
        raise HTTPException(400, "订单不存在")
    # 收款方式映射记账科目: 承兑→应收票据1121 / 电汇→银行存款1002 / 现金→库存现金1001
    dm = {
        "ACCEPTANCE": {"code": "1121", "name": "应收票据"},
        "TELEGRAPHIC": {"code": "1002", "name": "银行存款"},
        "CASH": {"code": "1001", "name": "库存现金"},
    }
    pm = (body.pay_method or "TELEGRAPHIC").upper()
    if pm not in dm:
        raise HTTPException(400, "收款方式必须为 承兑/电汇/现金")
    # 校验收款金额不超过应收余额
    ar = db.query(FinanceDoc).filter(
        FinanceDoc.related_type == "ORDER",
        FinanceDoc.related_id == o.id,
        FinanceDoc.doc_type == "RECEIVABLE",
    ).first()
    if ar:
        remaining = float(ar.amount or 0) - float(ar.settled_amount or 0)
        if body.amount > remaining:
            raise HTTPException(400, f"收款金额{body.amount}超过应收余额{remaining}")
    # 单号生成: 用自增ID避免并发冲突
    max_doc = db.query(FinanceDoc).filter(FinanceDoc.doc_type == "RECEIPT").order_by(FinanceDoc.id.desc()).first()
    seq = (max_doc.id if max_doc else 0) + 1
    # 公司主体: 现金固定归东莞加工厂(小规模), 承兑/电汇按所选主体, 缺省用订单主体
    if pm == "CASH":
        small = db.query(Company).filter(Company.tax_type == "SMALL", Company.status == "ACTIVE").first()
        cid = small.id if small else (body.company_id or o.company_id)
    else:
        cid = body.company_id or o.company_id
    from datetime import datetime as _dt
    rdate = bjt_now()
    if body.receipt_date:
        try:
            rdate = _dt.strptime(str(body.receipt_date)[:10], "%Y-%m-%d")
        except Exception:
            pass
    rc = FinanceDoc(
        doc_no=f"RC-{bjt_now().strftime('%Y%m%d')}-{seq:04d}",
        doc_type="RECEIPT", status="SETTLED",
        related_type="ORDER", related_id=o.id,
        counterparty_type="CUSTOMER", counterparty_id=o.customer_id,
        amount=body.amount, settled_amount=body.amount,
        account_date=rdate, source_event="manual",
        company_id=cid, billing_type="SPECIAL_VAT" if cid and cid != (small.id if pm == "CASH" and small else None) else "CASH",
        remark=body.remark, extra={"pay_method": pm},
    )
    db.add(rc)
    db.flush()
    # 生成标准记账凭证: 借→收款账户(1121/1002/1001), 贷→应收账款1122
    _make_receipt_voucher(db, rc, dm[pm], body.amount, user)
    log_audit(db, user, "create", "finance_doc", rc.id, after={"doc_no": rc.doc_no, "pay_method": pm})
    db.flush()
    emit(db, "receipt.created", "finance_doc", rc.id, {"doc_no": rc.doc_no}, user)
    db.commit()
    return Resp.ok({"id": rc.id, "doc_no": rc.doc_no})


def _make_receipt_voucher(db: Session, rc: FinanceDoc, debit: dict, amount: float, user: User):
    """收款生成记账凭证: 借:收款账户, 贷:应收账款; 科目缺则自动补建"""
    from decimal import Decimal
    debit_acc = db.query(Account).filter(Account.code == debit["code"]).first()
    credit_acc = db.query(Account).filter(Account.code == "1122").first()
    if not debit_acc:
        debit_acc = Account(code=debit["code"], name=debit["name"], type="ASSET",
                            direction="DEBIT", is_required=1, level=1, status="ACTIVE")
        db.add(debit_acc); db.flush()
    if not credit_acc:
        credit_acc = Account(code="1122", name="应收账款", type="ASSET",
                             direction="DEBIT", is_required=1, level=1, status="ACTIVE")
        db.add(credit_acc); db.flush()
    amt = Decimal(str(amount))
    from app.core.voucher_service import create_voucher, post_voucher
    voucher = create_voucher(db, {
        "period": bjt_now().strftime("%Y-%m"),
        "voucher_date": bjt_now(),
        "summary": f"收款-{rc.doc_no}-{debit['name']}",
        "entries": [
            {"account_id": debit_acc.id, "summary": f"收款-{rc.doc_no}", "debit": amt, "credit": 0,
             "aux_type": "CUSTOMER", "aux_id": rc.counterparty_id, "aux_name": rc.counterparty_name},
            {"account_id": credit_acc.id, "summary": f"收款-{rc.doc_no}", "debit": 0, "credit": amt,
             "aux_type": "CUSTOMER", "aux_id": rc.counterparty_id, "aux_name": rc.counterparty_name},
        ],
    }, creator_id=user.id)
    post_voucher(db, voucher.id)
    db.add(FinanceItem(finance_doc_id=rc.id, account_id=debit_acc.id, account_code=debit_acc.code,
                       debit=amt, credit=0, remark="收款码"))
    db.add(FinanceItem(finance_doc_id=rc.id, account_id=credit_acc.id, account_code=credit_acc.code,
                       debit=0, credit=amt, remark="应收账款对冲"))


def _fund_ledger_account(db: Session, fa) -> Account:
    """资金账户→记账科目: 现金→1001 / 银行→1002 / 承兑→1121(缺则自动补建)"""
    m = {"CASH": ("1001", "库存现金"), "BANK": ("1002", "银行存款"), "ACCEPTANCE": ("1121", "应收票据")}
    code, name = m.get((fa.account_type or "BANK").upper(), ("1002", "银行存款"))
    acc = db.query(Account).filter(Account.code == code).first()
    if not acc:
        acc = Account(code=code, name=name, type="ASSET", direction="DEBIT",
                      is_required=1, level=1, status="ACTIVE")
        db.add(acc)
        db.flush()
    return acc


class TransferIn(BaseModel):
    from_account_id: int
    to_account_id: int
    amount: float
    occur_date: Optional[str] = None
    summary: Optional[str] = None


@router.post("/transfer")
def create_transfer(body: TransferIn, user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                    db: Session = Depends(get_db)):
    """账户互转(公账提现/现金存银行等): 成对流水不计收支 + 自动凭证"""
    if body.from_account_id == body.to_account_id:
        raise HTTPException(400, "转出和转入账户不能相同")
    if body.amount <= 0:
        raise HTTPException(400, "金额必须大于0")
    fa_from = db.query(FundAccount).get(body.from_account_id)
    fa_to = db.query(FundAccount).get(body.to_account_id)
    if not fa_from or not fa_to:
        raise HTTPException(400, "账户不存在")
    from datetime import datetime as _dt
    odate = bjt_now()
    if body.occur_date:
        try:
            odate = _dt.strptime(str(body.occur_date)[:10], "%Y-%m-%d")
        except Exception:
            pass
    summary = body.summary or f"{fa_from.name}→{fa_to.name}"
    db.add(FundFlow(fund_account_id=fa_from.id, direction="OUT", amount=body.amount,
                    expense_category="账户互转", counterparty=fa_to.name, occur_date=odate,
                    summary=f"转出-{summary}", source_type="TRANSFER"))
    db.add(FundFlow(fund_account_id=fa_to.id, direction="IN", amount=body.amount,
                    expense_category="账户互转", counterparty=fa_from.name, occur_date=odate,
                    summary=f"转入-{summary}", source_type="TRANSFER"))
    # 自动凭证: 借 转入方科目 贷 转出方科目
    from app.core.voucher_service import create_voucher, post_voucher
    acc_in = _fund_ledger_account(db, fa_to)
    acc_out = _fund_ledger_account(db, fa_from)
    voucher = create_voucher(db, {
        "period": odate.strftime("%Y-%m"), "voucher_date": odate,
        "summary": f"账户互转-{summary}",
        "entries": [
            {"account_id": acc_in.id, "summary": summary, "debit": body.amount, "credit": 0},
            {"account_id": acc_out.id, "summary": summary, "debit": 0, "credit": body.amount},
        ],
    }, creator_id=user.id)
    post_voucher(db, voucher.id)
    log_audit(db, user, "create", "fund_transfer", fa_from.id,
              after={"from": fa_from.name, "to": fa_to.name, "amount": body.amount})
    db.commit()
    return Resp.ok({"voucher_no": voucher.voucher_no})


class PaymentIn(BaseModel):
    doc_id: int            # 应付单ID
    fund_account_id: int   # 付款资金账户
    amount: float
    pay_date: Optional[str] = None
    remark: Optional[str] = None


@router.post("/payments")
def create_payment(body: PaymentIn, user: User = Depends(require_role("FINANCE")),
                   db: Session = Depends(get_db)):
    """付款核销应付单(对称收款): 核减应付+资金流水+自动凭证"""
    doc = db.query(FinanceDoc).get(body.doc_id)
    if not doc or doc.doc_type != "PAYABLE":
        raise HTTPException(400, "应付单不存在")
    if body.amount <= 0:
        raise HTTPException(400, "金额必须大于0")
    remaining = float(doc.amount or 0) - float(doc.settled_amount or 0)
    if body.amount > remaining + 0.005:
        raise HTTPException(400, f"付款金额{body.amount}超过应付余额{round(remaining, 2)}")
    fa = db.query(FundAccount).get(body.fund_account_id)
    if not fa:
        raise HTTPException(400, "资金账户不存在")
    from datetime import datetime as _dt
    pdate = bjt_now()
    if body.pay_date:
        try:
            pdate = _dt.strptime(str(body.pay_date)[:10], "%Y-%m-%d")
        except Exception:
            pass
    # 1. 核销应付
    doc.settled_amount = float(doc.settled_amount or 0) + body.amount
    if doc.settled_amount >= float(doc.amount or 0) - 0.005:
        doc.status = "SETTLED"
        doc.settled_at = pdate
    # 2. 资金流水(货款支付: 不进七类期间费用, 仅影响余额)
    db.add(FundFlow(fund_account_id=fa.id, direction="OUT", amount=body.amount,
                    expense_category="货款支付", counterparty=doc.counterparty_name,
                    occur_date=pdate, summary=f"付款-{doc.doc_no}" + (f"({body.remark})" if body.remark else ""),
                    source_type="PAYMENT", source_id=doc.id))
    # 3. 自动凭证: 借 应付账款2202 贷 资金科目
    from app.core.voucher_service import create_voucher, post_voucher
    ap_acc = db.query(Account).filter(Account.code == "2202").first()
    if not ap_acc:
        ap_acc = Account(code="2202", name="应付账款", type="LIABILITY", direction="CREDIT",
                         is_required=1, level=1, status="ACTIVE")
        db.add(ap_acc)
        db.flush()
    fund_acc = _fund_ledger_account(db, fa)
    voucher = create_voucher(db, {
        "period": pdate.strftime("%Y-%m"), "voucher_date": pdate,
        "summary": f"付款-{doc.doc_no}-{doc.counterparty_name or ''}",
        "entries": [
            {"account_id": ap_acc.id, "summary": f"付款-{doc.doc_no}", "debit": body.amount, "credit": 0,
             "aux_type": doc.counterparty_type, "aux_id": doc.counterparty_id, "aux_name": doc.counterparty_name},
            {"account_id": fund_acc.id, "summary": f"付款-{doc.doc_no}", "debit": 0, "credit": body.amount,
             "aux_type": doc.counterparty_type, "aux_id": doc.counterparty_id, "aux_name": doc.counterparty_name},
        ],
    }, creator_id=user.id)
    post_voucher(db, voucher.id)
    log_audit(db, user, "create", "payment", doc.id,
              after={"doc_no": doc.doc_no, "amount": body.amount, "account": fa.name})
    db.commit()
    return Resp.ok({"doc_no": doc.doc_no, "settled": doc.settled_amount, "voucher_no": voucher.voucher_no})


@router.get("/fund-accounts")
def list_fund_accounts(user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                       db: Session = Depends(get_db)):
    """资金账户下拉(付款/转账用): 含实时余额"""
    from app.models.fund import FundAccount, FundFlow
    from sqlalchemy import func, case
    accs = db.query(FundAccount).order_by(FundAccount.id).all()
    items = []
    for a in accs:
        bal = db.query(func.coalesce(func.sum(
            case((FundFlow.direction == "IN", FundFlow.amount), else_=-FundFlow.amount)), 0)) \
            .filter(FundFlow.fund_account_id == a.id).scalar()
        items.append({"id": a.id, "name": a.name, "account_type": a.account_type,
                      "balance": round(float(bal or 0), 2)})
    return Resp.ok(items)


@router.get("/docs")
def list_docs(doc_type: Optional[str] = None, status: Optional[str] = None,
              keyword: Optional[str] = None,
              date_from: Optional[str] = None, date_to: Optional[str] = None,
              customer_id: Optional[int] = None,
              page: int = 1, size: int = 20,
              user: User = Depends(require_role("FINANCE", "ADMIN", "GM")), db: Session = Depends(get_db)):
    q = db.query(FinanceDoc)
    if doc_type:
        q = q.filter(FinanceDoc.doc_type == doc_type)
    if status:
        q = q.filter(FinanceDoc.status == status)
    if customer_id:
        q = q.filter(FinanceDoc.counterparty_id == customer_id)
    if date_from:
        q = q.filter(FinanceDoc.account_date >= date_from)
    if date_to:
        q = q.filter(FinanceDoc.account_date <= date_to)
    if keyword:
        kw = keyword.strip()
        if kw:
            from sqlalchemy import or_
            # 先按单据号/摘要匹配
            q = q.filter(or_(FinanceDoc.doc_no.ilike(f"%{kw}%"),
                             FinanceDoc.counterparty_name.ilike(f"%{kw}%"),
                             FinanceDoc.source_event.ilike(f"%{kw}%"),
                             FinanceDoc.remark.ilike(f"%{kw}%")))
    total = q.count()
    rows = q.order_by(FinanceDoc.id.desc()).offset((page - 1) * size).limit(size).all()
    # 客户名存在 counterparty_name(单据自带), 无需额外Join; 但订单客户名要从Order补
    from app.models.order import Order
    from app.models.customer import Customer
    oid_map = {o.id: o for o in db.query(Order).filter(Order.id.in_([r.related_id for r in rows if r.related_type == "ORDER" and r.related_id])).all()}
    cid_map = {c.id: c for c in db.query(Customer).filter(Customer.id.in_([r.counterparty_id for r in rows if r.counterparty_id])).all()}
    # 订单维度整体应收未核销
    from sqlalchemy import func
    order_ids = list({r.related_id for r in rows if r.related_type == "ORDER" and r.related_id})
    ar_map = {}
    if order_ids:
        for oid, amt, settled in db.query(
            FinanceDoc.related_id,
            func.coalesce(func.sum(FinanceDoc.amount), 0),
            func.coalesce(func.sum(FinanceDoc.settled_amount), 0),
        ).filter(FinanceDoc.doc_type == "RECEIVABLE", FinanceDoc.related_type == "ORDER",
                 FinanceDoc.related_id.in_(order_ids)).group_by(FinanceDoc.related_id).all():
            ar_map[oid] = float(amt) - float(settled)
    return {"code": 0, "total": total, "data": [{
        "id": d.id, "doc_no": d.doc_no, "doc_type": d.doc_type, "status": d.status,
        "related_type": d.related_type, "related_id": d.related_id,
        "order_id": d.related_id if d.related_type == "ORDER" else None,
        "order_no": (oid_map.get(d.related_id).order_no if d.related_type == "ORDER" and d.related_id in oid_map else None),
        "customer_name": (cid_map.get(d.counterparty_id).name if d.counterparty_id in cid_map else None),
        "counterparty_name": d.counterparty_name,
        "amount": float(d.amount or 0), "settled_amount": float(d.settled_amount or 0),
        "order_ar_unsettled": (round(ar_map.get(d.related_id, 0), 2) if d.related_type == "ORDER" else None),
        "account_date": d.account_date.isoformat() if d.account_date else None,
        "due_date": d.due_date.isoformat() if d.due_date else None,
        "source_event": d.source_event,
        "remark": d.remark,
        "pay_method": (d.extra or {}).get("pay_method") if isinstance(d.extra, dict) else None,
    } for d in rows]}


@router.get("/accounts")
def accounts(user: User = Depends(require_role("FINANCE", "ADMIN")), db: Session = Depends(get_db)):
    rows = db.query(Account).filter(Account.status == "ACTIVE").order_by(Account.code).all()
    return {"code": 0, "data": [{
        "id": a.id, "code": a.code, "name": a.name, "type": a.type,
        "direction": a.direction, "is_required": a.is_required, "level": a.level,
    } for a in rows]}


class AccountIn(BaseModel):
    code: str
    name: str
    type: str
    direction: str = "DEBIT"
    parent_code: Optional[str] = None
    is_required: int = 0
    level: int = 1


@router.post("/accounts")
def create_account(body: AccountIn, user: User = Depends(require_role("FINANCE", "ADMIN")),
                    db: Session = Depends(get_db)):
    exists = db.query(Account).filter(Account.code == body.code).first()
    if exists:
        raise HTTPException(400, "科目编码已存在")
    acc = Account(
        code=body.code, name=body.name, type=body.type,
        direction=body.direction, parent_code=body.parent_code,
        is_required=body.is_required, level=body.level, status="ACTIVE"
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    log_audit(db, user, "create", "account", acc.id, after={"code": acc.code, "name": acc.name})
    return {"code": 0, "data": {"id": acc.id, "code": acc.code, "name": acc.name}}


@router.put("/accounts/{account_id}")
def update_account(account_id: int, body: AccountIn,
                   user: User = Depends(require_role("FINANCE", "ADMIN")),
                   db: Session = Depends(get_db)):
    acc = db.query(Account).filter(Account.id == account_id).first()
    if not acc:
        raise HTTPException(404, "科目不存在")
    old = {"code": acc.code, "name": acc.name}
    acc.name = body.name
    acc.type = body.type
    acc.direction = body.direction
    acc.parent_code = body.parent_code
    acc.is_required = body.is_required
    acc.level = body.level
    log_audit(db, user, "update", "account", account_id, before=old,
              after={"code": acc.code, "name": acc.name})
    db.commit()
    return {"code": 0}


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int,
                   user: User = Depends(require_role("FINANCE", "ADMIN")),
                   db: Session = Depends(get_db)):
    acc = db.query(Account).filter(Account.id == account_id).first()
    if not acc:
        raise HTTPException(404, "科目不存在")
    if acc.is_required:
        raise HTTPException(400, "必填科目不能删除")
    # F4: 检查科目是否已有余额/被凭证引用, 有则禁止删除(置INACTIVE)
    from app.models.voucher import AccountBalance, VoucherEntry
    has_balance = db.query(AccountBalance).filter(AccountBalance.account_id == account_id).first()
    if has_balance and (has_balance.debit_amount or has_balance.credit_amount
                        or has_balance.opening_debit or has_balance.opening_credit
                        or has_balance.closing_debit or has_balance.closing_credit):
        raise HTTPException(400, "该科目存在余额, 不能删除")
    has_ref = db.query(VoucherEntry).filter(VoucherEntry.account_id == account_id).first()
    if has_ref:
        raise HTTPException(400, "该科目已被凭证引用, 不能删除")
    acc.status = "INACTIVE"
    log_audit(db, user, "delete", "account", account_id,
              before={"code": acc.code, "name": acc.name})
    db.commit()
    return {"code": 0}


@router.get("/accounts/{account_id}")
def get_account(account_id: int,
                user: User = Depends(require_role("FINANCE", "ADMIN")),
                db: Session = Depends(get_db)):
    acc = db.query(Account).filter(Account.id == account_id).first()
    if not acc:
        raise HTTPException(404, "科目不存在")
    return {"code": 0, "data": {
        "id": acc.id, "code": acc.code, "name": acc.name, "type": acc.type,
        "direction": acc.direction, "parent_code": acc.parent_code,
        "is_required": acc.is_required, "level": acc.level, "status": acc.status
    }}


# 工单成本明细
@router.get("/work-order-costs/{wid}")
def wo_costs(wid: int, user: User = Depends(require_role("FINANCE", "MANAGER", "ADMIN")), db: Session = Depends(get_db)):
    rows = db.query(WorkOrderCost).filter(WorkOrderCost.work_order_id == wid).all()
    by_type = {}
    total = 0
    for c in rows:
        by_type[c.cost_type] = by_type.get(c.cost_type, 0) + float(c.amount or 0)
        total += float(c.amount or 0)
    return {"code": 0, "data": {
        "work_order_id": wid, "total_cost": total, "breakdown": by_type,
        "details": [{"cost_type": c.cost_type, "amount": float(c.amount or 0),
                     "source_doc_type": c.source_doc_type, "occurred_at": c.occurred_at.isoformat() if c.occurred_at else None}
                    for c in rows],
    }}


# 订单利润分析
@router.get("/profit/order/{oid}")
def order_profit(oid: int, user: User = Depends(require_role("FINANCE", "ADMIN", "GM")), db: Session = Depends(get_db)):
    o = db.query(Order).get(oid)
    if not o:
        raise HTTPException(404, "订单不存在")
    wo_ids = [w.id for w in db.query(WorkOrder).filter(WorkOrder.order_id == oid).all()]
    total_cost = 0
    breakdown = {}
    if wo_ids:
        costs = db.query(WorkOrderCost).filter(WorkOrderCost.work_order_id.in_(wo_ids)).all()
        for c in costs:
            breakdown[c.cost_type] = breakdown.get(c.cost_type, 0) + float(c.amount or 0)
            total_cost += float(c.amount or 0)
    revenue = float(o.total_amount or 0)
    profit = revenue - total_cost
    margin = round(profit / revenue * 100, 2) if revenue else 0
    return {"code": 0, "data": {
        "order_id": oid, "order_no": o.order_no, "revenue": revenue,
        "cost": total_cost, "cost_breakdown": breakdown,
        "profit": profit, "gross_margin_pct": margin,
    }}


# 应收账龄
@router.get("/receivables/aging")
def ar_aging(user: User = Depends(require_role("FINANCE", "ADMIN", "GM")), db: Session = Depends(get_db)):
    rows = db.query(FinanceDoc).filter(
        FinanceDoc.doc_type == "RECEIVABLE",
        FinanceDoc.status.in_(["OPEN", "DRAFT"])
    ).all()
    now = bjt_now()
    aging = {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
    for r in rows:
        remaining = float(r.amount or 0) - float(r.settled_amount or 0)
        if remaining <= 0:
            continue
        days = (now - (r.account_date or now)).days
        if days <= 30:
            aging["0-30"] += remaining
        elif days <= 60:
            aging["31-60"] += remaining
        elif days <= 90:
            aging["61-90"] += remaining
        else:
            aging["90+"] += remaining
    return {"code": 0, "data": aging}


@router.get("/receivable-remind")
def receivable_remind(user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                      db: Session = Depends(get_db)):
    """收款提醒: 15天内即将到期 + 已逾期(应收未核销)"""
    from datetime import timedelta
    rows = db.query(FinanceDoc).filter(
        FinanceDoc.doc_type == "RECEIVABLE",
        FinanceDoc.status.in_(["OPEN", "DRAFT"])
    ).all()
    now = bjt_now()
    horizon = now + timedelta(days=15)
    due_soon, overdue = [], []
    total_amount = 0.0
    for r in rows:
        remaining = float(r.amount or 0) - float(r.settled_amount or 0)
        if remaining <= 0.005:
            continue
        if not r.due_date:
            continue
        item = {
            "id": r.id, "doc_no": r.doc_no, "amount": float(r.amount or 0),
            "settled_amount": float(r.settled_amount or 0),
            "remaining": round(remaining, 2),
            "due_date": r.due_date.strftime("%Y-%m-%d") if r.due_date else None,
            "counterparty_name": r.counterparty_name,
            "related_type": r.related_type, "related_id": r.related_id,
            "days_to_due": (r.due_date - now).days,
            "status": r.status, "remark": r.remark,
        }
        if r.due_date < now:
            item["overdue_days"] = (now - r.due_date).days
            overdue.append(item)
            total_amount += remaining
        elif r.due_date <= horizon:
            due_soon.append(item)
            total_amount += remaining
    due_soon.sort(key=lambda x: x["due_date"])
    overdue.sort(key=lambda x: x["due_date"])
    return Resp.ok({
        "due_soon": due_soon,
        "overdue": overdue,
        "due_soon_count": len(due_soon),
        "overdue_count": len(overdue),
        "total_amount": round(total_amount, 2),
        "now": now.isoformat(),
        "horizon": horizon.strftime("%Y-%m-%d"),
    })


# ============ 退货/返工申请(财务单据页发起) ============
SALES_ROLES = ("SALES", "SALES_VICE_MANAGER", "SALES_MANAGER")
"""销售阵营角色(业务变更类动作只能由销售线发起)"""


def _order_ar_stats(db, order_id: int):
    """订单维度应收合计/已核销/未核销(用于发起时状态判断)"""
    from sqlalchemy import func
    row = db.query(
        func.coalesce(func.sum(FinanceDoc.amount), 0),
        func.coalesce(func.sum(FinanceDoc.settled_amount), 0),
    ).filter(FinanceDoc.doc_type == "RECEIVABLE",
             FinanceDoc.related_type == "ORDER",
             FinanceDoc.related_id == order_id).first() or (0, 0)
    total, settled = float(row[0] or 0), float(row[1] or 0)
    return total, settled, round(total - settled, 2)


class ReturnIn(BaseModel):
    order_id: int
    amount: float  # 退货金额(冲减应收)
    reason: str
    remark: Optional[str] = None


class ReworkIn(BaseModel):
    order_id: int
    amount: float  # 返工成本
    reason: str
    remark: Optional[str] = None


def _get_or_create_account(db, code: str, name: str, type_: str = "ASSET", direction: str = "DEBIT"):
    a = db.query(Account).filter(Account.code == code).first()
    if a: return a
    a = Account(code=code, name=name, type=type_, direction=direction, is_required=0, level=1, status="ACTIVE")
    db.add(a); db.flush()
    return a


def _ensure_flow_default(db, biz_type: str):
    """若无该类型流程定义,建一个默认审批流: 发起→GM终审 (兜底保证可用)"""
    from app.models.approval import FlowDefinition
    fd = db.query(FlowDefinition).filter(
        FlowDefinition.biz_type == biz_type, FlowDefinition.status == "ACTIVE"
    ).first()
    if fd:
        return fd
    fd = FlowDefinition(
        name="退货申请" if biz_type == "RETURN" else "返工申请",
        biz_type=biz_type,
        nodes=[
            {"seq": 1, "name": "开始", "type": "start"},
            {"seq": 2, "name": "总经理终审", "type": "approve", "approver_role": "GM"},
            {"seq": 3, "name": "结束", "type": "end"},
        ],
        status="ACTIVE", version=1,
    )
    db.add(fd); db.flush()
    return fd


@router.post("/returns")
def create_return(body: ReturnIn, user: User = Depends(require_role(*SALES_ROLES)),
                  db: Session = Depends(get_db)):
    """销售线发起退货申请(财务/运营不能代发起,保持责任链)"""
    from app.models.sales import ReturnRequest
    from app.models.customer import Customer
    from app.core.number_gen import generate_number
    o = db.query(Order).get(body.order_id)
    if not o:
        raise HTTPException(400, "订单不存在")
    if body.amount <= 0:
        raise HTTPException(400, "退货金额必须大于0")
    ar_total, ar_settled, ar_unsettled = _order_ar_stats(db, o.id)
    if ar_total <= 0:
        raise HTTPException(400, "该订单尚无应收记录,不能申请退货")
    # 已全额收款 → 不允许退货冲应收(必须先做红字发票/退款流程)
    if ar_settled >= ar_total - 0.001:
        raise HTTPException(400, f"该订单已收款{ar_settled:.2f}(应收{ar_total:.2f}),已收齐,不能申请退货冲减应收")
    # 未核销余额 < 退货金额 → 不允许退货超未核销
    if body.amount > ar_unsettled + 0.001:
        raise HTTPException(400, f"退货金额{body.amount:.2f}超过订单应收未核销余额{ar_unsettled:.2f}")
    if db.query(ReturnRequest).filter(ReturnRequest.order_id == body.order_id,
                                      ReturnRequest.status == "PENDING").first():
        raise HTTPException(400, "该订单已有待审批的退货申请")
    r = ReturnRequest(rt_no=generate_number("RETURN", db=db), order_id=o.id,
                      customer_id=o.customer_id, amount=body.amount, reason=body.reason,
                      remark=body.remark, status="PENDING", initiator_user_id=user.id)
    db.add(r); db.flush()
    fd = _ensure_flow_default(db, "RETURN")
    inst = start_flow(db, "RETURN", r.id, user,
                      biz_data={"reason": body.reason, "amount": body.amount,
                                "order_no": o.order_no, "order_id": o.id,
                                "ar_total": ar_total, "ar_settled": ar_settled, "ar_unsettled": ar_unsettled})
    if inst:
        r.approval_instance_id = inst.id
    log_audit(db, user, "create", "return_request", r.id, after={"rt_no": r.rt_no, "amount": body.amount})
    db.commit()
    return Resp.ok({"id": r.id, "rt_no": r.rt_no})


@router.post("/reworks")
def create_rework(body: ReworkIn, user: User = Depends(require_role(*SALES_ROLES)),
                  db: Session = Depends(get_db)):
    """销售线发起返工申请(返工影响客诉交付,销售是责任人)"""
    from app.models.sales import ReworkRequest
    from app.core.number_gen import generate_number
    o = db.query(Order).get(body.order_id)
    if not o:
        raise HTTPException(400, "订单不存在")
    if body.amount <= 0:
        raise HTTPException(400, "返工成本必须大于0")
    # 应收状态不阻塞返工(即便是已收款,客户要求返工也得返工)——返工加成本,不直接碰已收款
    if db.query(ReworkRequest).filter(ReworkRequest.order_id == body.order_id,
                                      ReworkRequest.status == "PENDING").first():
        raise HTTPException(400, "该订单已有待审批的返工申请")
    r = ReworkRequest(rw_no=generate_number("REWORK", db=db), order_id=o.id,
                      customer_id=o.customer_id, amount=body.amount, reason=body.reason,
                      remark=body.remark, status="PENDING", initiator_user_id=user.id)
    db.add(r); db.flush()
    fd = _ensure_flow_default(db, "REWORK")
    inst = start_flow(db, "REWORK", r.id, user,
                      biz_data={"reason": body.reason, "amount": body.amount,
                                "order_no": o.order_no, "order_id": o.id})
    if inst:
        r.approval_instance_id = inst.id
    log_audit(db, user, "create", "rework_request", r.id, after={"rw_no": r.rw_no, "amount": body.amount})
    db.commit()
    return Resp.ok({"id": r.id, "rw_no": r.rw_no})


@router.get("/returns")
def list_returns(user: User = Depends(require_role("FINANCE")), db: Session = Depends(get_db)):
    from app.models.sales import ReturnRequest
    rows = db.query(ReturnRequest).order_by(ReturnRequest.id.desc()).all()
    out = []
    for r in rows:
        o = db.query(Order).get(r.order_id)
        out.append({"id": r.id, "rt_no": r.rt_no, "order_id": r.order_id,
                    "order_no": o.order_no if o else "-", "amount": float(r.amount or 0),
                    "reason": r.reason, "status": r.status, "remark": r.remark,
                    "created_at": bjt_now().__str__()})
    return Resp.ok(out)


@router.get("/reworks")
def list_reworks(user: User = Depends(require_role("FINANCE")), db: Session = Depends(get_db)):
    from app.models.sales import ReworkRequest
    rows = db.query(ReworkRequest).order_by(ReworkRequest.id.desc()).all()
    out = []
    for r in rows:
        o = db.query(Order).get(r.order_id)
        out.append({"id": r.id, "rw_no": r.rw_no, "order_id": r.order_id,
                    "order_no": o.order_no if o else "-", "amount": float(r.amount or 0),
                    "reason": r.reason, "status": r.status, "remark": r.remark,
                    "created_at": bjt_now().__str__()})
    return Resp.ok(out)


# ==========================================================
#  矩阵主表 / 总经理速览 / 行展开 / 单元格明细
#  权限: FINANCE / GM / ADMIN
# ==========================================================

def _bucket_by(granularity: str, dt: datetime, year: int) -> str:
    """把 datetime 按粒度映射成矩阵列 key。
    年→'Q1'.. 季→'01'..'12'  周→'W01'..'W53'  日→'08-22'
    """
    if granularity == "year":
        q = (dt.month - 1) // 3 + 1
        return f"Q{q}"
    if granularity == "quarter":
        return f"{dt.month:02d}"
    if granularity == "month":
        return f"{dt.month:02d}"
    if granularity == "week":
        iso = dt.isocalendar()
        return f"W{iso[1]:02d}"
    # day
    return f"{dt.month:02d}-{dt.day:02d}"


def _build_columns_in_range(granularity: str, s: datetime, e: datetime):
    """在时间窗 [s,e) 内按粒度切片生成列（key格式与 _bucket_by 完全一致）。
    年/季→'Q1'  月→'01'  周→'W01'  日→'08-22'"""
    from datetime import timedelta as td
    keys, ranges = [], {}
    if granularity in ("year", "quarter"):
        # 季度切片
        cur = datetime(s.year, ((s.month - 1) // 3) * 3 + 1, 1)
        while cur < e:
            q = (cur.month - 1) // 3 + 1
            em = cur.month + 3
            ey, em2 = (cur.year, em) if em <= 12 else (cur.year + 1, em - 12)
            ce = datetime(ey, em2, 1)
            k = f"Q{q}"
            if k not in ranges:
                keys.append(k)
                ranges[k] = (max(cur, s), min(ce, e))
            cur = ce
    elif granularity == "month":
        cur = datetime(s.year, s.month, 1)
        while cur < e:
            em = cur.month + 1
            ey, em2 = (cur.year, em) if em <= 12 else (cur.year + 1, 1)
            ce = datetime(ey, em2, 1)
            k = f"{cur.month:02d}"
            if k not in ranges:
                keys.append(k)
                ranges[k] = (max(cur, s), min(ce, e))
            cur = ce
    elif granularity == "week":
        cur = s - td(days=s.weekday())  # 窗口首日所在周的周一
        while cur < e:
            ce = cur + td(days=7)
            k = f"W{cur.isocalendar()[1]:02d}"
            if k not in ranges:
                keys.append(k)
                ranges[k] = (max(cur, s), min(ce, e))
            cur = ce
    else:  # day
        # e 可能带时分秒（now+1d），日切片截止到今天（含），不出"明天"空列
        today0 = bjt_now().replace(hour=0, minute=0, second=0, microsecond=0)
        end_d = min(e, today0 + td(days=1))
        cur = s
        while cur < end_d:
            k = f"{cur.month:02d}-{cur.day:02d}"
            keys.append(k)
            ranges[k] = (cur, cur + td(days=1))
            cur += td(days=1)
    return keys, ranges


def _build_column_keys(granularity: str, year: int):
    """返回有序列key列表 + 每列的 (start_dt, end_dt) 时间范围。"""
    from datetime import timedelta as td
    keys = []
    ranges = {}
    if granularity == "year":
        for q in range(1, 5):
            sm = (q - 1) * 3 + 1
            s = datetime(year, sm, 1)
            em = sm + 2
            e_y, e_m = (year, em + 1) if em < 12 else (year + 1, 1)
            e = datetime(e_y, e_m, 1)
            k = f"Q{q}"
            keys.append(k)
            ranges[k] = (s, e)
    elif granularity == "quarter":
        # 展示 12 个月，方便季度报表
        for m in range(1, 13):
            s = datetime(year, m, 1)
            ey, em = (year, m + 1) if m < 12 else (year + 1, 1)
            e = datetime(ey, em, 1)
            k = f"{m:02d}"
            keys.append(k)
            ranges[k] = (s, e)
    elif granularity == "month":
        for m in range(1, 13):
            s = datetime(year, m, 1)
            ey, em = (year, m + 1) if m < 12 else (year + 1, 1)
            e = datetime(ey, em, 1)
            k = f"{m:02d}"
            keys.append(k)
            ranges[k] = (s, e)
    elif granularity == "week":
        # 一年 ISO 周：从该年 1月1日所在周的周一开始
        d = datetime(year, 1, 1)
        # 第一个周一是 Jan 1 的 weekday == Sunday?
        import datetime as _dt
        jan1_wd = d.weekday()
        first_mon = d - td(days=jan1_wd) if jan1_wd != 6 else d + td(days=1)
        cur = first_mon
        while cur.year <= year:
            iso = cur.isocalendar()
            if iso[0] != year and (cur + td(days=6)).year != year:
                cur += td(days=7)
                continue
            k = f"W{iso[1]:02d}"
            if k not in ranges:
                keys.append(k)
                ranges[k] = (cur, cur + td(days=7))
            cur += td(days=7)
    else:  # day
        today = bjt_now()
        d0 = datetime(year, 1, 1)
        end_day = datetime(year, 12, 31)
        if year == today.year:
            end_day = today
        cur = d0
        while cur <= end_day:
            k = f"{cur.month:02d}-{cur.day:02d}"
            keys.append(k)
            ranges[k] = (cur.replace(hour=0, minute=0, second=0),
                         cur.replace(hour=0, minute=0, second=0) + td(days=1))
            cur += td(days=1)
    return keys, ranges


def _fund_account_filter(company: Optional[int], view: str, db: Session):
    """按公司/资金视角过滤账户 → 返回 account_id 列表"""
    from app.models.fund import FundAccount
    q = db.query(FundAccount).filter(FundAccount.enabled == 1)
    accs = q.all()
    out = []
    for a in accs:
        # 公司过滤: None=全部; 0/NULL 表示集团级(现金),按规则归入全集团时才显示
        if company is not None:
            if a.company_id and a.company_id != company:
                continue
            # company_id=None/0 的集团现金: 公司过滤时不纳入单个公司(除非用户单独选现金)
            if (a.company_id is None or a.company_id == 0) and a.account_type != "CASH":
                continue
            if (a.company_id is None or a.company_id == 0) and a.account_type == "CASH" and company in (1, 2):
                # 现金在公司单独视角下也显示一半? 简化做法: 公司单独视角不显示集团现金
                # 但用户明确要求现金独立流向——单独公司视角也把现金带上
                pass
        # 资金视角过滤
        if view == "bank-no-acceptance" and a.account_type != "BANK":
            continue
        if view == "jx" and a.code != "JX-BANK":
            continue
        if view == "dg" and a.code != "DG-BANK":
            continue
        if view == "acceptance" and a.code != "ACCEPTANCE":
            continue
        if view == "cash" and a.code != "CASH":
            continue
        out.append(a)
    return out


EXPENSE_ORDER = ["工资", "融资成本", "气体", "耗材", "食堂", "佣金", "提成"]  # D行固定7项顺序


@router.get("/summary")
def finance_summary(year: Optional[int] = None, granularity: str = "month",
                    period: str = "month", start: Optional[str] = None, end: Optional[str] = None,
                    company: Optional[int] = None, view: str = "all",
                    user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                    db: Session = Depends(get_db)):
    """总经理速览条：当期利润、资金总、现金、应收逾期、应付7天到期"""
    from sqlalchemy import func, and_
    from app.models.fund import FundAccount, FundFlow
    from app.models.finance import FinanceDoc, FinanceItem, Account
    from app.models.voucher import AccountBalance, VoucherEntry
    now = bjt_now()
    if year is None:
        year = now.year
    # 周期范围（用于“本期利润/应收/应付”）
    s, e = _resolve_range(period, start, end)

    # 1. 资金总额 & 现金余额（到今天的实时）
    accounts = _fund_account_filter(company, view, db)
    aid_list = [a.id for a in accounts]
    fund_total = 0.0
    cash_balance = 0.0
    today_end = now + _timedelta(days=1)
    today_start = datetime(now.year, 1, 1)
    for a in accounts:
        # 截至今天前的累计净流
        inn = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
            FundFlow.fund_account_id == a.id, FundFlow.direction == 'IN',
            FundFlow.occur_date < today_end).scalar() or 0)
        out = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
            FundFlow.fund_account_id == a.id, FundFlow.direction == 'OUT',
            FundFlow.occur_date < today_end).scalar() or 0)
        bal = float(a.opening_balance or 0) + inn - out
        fund_total += bal
        if a.code == "CASH":
            cash_balance += bal

    # 2. 本期利润 = 本期营收 - 本期7项费用 (从FundFlow聚合)
    period_income = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
        FundFlow.fund_account_id.in_(aid_list), FundFlow.direction == 'IN',
        FundFlow.occur_date >= s, FundFlow.occur_date < e).scalar() or 0)
    period_exp = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
        FundFlow.fund_account_id.in_(aid_list), FundFlow.direction == 'OUT',
        FundFlow.occur_date >= s, FundFlow.occur_date < e).scalar() or 0)

    # 账套 6001 本期营收
    acc = db.query(Account).filter(Account.code == '6001').first()
    rev_6001 = 0.0
    if acc:
        periods = []
        cur = s
        while cur < e:
            periods.append(f"{cur.year}-{cur.month:02d}")
            cur = datetime(cur.year + (1 if cur.month == 12 else 0),
                           1 if cur.month == 12 else cur.month + 1, 1)
        rev_6001 = float(db.query(func.coalesce(func.sum(AccountBalance.credit_amount), 0)).filter(
            AccountBalance.account_id == acc.id,
            AccountBalance.period.in_(list(set(periods)))).scalar() or 0)

    # 3. 应收逾期 30天以上
    now_ts = now
    import datetime as _dt
    cutoff30 = now_ts - _timedelta(days=30)
    _unsettled = FinanceDoc.amount - FinanceDoc.settled_amount
    ar_overdue = float(db.query(func.coalesce(func.sum(_unsettled), 0)).filter(
        FinanceDoc.doc_type == 'RECEIVABLE', FinanceDoc.due_date < cutoff30,
        _unsettled > 0).scalar() or 0)
    # TOP3 逾期客户（直接用 counterparty_name）
    ar_rows = db.query(FinanceDoc).filter(
        FinanceDoc.doc_type == 'RECEIVABLE', FinanceDoc.due_date < cutoff30,
        _unsettled > 0).all()
    from collections import defaultdict
    cust_map = defaultdict(float)
    for fd in ar_rows:
        cust_map[fd.counterparty_name or "未知客户"] += float(fd.amount or 0) - float(fd.settled_amount or 0)
    ar_top3 = sorted(cust_map.items(), key=lambda x: -x[1])[:3]

    # 4. 应付 7天内到期
    cutoff7up = now_ts + _timedelta(days=7)
    ap_soon = float(db.query(func.coalesce(func.sum(_unsettled), 0)).filter(
        FinanceDoc.doc_type == 'PAYABLE', FinanceDoc.due_date >= now_ts,
        FinanceDoc.due_date <= cutoff7up,
        _unsettled > 0).scalar() or 0)

    # 同比/环比（简易）
    prev_s = s - (e - s)
    prev_income = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
        FundFlow.fund_account_id.in_(aid_list), FundFlow.direction == 'IN',
        FundFlow.occur_date >= prev_s, FundFlow.occur_date < s).scalar() or 0)
    prev_exp = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
        FundFlow.fund_account_id.in_(aid_list), FundFlow.direction == 'OUT',
        FundFlow.occur_date >= prev_s, FundFlow.occur_date < s).scalar() or 0)
    prev_profit = prev_income - prev_exp
    cur_profit = period_income - period_exp
    qoq = round((cur_profit - prev_profit) / prev_profit * 100, 1) if prev_profit else 0.0

    # 公账不含承兑余额
    bank_nacc_ids = [a.id for a in accounts if a.code in ('JX-BANK', 'DG-BANK')]
    bank_balance = 0.0
    for a in accounts:
        if a.id not in bank_nacc_ids:
            continue
        inn = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
            FundFlow.fund_account_id == a.id, FundFlow.direction == 'IN',
            FundFlow.occur_date < today_end).scalar() or 0)
        out = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
            FundFlow.fund_account_id == a.id, FundFlow.direction == 'OUT',
            FundFlow.occur_date < today_end).scalar() or 0)
        bank_balance += float(a.opening_balance or 0) + inn - out
    acc_ids_accept = [a.id for a in accounts if a.code == 'ACCEPTANCE']
    accept_balance = 0.0
    for a in accounts:
        if a.id not in acc_ids_accept:
            continue
        inn = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
            FundFlow.fund_account_id == a.id, FundFlow.direction == 'IN',
            FundFlow.occur_date < today_end).scalar() or 0)
        out = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
            FundFlow.fund_account_id == a.id, FundFlow.direction == 'OUT',
            FundFlow.occur_date < today_end).scalar() or 0)
        accept_balance += float(a.opening_balance or 0) + inn - out

    # 各账户实时余额
    acc_balances = []
    for a in accounts:
        inn = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
            FundFlow.fund_account_id == a.id, FundFlow.direction == 'IN',
            FundFlow.occur_date < today_end).scalar() or 0)
        out = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
            FundFlow.fund_account_id == a.id, FundFlow.direction == 'OUT',
            FundFlow.occur_date < today_end).scalar() or 0)
        bal = float(a.opening_balance or 0) + inn - out
        acc_balances.append({"code": a.code, "name": a.name, "balance": round(bal, 2)})

    return Resp.ok({
        "year": year, "granularity": granularity, "period": period,
        "range": {"start": s.isoformat(), "end": (e - _timedelta(days=1)).isoformat()},
        "fund_total": round(fund_total, 2),
        "cash_balance": round(cash_balance, 2),
        "bank_balance": round(bank_balance, 2),
        "acceptance_balance": round(accept_balance, 2),
        "account_balances": acc_balances,
        "period_income": round(period_income, 2),
        "period_expense": round(period_exp, 2),
        "period_profit": round(cur_profit, 2),
        "revenue_6001": round(rev_6001, 2),
        "profit_qoq": qoq,
        "ar_overdue_30d": round(ar_overdue, 2),
        "ar_overdue_top3": [{"name": k, "amount": round(v, 2)} for k, v in ar_top3],
        "ap_due_7d": round(ap_soon, 2),
    })


@router.get("/matrix")
def finance_matrix(year: Optional[int] = None, granularity: str = "month",
                   period: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
                   company: Optional[int] = None, view: str = "all",
                   user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                   db: Session = Depends(get_db)):
    """
    矩阵表：固定18行。
    粒度：year(季4列) / quarter(月12列) / month(月12列) / week(周) / day(日)
    周期：period+start/end 存在时 → 在窗口内按粒度切片（表随筛选起舞）；缺省 → 全年
    company: None=全部 / 1=机械 / 2=加工厂
    view: all / bank-no-acceptance / jx / dg / acceptance / cash
    """
    from sqlalchemy import func, and_
    from app.models.fund import FundAccount, FundFlow
    from app.models.finance import Account
    from app.models.voucher import AccountBalance
    from app.models.customer import Customer
    from collections import defaultdict

    now = bjt_now()
    if year is None:
        year = now.year
    if granularity not in ("year", "quarter", "month", "week", "day"):
        granularity = "month"

    # 周期窗口模式：表随筛选起舞
    win_mode = bool(period) or bool(start and end)
    if win_mode:
        if period == "year" and not (start and end):
            # 年度语义：尊重前端选定年（选2024就看2024全年）
            ws, we = datetime(year, 1, 1), datetime(year + 1, 1, 1)
        else:
            ws, we = _resolve_range(period or "year", start, end)
        keys, ranges = _build_columns_in_range(granularity, ws, we)
    else:
        ws, we = datetime(year, 1, 1), datetime(year + 1, 1, 1)
        keys, ranges = _build_column_keys(granularity, year)
    # 列数防爆自动降级：日>31列→升周；周>26列→升月（老板看不了几百列的表）
    if granularity == "day" and len(keys) > 31:
        granularity = "week"
        keys, ranges = _build_columns_in_range(granularity, ws, we)
    if granularity == "week" and len(keys) > 26:
        granularity = "month"
        keys, ranges = _build_columns_in_range(granularity, ws, we)
    accounts = _fund_account_filter(company, view, db)
    aid_list = [a.id for a in accounts]
    acc_by_code = {a.code: a for a in accounts}
    cmap = {c.id: c.name for c in db.query(Company).all()}

    # 预查询窗口内资金流水（含账户过滤）
    yr_start, yr_end = ws, we
    flows_all = db.query(FundFlow).filter(
        FundFlow.fund_account_id.in_(aid_list),
        FundFlow.occur_date >= yr_start, FundFlow.occur_date < yr_end).all()

    # 按列key+acc_id聚合 IN/OUT；按列key+expense_category聚合 OUT
    col_acc_in = defaultdict(lambda: defaultdict(float))
    col_tr_in = defaultdict(float)  # 账户互转转入(日/周粒度营收需剔除)
    col_acc_out = defaultdict(lambda: defaultdict(float))
    col_cat_out = defaultdict(lambda: defaultdict(float))
    # 用于行展开的辅助聚合
    col_income_by_src = defaultdict(lambda: defaultdict(float))  # 承兑/电汇/现金/其他
    col_income_by_cust = defaultdict(lambda: defaultdict(float))
    col_expense_by_cat_supplier = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))

    def _col_key(dt):
        return _bucket_by(granularity, dt, year)

    for f in flows_all:
        k = _col_key(f.occur_date)
        if k not in ranges:
            continue
        amt = float(f.amount or 0)
        is_transfer = (f.source_type == 'TRANSFER')  # 账户互转: 只影响余额, 不算营收/费用
        if f.direction == 'IN':
            col_acc_in[k][f.fund_account_id] += amt
            if is_transfer:
                col_tr_in[k] += amt
                continue
            # 收入来源（按账户类别）
            code = next((a.code for a in accounts if a.id == f.fund_account_id), 'UNK')
            src_map = {'ACCEPTANCE': '承兑回款', 'CASH': '现金收入', 'JX-BANK': '电汇-机械',
                       'DG-BANK': '电汇-加工'}
            src = src_map.get(code, '其他收入')
            col_income_by_src[k][src] += amt
            col_income_by_cust[k][f.counterparty or "其他"] += amt
        else:
            col_acc_out[k][f.fund_account_id] += amt
            if is_transfer:
                continue  # 互转支出不进费用类别
            cat = f.expense_category or "其他"
            col_cat_out[k][cat] += amt
            col_expense_by_cat_supplier[k][cat][f.counterparty or "其他供应商"] += amt

    # 账套 6001 每期营收（月/季粒度：权责发生制）
    # 日/周粒度账套无法精确切 → 切资金回款口径（老板看日表关心"今天回了多少钱"）
    acc_6001 = db.query(Account).filter(Account.code == '6001').first()
    col_rev = {k: 0.0 for k in keys}
    if granularity in ("day", "week"):
        for k in keys:
            col_rev[k] = round(sum(col_acc_in[k].values()) - col_tr_in.get(k, 0.0), 2)
    elif acc_6001:
        # 窗口可能跨年：查询窗口覆盖年份范围，映射后按 ranges 校验
        rows = db.query(AccountBalance).filter(
            AccountBalance.account_id == acc_6001.id,
            AccountBalance.period >= f"{ws.year}-01",
            AccountBalance.period <= f"{we.year}-12").all()
        for r in rows:
            try:
                ym = r.period.split("-")
                ry, m = int(ym[0]), int(ym[1])
            except Exception:
                continue
            # 把会计期间映射到列key（切片key与分桶key同构，in col_rev 即窗口内）
            s_d = datetime(ry, m, 1)
            k = _col_key(s_d)
            if k in col_rev:
                col_rev[k] += float(r.credit_amount or 0)

    # 构建矩阵数据：A(4) B(1) C(4) D(7) E(1) F(1) F2(1)
    rows_out = []

    # A. 上期期末余额（= 窗口起点前累计净流 + 开户初始余额）
    yr_prev_end = ws
    acc_open = {}
    for a in accounts:
        inn = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
            FundFlow.fund_account_id == a.id, FundFlow.direction == 'IN',
            FundFlow.occur_date < yr_prev_end).scalar() or 0)
        out = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
            FundFlow.fund_account_id == a.id, FundFlow.direction == 'OUT',
            FundFlow.occur_date < yr_prev_end).scalar() or 0)
        acc_open[a.id] = float(a.opening_balance or 0) + inn - out

    def _make_row(row_id, section, title, expandable=True, section_title=None):
        cells = {}
        for k in keys:
            cells[k] = {"v": 0.0, "label": "", "qv": 0.0, "qv_label": ""}
        r = {"id": row_id, "section": section, "title": title,
             "expandable": expandable, "section_title": section_title,
             "cells": cells, "total": 0.0}
        return r

    A_CODES = [("JX-BANK", "机械公账"), ("DG-BANK", "加工厂公账"), ("ACCEPTANCE", "承兑汇票"), ("CASH", "现金")]
    last_balance_per_acc = {code: acc_open.get(acc_by_code[code].id, 0.0)
                            if code in acc_by_code else 0.0
                            for code, _ in A_CODES}

    for code, name in A_CODES:
        row = _make_row(f"A:{code}", "A", name, section_title="各账户月初余额")
        a = acc_by_code.get(code)
        running = last_balance_per_acc[code]
        for i, k in enumerate(keys):
            # A段: 第1列=上年末; 之后各列=前一列期末余额
            if i == 0:
                row["cells"][k]["v"] = round(running, 2)
            else:
                prev_k = keys[i - 1]
                prev_in = col_acc_in[prev_k].get(a.id, 0.0) if a else 0.0
                prev_out = col_acc_out[prev_k].get(a.id, 0.0) if a else 0.0
                running = round(running + prev_in - prev_out, 2)
                row["cells"][k]["v"] = running
        row["total"] = row["cells"][keys[-1]]["v"]
        rows_out.append(row)

    # B. 本月营收（用账套6001 credit）
    row_b = _make_row("B:REV", "B", "本月营收", section_title="本期营收")
    for k in keys:
        row_b["cells"][k]["v"] = round(col_rev.get(k, 0.0), 2)
    row_b["total"] = round(sum(row_b["cells"][k]["v"] for k in keys), 2)
    rows_out.append(row_b)

    # C. 本期期末余额（=期初+本期入-本期出，逐列滚动）
    last_balance_per_acc = {code: acc_open.get(acc_by_code[code].id, 0.0)
                            if code in acc_by_code else 0.0
                            for code, _ in A_CODES}
    for code, name in A_CODES:
        row = _make_row(f"C:{code}", "C", name, section_title="各账户月末余额")
        a = acc_by_code.get(code)
        running = last_balance_per_acc[code]
        for k in keys:
            inn = col_acc_in[k].get(a.id, 0.0) if a else 0.0
            out = col_acc_out[k].get(a.id, 0.0) if a else 0.0
            running = round(running + inn - out, 2)
            row["cells"][k]["v"] = running
        row["total"] = row["cells"][keys[-1]]["v"]
        rows_out.append(row)

    # D. 7项费用
    for cat in EXPENSE_ORDER:
        row = _make_row(f"D:{cat}", "D", cat, section_title="费用明细")
        for k in keys:
            row["cells"][k]["v"] = round(col_cat_out[k].get(cat, 0.0), 2)
        row["total"] = round(sum(row["cells"][k]["v"] for k in keys), 2)
        rows_out.append(row)

    # E. 本期净增加 = B 营收 - Σ7项费用
    row_e = _make_row("E:NET", "E", "本期净增加", expandable=False, section_title="经营结果")
    for k in keys:
        exp7 = sum(col_cat_out[k].get(c, 0.0) for c in EXPENSE_ORDER)
        row_e["cells"][k]["v"] = round(col_rev.get(k, 0.0) - exp7, 2)
    row_e["total"] = round(sum(row_e["cells"][k]["v"] for k in keys), 2)
    rows_out.append(row_e)

    # F. 累计净增加 (E的前缀累积)
    row_f = _make_row("F:CUMNET", "F", "累计净增加", expandable=False, section_title="累计")
    cum = 0.0
    for k in keys:
        cum += row_e["cells"][k]["v"]
        row_f["cells"][k]["v"] = round(cum, 2)
    row_f["total"] = round(cum, 2)
    rows_out.append(row_f)

    # F2. 非经营项净额 = C累计净增(资金实增) − F累计净增
    # C各账户本年净增减合计 = ΣC.code[-1] − ΣA.code[0]
    total_c_increase = 0.0
    for code, _ in A_CODES:
        c_row = next((r for r in rows_out if r["id"] == f"C:{code}"), None)
        a_row = next((r for r in rows_out if r["id"] == f"A:{code}"), None)
        if c_row and a_row:
            total_c_increase += c_row["total"] - a_row["cells"][keys[0]]["v"]
    row_f2 = _make_row("F2:NONOP", "F2", "非经营项净额", expandable=False, section_title="勾稽校验")
    cum2 = 0.0
    cum_e = 0.0
    # 每列非经营项 = 列总资金净增 − E本期净增加
    for i, k in enumerate(keys):
        col_fund_delta = 0.0
        for code, _ in A_CODES:
            c_row = next((r for r in rows_out if r["id"] == f"C:{code}"), None)
            prev_bal = 0.0
            if i == 0:
                a_row = next((r for r in rows_out if r["id"] == f"A:{code}"), None)
                prev_bal = a_row["cells"][k]["v"] if a_row else 0.0
            else:
                c_row_p = next((r for r in rows_out if r["id"] == f"C:{code}"), None)
                prev_bal = c_row_p["cells"][keys[i - 1]]["v"] if c_row_p else 0.0
            cur_bal = c_row["cells"][k]["v"] if c_row else 0.0
            col_fund_delta += cur_bal - prev_bal
        nonop = col_fund_delta - row_e["cells"][k]["v"]
        cum2 += nonop
        row_f2["cells"][k]["v"] = round(nonop, 2)
    row_f2["total"] = round(cum2, 2)
    rows_out.append(row_f2)

    # 未来列（列起点 > 今天）统一置空：未发生的月份不该"穿越"出数据
    future_keys = [k for k in keys if ranges[k][0] > now]
    if future_keys:
        for row in rows_out:
            for k in future_keys:
                row["cells"][k]["v"] = None
            vals = [row["cells"][k]["v"] for k in keys if row["cells"][k]["v"] is not None]
            if not vals:
                row["total"] = None
            elif row["id"].split(":")[0] in ("A", "C", "F"):
                row["total"] = vals[-1]  # 余额/累计口径：取最后有效列
            else:
                row["total"] = round(sum(vals), 2)  # 流量口径：求和

    # 给每格补环比/同比标记（简单：和前一列比）
    for row in rows_out:
        for i, k in enumerate(keys):
            cur_v = row["cells"][k]["v"]
            if cur_v is None or i == 0:
                continue
            prev_v = row["cells"][keys[i - 1]]["v"]
            if prev_v is None:
                continue
            if abs(prev_v) > 0.01:
                qv = round((cur_v - prev_v) / abs(prev_v) * 100, 1)
                row["cells"][k]["qv"] = qv
                if qv >= 15 and row["section"] == "D":
                    row["cells"][k]["qv_label"] = "↑"
                if qv <= -15 and row["section"] == "B":
                    row["cells"][k]["qv_label"] = "↓"
    # 列的标签(给前端标题用)
    col_labels = []
    for k in keys:
        s, e = ranges[k]
        col_labels.append({"key": k, "start": s.isoformat(), "end": (e - _timedelta(days=1)).isoformat()})

    return Resp.ok({
        "year": year, "granularity": granularity, "company": company, "view": view,
        "window": {"start": ws.isoformat()[:10], "end": (we - _timedelta(days=1)).isoformat()[:10],
                   "win_mode": win_mode},
        "columns": col_labels, "rows": rows_out,
        "_aux": {
            "income_by_src": {k: dict(v) for k, v in col_income_by_src.items()},
            "income_by_cust": {k: dict(sorted(v.items(), key=lambda x: -x[1])[:5])
                               for k, v in col_income_by_cust.items()},
            "exp_by_supplier": {
                k: {cat: dict(sorted(vv.items(), key=lambda x: -x[1])[:5])
                    for cat, vv in v.items()}
                for k, v in col_expense_by_cat_supplier.items()},
        }
    })


@router.get("/cell-details")
def finance_cell_details(col_key: str, year: int, granularity: str = "month",
                         row_id: str = "", company: Optional[int] = None, view: str = "all",
                         page: int = 1, size: int = 50,
                         user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                         db: Session = Depends(get_db)):
    """抽屉：单元格逐笔资金流水（row_id 决定筛选方向）"""
    from sqlalchemy import func
    from app.models.fund import FundAccount, FundFlow
    from app.models.voucher import Voucher, VoucherEntry
    from app.models.finance import FinanceDoc
    _, ranges = _build_column_keys(granularity, year)
    s, e = ranges.get(col_key) or (datetime(year, 1, 1), datetime(year + 1, 1, 1))
    accounts = _fund_account_filter(company, view, db)
    aid_list = [a.id for a in accounts]

    q = db.query(FundFlow).filter(
        FundFlow.fund_account_id.in_(aid_list),
        FundFlow.occur_date >= s, FundFlow.occur_date < e)

    # row_id 决定方向/类别过滤
    direction = None
    category = None
    if row_id.startswith("B:"):
        direction = "IN"
    elif row_id.startswith("D:"):
        direction = "OUT"
        category = row_id.split(":", 1)[1]
        if category not in EXPENSE_ORDER:
            category = None
    elif row_id.startswith("A:") or row_id.startswith("C:"):
        code = row_id.split(":", 1)[1]
        a = next((x for x in accounts if x.code == code), None)
        if a:
            q = q.filter(FundFlow.fund_account_id == a.id)
    if direction:
        q = q.filter(FundFlow.direction == direction)
    if category:
        q = q.filter(FundFlow.expense_category == category)

    # 全量取出（算汇总条+当时余额），再分页切片
    all_flows = q.order_by(FundFlow.occur_date.asc(), FundFlow.id.asc()).all()
    total = len(all_flows)
    sum_in = round(sum(float(f.amount or 0) for f in all_flows if f.direction == "IN"), 2)
    sum_out = round(sum(float(f.amount or 0) for f in all_flows if f.direction == "OUT"), 2)

    # 当时余额（金蝶明细账灵魂）：仅单账户行（A:/C:）有意义——期初=s前累计净流+开户初始，逐笔滚动
    single_acc = None
    if row_id.startswith(("A:", "C:")):
        code = row_id.split(":", 1)[1]
        single_acc = next((x for x in accounts if x.code == code), None)
    bal_map = {}
    opening = None
    if single_acc:
        pre_in = db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
            FundFlow.fund_account_id == single_acc.id, FundFlow.direction == "IN",
            FundFlow.occur_date < s).scalar()
        pre_out = db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
            FundFlow.fund_account_id == single_acc.id, FundFlow.direction == "OUT",
            FundFlow.occur_date < s).scalar()
        opening = round(float(single_acc.opening_balance or 0) + float(pre_in) - float(pre_out), 2)
        run = opening
        for f in all_flows:
            run += float(f.amount or 0) * (1 if f.direction == "IN" else -1)
            bal_map[f.id] = round(run, 2)

    # 倒序分页（最新在前）
    rows = list(reversed(all_flows))[(page - 1) * size: page * size]

    cmap_real = {c.id: c.name for c in db.query(Company).all()}
    out = []
    famap = {}
    for a in accounts:
        famap[a.id] = a
    for f in rows:
        fa = famap.get(f.fund_account_id)
        if fa is None:
            fa = db.query(FundAccount).get(f.fund_account_id)
        comp_name = "集团" if (fa and (fa.company_id is None or fa.company_id == 0)) else (
            cmap_real.get(fa.company_id, "-") if fa else "-")
        # 关联凭证号（按 发生日+金额 粗关联 FundFlow -> VoucherEntry -> Voucher）
        voucher_no = None
        voucher_id = None
        if f.direction == "OUT":
            ve = db.query(VoucherEntry).join(Voucher).filter(
                Voucher.voucher_date.between(
                    f.occur_date - _timedelta(days=3), f.occur_date + _timedelta(days=3)),
                VoucherEntry.debit >= float(f.amount) - 0.1,
                VoucherEntry.debit <= float(f.amount) + 0.1,
            ).first()
        else:
            ve = db.query(VoucherEntry).join(Voucher).filter(
                Voucher.voucher_date.between(
                    f.occur_date - _timedelta(days=3), f.occur_date + _timedelta(days=3)),
                VoucherEntry.credit >= float(f.amount) - 0.1,
                VoucherEntry.credit <= float(f.amount) + 0.1,
            ).first()
        if ve:
            v = db.query(Voucher).get(ve.voucher_id)
            if v:
                voucher_no = v.voucher_no
                voucher_id = v.id
        out.append({
            "id": f.id, "date": f.occur_date.strftime("%Y-%m-%d %H:%M"),
            "fund_account": fa.name if fa else "-",
            "company": comp_name,
            "direction": f.direction,
            "category": f.expense_category or "-",
            "counterparty": f.counterparty or "-",
            "summary": f.summary or "-",
            "amount": float(f.amount or 0),
            "balance_after": bal_map.get(f.id),
            "source_type": f.source_type,
            "voucher_no": voucher_no, "voucher_id": voucher_id,
        })
    return Resp.ok({"total": total, "page": page, "size": size, "list": out,
                    "summary": {"count": total, "in": sum_in, "out": sum_out,
                                "opening": opening}})


@router.get("/row-expand")
def finance_row_expand(row_id: str, col_key: str, year: int, granularity: str = "month",
                       company: Optional[int] = None, view: str = "all",
                       user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                       db: Session = Depends(get_db)):
    """行首+号展开：当月(列)该行的构成拆分"""
    from sqlalchemy import func
    from app.models.fund import FundAccount, FundFlow
    _, ranges = _build_column_keys(granularity, year)
    s, e = ranges.get(col_key) or (datetime(year, 1, 1), datetime(year + 1, 1, 1))
    accounts = _fund_account_filter(company, view, db)
    aid_list = [a.id for a in accounts]

    result = {"row_id": row_id, "col_key": col_key, "items": []}
    # B 本月营收：按来源 / 按客户TOP5
    if row_id == "B:REV":
        flows = db.query(FundFlow).filter(
            FundFlow.fund_account_id.in_(aid_list), FundFlow.direction == 'IN',
            FundFlow.occur_date >= s, FundFlow.occur_date < e).all()
        src = {}
        cust = {}
        acc_by_id = {a.id: a.code for a in accounts}
        for f in flows:
            code = acc_by_id.get(f.fund_account_id, 'UNK')
            src_map = {'ACCEPTANCE': '承兑回款', 'CASH': '现金收入',
                       'JX-BANK': '电汇-机械', 'DG-BANK': '电汇-加工'}
            s_k = src_map.get(code, '其他收入')
            src[s_k] = src.get(s_k, 0.0) + float(f.amount or 0)
            cust[f.counterparty or "其他"] = cust.get(f.counterparty or "其他", 0.0) + float(f.amount or 0)
        result["items"].append({"group": "收入来源", "rows": [
            {"name": k, "amount": round(v, 2)} for k, v in sorted(src.items(), key=lambda x: -x[1])]})
        result["items"].append({"group": "客户TOP5", "rows": [
            {"name": k, "amount": round(v, 2)} for k, v in sorted(cust.items(), key=lambda x: -x[1])[:5]]})

    # A/C 账户余额：期初/入/去向/期末 的汇总
    elif row_id.startswith("A:") or row_id.startswith("C:"):
        code = row_id.split(":", 1)[1]
        a = next((x for x in accounts if x.code == code), None)
        if not a:
            return Resp.ok(result)
        # 年初/期初
        yr_start = datetime(year, 1, 1)
        inn = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
            FundFlow.fund_account_id == a.id, FundFlow.direction == 'IN',
            FundFlow.occur_date < s).scalar() or 0)
        out = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
            FundFlow.fund_account_id == a.id, FundFlow.direction == 'OUT',
            FundFlow.occur_date < s).scalar() or 0)
        open_bal = round(float(a.opening_balance or 0) + inn - out, 2)
        # 本列 入 / 出
        p_in = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
            FundFlow.fund_account_id == a.id, FundFlow.direction == 'IN',
            FundFlow.occur_date >= s, FundFlow.occur_date < e).scalar() or 0)
        p_out = float(db.query(func.coalesce(func.sum(FundFlow.amount), 0)).filter(
            FundFlow.fund_account_id == a.id, FundFlow.direction == 'OUT',
            FundFlow.occur_date >= s, FundFlow.occur_date < e).scalar() or 0)
        # 收入来源TOP5
        in_flows = db.query(FundFlow).filter(
            FundFlow.fund_account_id == a.id, FundFlow.direction == 'IN',
            FundFlow.occur_date >= s, FundFlow.occur_date < e).all()
        out_flows = db.query(FundFlow).filter(
            FundFlow.fund_account_id == a.id, FundFlow.direction == 'OUT',
            FundFlow.occur_date >= s, FundFlow.occur_date < e).all()
        in_src = {}
        for f in in_flows:
            in_src[f.counterparty or "其他"] = in_src.get(f.counterparty or "其他", 0.0) + float(f.amount or 0)
        out_dest = {}
        for f in out_flows:
            out_dest[(f.expense_category or "其他") + "/" + (f.counterparty or "其他")] = \
                out_dest.get((f.expense_category or "其他") + "/" + (f.counterparty or "其他"), 0.0) + float(f.amount or 0)
        result["items"] = [
            {"group": "余额构成", "rows": [
                {"name": "期初余额", "amount": open_bal},
                {"name": "本期收入合计", "amount": round(p_in, 2)},
                {"name": "本期支出合计", "amount": round(p_out, 2)},
                {"name": "期末余额", "amount": round(open_bal + p_in - p_out, 2)},
            ]},
            {"group": "收入来源TOP5", "rows": [
                {"name": k, "amount": round(v, 2)} for k, v in sorted(in_src.items(), key=lambda x: -x[1])[:5]]},
            {"group": "支出去向TOP5", "rows": [
                {"name": k, "amount": round(v, 2)} for k, v in sorted(out_dest.items(), key=lambda x: -x[1])[:5]]},
        ]
    # D 费用行：按供应商/分类拆分
    elif row_id.startswith("D:"):
        cat = row_id.split(":", 1)[1]
        flows = db.query(FundFlow).filter(
            FundFlow.fund_account_id.in_(aid_list), FundFlow.direction == 'OUT',
            FundFlow.expense_category == cat,
            FundFlow.occur_date >= s, FundFlow.occur_date < e).all()
        by_supp = {}
        by_acc = {}
        acc_by_id = {a.id: a.name for a in accounts}
        for f in flows:
            by_supp[f.counterparty or "其他"] = by_supp.get(f.counterparty or "其他", 0.0) + float(f.amount or 0)
            by_acc[acc_by_id.get(f.fund_account_id, "-")] = \
                by_acc.get(acc_by_id.get(f.fund_account_id, "-"), 0.0) + float(f.amount or 0)
        total = sum(by_supp.values())
        result["items"] = [
            {"group": f"{cat}合计", "rows": [{"name": f"{cat}本期合计", "amount": round(total, 2)}]},
            {"group": "供应商TOP5", "rows": [
                {"name": k, "amount": round(v, 2)} for k, v in sorted(by_supp.items(), key=lambda x: -x[1])[:5]]},
            {"group": "支付账户分布", "rows": [
                {"name": k, "amount": round(v, 2)} for k, v in sorted(by_acc.items(), key=lambda x: -x[1])]},
        ]
    return Resp.ok(result)
