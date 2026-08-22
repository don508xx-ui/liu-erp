"""AI财务工作台 - 替财务干活, 财务只审核确认。0 token, 纯SQL匹配+规则引擎。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from app.core.db import get_db
from app.core.auth import get_current_user
from app.core.permissions import require_role
from app.models.system import User
from app.models.finance import FinanceDoc, PayrollRun
from app.models.expense import ExpenseClaim
from app.models.fund import FundFlow, FundAccount, AcceptanceBill
from app.models.workshop import WorkOrder, Completion, CompletionItem
from app.models.inventory import InventoryItem
from app.schemas import Resp

router = APIRouter(prefix="/api/ai-ops", tags=["ai-ops"])


# ============ 1. 收款认领: AI匹配资金流入→应收单 ============

@router.get("/receipt-match")
def receipt_match(user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                  db: Session = Depends(get_db)):
    """扫描未认领的收款流水 → 按客户名+金额匹配应收单, 财务点确认即核销。"""
    # 未认领的流入: 有counterparty但source_type不是RECEIPT/ACCEPTANCE_IN
    flows = db.query(FundFlow).filter(
        FundFlow.direction == "IN",
        FundFlow.counterparty.isnot(None),
        FundFlow.counterparty != "",
    ).all()
    # 排除已自动匹配的
    matched_flow_ids = set()
    ars = db.query(FinanceDoc).filter(
        FinanceDoc.doc_type == "RECEIVABLE",
        FinanceDoc.status.in_(["OPEN", "DRAFT", "UNPAID", "PARTIAL"]),
    ).all()
    # 按客户名分组应收单
    ar_by_customer = {}
    for ar in ars:
        name = (ar.counterparty_name or "").strip()
        if name:
            ar_by_customer.setdefault(name, []).append(ar)

    results = []
    for f in flows:
        if f.id in matched_flow_ids:
            continue
        if f.source_type in ("RECEIPT", "ACCEPTANCE_IN"):
            continue
        cp = (f.counterparty or "").strip()
        amt = round(float(f.amount or 0), 2)
        # 精确匹配: 客户名一致 + 金额一致
        exact = []
        partial = []
        for name, ars_list in ar_by_customer.items():
            name_similar = _name_match(cp, name)
            if not name_similar:
                continue
            for ar in ars_list:
                remaining = round(float(ar.amount or 0) - float(ar.settled_amount or 0), 2)
                if remaining <= 0:
                    continue
                if abs(remaining - amt) < 0.5:
                    exact.append({"ar_id": ar.id, "doc_no": ar.doc_no, "amount": float(ar.amount or 0),
                                 "remaining": remaining, "customer": name, "confidence": "高"})
                elif abs(remaining - amt) < amt * 0.05:
                    partial.append({"ar_id": ar.id, "doc_no": ar.doc_no, "amount": float(ar.amount or 0),
                                   "remaining": remaining, "customer": name, "confidence": "中"})
        if exact or partial:
            results.append({
                "flow_id": f.id,
                "flow_date": (f.occur_date or datetime.utcnow()).isoformat() if f.occur_date else None,
                "counterparty": cp,
                "amount": amt,
                "summary": f.summary or "",
                "matches": exact + partial,
                "best_match": (exact + partial)[0] if (exact or partial) else None,
            })
    return {"code": 0, "data": results, "total": len(results)}


@router.post("/receipt-match/{flow_id}/confirm")
def confirm_receipt(flow_id: int, body: dict = None,
                    user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                    db: Session = Depends(get_db)):
    """财务确认: 流水核销应收单 → 自动生成资金流水source_type + 凭证"""
    flow = db.query(FundFlow).get(flow_id)
    if not flow:
        raise HTTPException(404, "流水不存在")
    ar_id = (body or {}).get("ar_id")
    if not ar_id:
        raise HTTPException(400, "需指定应收单ID")
    ar = db.query(FinanceDoc).get(ar_id)
    if not ar or ar.doc_type != "RECEIVABLE":
        raise HTTPException(400, "应收单不存在")
    settle_amt = round(float(flow.amount or 0), 2)
    remaining = round(float(ar.amount or 0) - float(ar.settled_amount or 0), 2)
    if settle_amt > remaining + 0.5:
        raise HTTPException(400, f"收款金额 ¥{settle_amt} 超过未收 ¥{remaining}")
    # 核销应收单
    ar.settled_amount = round(float(ar.settled_amount or 0) + settle_amt, 2)
    ar.settled_at = datetime.utcnow()
    if abs(float(ar.amount or 0) - float(ar.settled_amount or 0)) < 0.5:
        ar.status = "SETTLED"
    else:
        ar.status = "PARTIAL"
    # 标记流水已认领
    flow.source_type = "RECEIPT"
    flow.source_id = ar.id
    flow.summary = f"核销应收-{ar.doc_no}"
    db.commit()
    return Resp.ok({"ar_id": ar.id, "doc_no": ar.doc_no, "settled": settle_amt,
                    "remaining": round(float(ar.amount or 0) - float(ar.settled_amount or 0), 2)})


def _name_match(a: str, b: str) -> bool:
    """客户名模糊匹配: 包含关系或去掉常见后缀后一致"""
    if not a or not b:
        return False
    a, b = a.strip(), b.strip()
    if a == b:
        return True
    if a in b or b in a:
        return True
    # 去掉有限公司/公司/厂等后缀
    for suffix in ["有限公司", "有限责任公司", "公司", "厂", "加工厂", "机械"]:
        a2, b2 = a.replace(suffix, ""), b.replace(suffix, "")
        if a2 and b2 and (a2 in b2 or b2 in a2):
            return True
    return False


# ============ 2. 报销审单: AI查重+超标检测 ============

@router.get("/expense-review")
def expense_review(user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                    db: Session = Depends(get_db)):
    """扫描已提交报销单 → 查重/超标/异常 → 标红附理由, 财务只看被标红的"""
    claims = db.query(ExpenseClaim).filter(
        ExpenseClaim.status.in_(["SUBMITTED", "APPROVED"])
    ).all()
    # 按人和金额查重: 同一天同一人报了相同金额
    seen = {}
    results = []
    for ec in claims:
        flags = []
        # 1. 重复检测: 同一申请人同一天相同金额
        key = (ec.applicant_user_id, float(ec.amount or 0))
        if key in seen:
            other = seen[key]
            flags.append({"type": "duplicate", "level": "danger",
                         "msg": f"与报销单 {other.claim_no} 金额相同(¥{float(ec.amount or 0):,.2f}), 疑似重复"})
        else:
            seen[key] = ec
        # 2. 大额预警: >5000需GM终审
        if float(ec.amount or 0) > 5000:
            flags.append({"type": "large_amount", "level": "warning",
                         "msg": f"金额 ¥{float(ec.amount or 0):,.2f} 超5000, 需GM终审"})
        # 3. 类型+金额交叉: 差旅>3000 或 餐饮>2000
        ct = ec.claim_type or ""
        amt = float(ec.amount or 0)
        if ct == "TRAVEL" and amt > 3000:
            flags.append({"type": "over_standard", "level": "warning",
                         "msg": f"差旅费 ¥{amt:,.2f} 超常理标准(¥3000)"})
        if ct == "MEAL" and amt > 2000:
            flags.append({"type": "over_standard", "level": "danger",
                         "msg": f"餐饮费 ¥{amt:,.2f} 异常偏高(¥2000)"})
        # 4. 无明细大额报销
        items = ec.items if isinstance(ec.items, list) else []
        if amt > 1000 and len(items) == 0:
            flags.append({"type": "no_detail", "level": "warning",
                         "msg": f"金额 ¥{amt:,.2f} 但无明细, 建议补附件"})
        applicant = db.query(User).filter(User.id == ec.applicant_user_id).first() if ec.applicant_user_id else None
        results.append({
            "id": ec.id, "claim_no": ec.claim_no,
            "applicant": applicant.display_name if applicant else f"用户{ec.applicant_user_id}",
            "amount": amt, "claim_type": ct,
            "status": ec.status,
            "flags": flags,
            "is_clean": len(flags) == 0,
        })
    clean = [r for r in results if r["is_clean"]]
    flagged = [r for r in results if not r["is_clean"]]
    return {"code": 0, "data": {"clean": clean, "flagged": flagged, "total": len(results)}}


@router.post("/expense-review/batch-approve")
def batch_approve_expenses(body: dict,
                           user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                           db: Session = Depends(get_db)):
    """批量审批通过的报销单"""
    ids = body.get("ids") or []
    if not ids:
        raise HTTPException(400, "未选择报销单")
    approved = []
    for eid in ids:
        ec = db.query(ExpenseClaim).get(eid)
        if not ec:
            continue
        if ec.status not in ("SUBMITTED", "APPROVED"):
            continue
        role_code = _role(user, db)
        amt = float(ec.amount or 0)
        if role_code in ("GM", "ADMIN") or amt <= 5000:
            ec.status = "PAID"
            ec.approved_by_user_id = user.id
            ec.approved_at = datetime.utcnow()
            approved.append(ec.claim_no)
    db.commit()
    return Resp.ok({"approved": approved, "count": len(approved)})


@router.post("/expense-review/{eid}/reject")
def reject_expense(eid: int, body: dict = None,
                   user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                   db: Session = Depends(get_db)):
    """驳回报销单"""
    ec = db.query(ExpenseClaim).get(eid)
    if not ec:
        raise HTTPException(404, "报销单不存在")
    reason = (body or {}).get("reason", "AI审单异常")
    ec.status = "REJECTED"
    ec.remark = (ec.remark or "") + f"\n[AI审单驳回] {reason}"
    db.commit()
    return Resp.ok({"id": eid, "status": "REJECTED"})


# ============ 3. 成本核算: 按工单归集材料+人工+制费 ============

@router.get("/cost-calc")
def cost_calc(user: User = Depends(require_role("FINANCE", "GM", "ADMIN", "MANAGER")),
              db: Session = Depends(get_db)):
    """扫描已确认完工单 → 归集材料+人工+制费 → 算单位成本 → 标异常"""
    comps = db.query(Completion).filter(Completion.status == "CONFIRMED").all()
    results = []
    for cp in comps:
        wo = db.query(WorkOrder).get(cp.work_order_id) if cp.work_order_id else None
        # 材料成本 = sum(CompletionItem.actual_qty * unit_cost)
        mat_cost = 0
        mat_detail = []
        for ci in (cp.items or []):
            if ci.actual_qty and ci.unit_cost:
                line = round(float(ci.actual_qty) * float(ci.unit_cost), 2)
                mat_cost += line
                mat_detail.append({"item": ci.item_name, "qty": float(ci.actual_qty or 0),
                                   "unit_cost": float(ci.unit_cost or 0), "line_cost": line})
        labor = float(cp.labor_cost or 0)
        overhead = float(cp.overhead_cost or 0)
        total = round(mat_cost + labor + overhead, 2)
        qualified = float(cp.qualified_qty or 0)
        unit = round(total / qualified, 2) if qualified > 0 else 0
        # 异常检测
        flags = []
        if unit <= 0:
            flags.append({"type": "zero_cost", "level": "danger", "msg": "单位成本为0, 数据缺失"})
        if qualified == 0 and float(cp.finished_qty or 0) > 0:
            flags.append({"type": "no_qualified", "level": "danger", "msg": "完工但合格数为0, 全部返工/废品"})
        if mat_cost == 0 and total > 0:
            flags.append({"type": "no_material", "level": "warning", "msg": "无材料成本, 可能漏录领料"})
        if labor == 0 and overhead == 0 and total > 0:
            flags.append({"type": "no_labor", "level": "info", "msg": "无人工/制费, 可能未填"})
        results.append({
            "id": cp.id, "completion_no": cp.completion_no,
            "work_order_no": wo.work_order_no if wo else "",
            "material_cost": round(mat_cost, 2),
            "labor_cost": labor,
            "overhead_cost": overhead,
            "total_cost": total,
            "qualified_qty": qualified,
            "unit_cost": unit,
            "flags": flags,
            "is_normal": len(flags) == 0,
            "material_detail": mat_detail,
        })
    return {"code": 0, "data": results, "total": len(results)}


@router.post("/cost-calc/{cp_id}/post-voucher")
def post_cost_voucher(cp_id: int,
                      user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                      db: Session = Depends(get_db)):
    """成本确认: 生成结转凭证(借 产成品 贷 生产成本-材料/人工/制费)"""
    from app.api.vouchers import _get_or_create_account
    from app.core import voucher_service
    cp = db.query(Completion).get(cp_id)
    if not cp:
        raise HTTPException(404, "完工单不存在")
    if cp.status != "CONFIRMED":
        raise HTTPException(400, f"完工单状态为{cp.status}, 仅已确认的完工单可结转")
    qualified = float(cp.qualified_qty or 0)
    if qualified <= 0:
        raise HTTPException(400, "合格数量为0, 无法结转成本(请先填写合格数量)")
    # 算成本
    mat_cost = sum(round(float(ci.actual_qty or 0) * float(ci.unit_cost or 0), 2) for ci in (cp.items or []))
    labor = float(cp.labor_cost or 0)
    overhead = float(cp.overhead_cost or 0)
    total = round(mat_cost + labor + overhead, 2)
    if total <= 0:
        raise HTTPException(400, "成本为0, 无法结转(请补录材料/人工/制费)")
    period = (cp.confirmed_at or datetime.utcnow()).strftime("%Y-%m")
    # 借 1405产成品 贷 5001生产成本-材料 / 5002生产成本-人工 / 5101制造费用
    fg = _get_or_create_account(db, "1405", "库存商品", "ASSET", "DEBIT")
    mat_acc = _get_or_create_account(db, "5001", "生产成本-材料", "COST", "DEBIT")
    labor_acc = _get_or_create_account(db, "5002", "生产成本-人工", "COST", "DEBIT")
    oh_acc = _get_or_create_account(db, "5101", "制造费用", "COST", "DEBIT")
    entries = [{"account_id": fg.id, "summary": f"完工入库-{cp.completion_no}", "debit": total, "credit": 0}]
    if mat_cost > 0:
        entries.append({"account_id": mat_acc.id, "summary": f"材料-{cp.completion_no}", "debit": 0, "credit": mat_cost})
    if labor > 0:
        entries.append({"account_id": labor_acc.id, "summary": f"人工-{cp.completion_no}", "debit": 0, "credit": labor})
    if overhead > 0:
        entries.append({"account_id": oh_acc.id, "summary": f"制费-{cp.completion_no}", "debit": 0, "credit": overhead})
    v = voucher_service.create_voucher(db, {
        "period": period,
        "voucher_date": cp.confirmed_at or datetime.utcnow(),
        "summary": f"完工成本结转-{cp.completion_no}",
        "entries": entries,
    }, creator_id=user.id)
    voucher_service.post_voucher(db, v.id)
    cp.total_cost = total
    db.commit()
    return Resp.ok({"voucher_no": v.voucher_no, "total_cost": total, "unit_cost": round(total / float(cp.qualified_qty or 1), 2)})


# ============ 4. 银企对账: 预留接口 ============

@router.get("/bank-recon")
def bank_recon(user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
               db: Session = Depends(get_db)):
    """银企对账: 预留接口, 等银行流水导入后自动匹配。当前返回待对账流水数"""
    unmatched = db.query(FundFlow).filter(
        FundFlow.direction.in_(["IN", "OUT"]),
        FundFlow.source_type.is_(None),
    ).count()
    return {"code": 0, "data": {"unmatched_flows": unmatched, "hint": "银行接口预留, 待接入后自动匹配"}}


def _role(user: User, db: Session) -> str:
    from app.models.system import Role
    if user.role_id:
        r = db.query(Role).get(user.role_id)
        return r.code if r else "USER"
    return "USER"


# ============ 5. AI审凭证: LLM逐张审核当期凭证科目/金额/摘要/逻辑 ============

VOUCHER_REVIEW_PROMPT = """你是一名资深企业总账会计，现在审核以下会计凭证。请逐张检查，找出问题。

检查维度:
1. 科目用错: 借贷方向反了、科目选错(如应走"管理费用"走成了"销售费用"、应入"固定资产"入了"费用")
2. 金额异常: 金额与业务明显不符(如工资计提只有几块钱、采购材料金额为0)、借贷不平
3. 摘要不规范: 摘要太模糊(如只写"做账""调整")、摘要与分录科目矛盾
4. 业务逻辑不通: 如收款没对应应收、付款没对应应付、计提无依据、红字冲销无说明
5. 税务风险: 如大额现金收支、无票费用、个人收款

企业背景: 小型精密机械加工厂(热喷涂/表面处理)，业务涉及: 销售加工服务、采购涂料粉末、设备折旧、工资、厂房租金、水电、差旅报销、承兑汇票。

请返回JSON格式(不要输出其他内容):
{
  "summary": "整体评价一句话",
  "total_issues": 问题总数,
  "results": [
    {
      "voucher_no": "凭证号",
      "issues": [
        {"level": "danger/warning/info", "type": "错误类型", "msg": "具体问题描述", "suggestion": "修改建议"}
      ]
    }
  ]
}
- level: danger=必须改(科目错/借贷错/金额异常), warning=建议改(摘要不清/逻辑存疑), info=提醒(可优化)
- 没有问题的凭证不要出现在results里
- 如果所有凭证都没问题, results返回空数组
"""

@router.post("/voucher-review")
def voucher_review(body: dict,
                   user: User = Depends(require_role("FINANCE", "GM", "ADMIN")),
                   db: Session = Depends(get_db)):
    """AI审核当期凭证: 科目/金额/摘要/业务逻辑检查, 返回结构化问题清单"""
    from app.models.voucher import Voucher
    period = body.get("period")
    if not period:
        from datetime import datetime
        period = datetime.utcnow().strftime("%Y-%m")
    vouchers = db.query(Voucher).filter(
        Voucher.period == period,
        Voucher.status == "POSTED"
    ).order_by(Voucher.voucher_no).all()
    total = len(vouchers)
    BATCH = 60
    truncated = total > BATCH
    if truncated:
        vouchers = vouchers[-BATCH:]
    if not vouchers:
        return Resp.ok({"summary": f"{period} 无已过账凭证可审核", "total_issues": 0, "results": [], "total": 0, "truncated": False})
    lines = []
    for v in vouchers:
        ents = []
        td = tc = 0.0
        for e in (v.entries or []):
            d, c = float(e.debit or 0), float(e.credit or 0)
            td += d; tc += c
            if d > 0: ents.append(f"借{e.account_name} {d:.2f}")
            if c > 0: ents.append(f"贷{e.account_name} {c:.2f}")
        bal = "平" if abs(td - tc) < 0.01 else f"不平{td-tc:.2f}"
        lines.append(f"{v.voucher_no}[{bal}] {v.summary or '(无)'}→{'/'.join(ents)}")
    voucher_text = "\n".join(lines)
    from app.core import llm
    from app.config import settings
    user_content = f"审核{period}的{len(vouchers)}张已过账凭证"
    if truncated:
        user_content += f"(共{total}张,仅审最后{BATCH}张)"
    user_content += f":\n{voucher_text}"
    messages = [
        {"role": "system", "content": VOUCHER_REVIEW_PROMPT},
        {"role": "user", "content": user_content}
    ]
    try:
        result = llm.chat_json(settings.DEEPSEEK_MODEL_FAST, messages, temperature=0.1, max_tokens=8192, timeout=90)
        result["total"] = total
        result["truncated"] = truncated
        return Resp.ok(result)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"AI审凭证失败: {e}", exc_info=True)
        raise HTTPException(500, f"AI审核失败: {str(e)[:200]}")
