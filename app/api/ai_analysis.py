"""
AI分析模块 - DeepSeek双调用架构
================================
1. 意图解析(flash): 自然语言 → pivot参数
2. 执行 build_pivot 拿到数据
3. 报告生成(pro): 数据 → 专业分析报告(含异常/风险/建议)

LLM失败时降级到关键词匹配。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import logging
from app.core.db import get_db
from app.core.auth import get_current_user
from app.models.system import User
from app.schemas import Resp
from app.core.pivot import build_pivot, list_datasets
from app.api.analysis import kpi as calc_kpi, receivable_aging, payment_schedules
from app.core import llm
from app.config import settings

router = APIRouter(prefix="/api/ai", tags=["ai_analysis"])
logger = logging.getLogger(__name__)


# ============ 数据源schema(供LLM意图解析) ============
def _build_schema_text() -> str:
    """构造精简数据源字典文本,供LLM意图解析"""
    ds = list_datasets()
    lines = []
    for key, v in ds.items():
        dims = [f'{d["key"]}({d["label"]})' for d in v["dims"]]
        time_dims = [f'{d["key"]}({d["label"]})' for d in v["time_dims"]]
        metrics = [f'{m["key"]}({m["label"]})' for m in v["metrics"]]
        lines.append(f"- {key}({v['label']}): 维度[{','.join(dims + time_dims)}] 指标[{','.join(metrics)}]")
    return "\n".join(lines)


# ============ 通用问题(KPI/预警/账龄) - 直接查库不走LLM ============
def _format_general(text: str, db: Session, user: User = None) -> Optional[str]:
    text_lower = text.lower()
    if any(kw in text_lower for kw in ["kpi", "概况", "总览", "整体", "赚了多少", "亏了", "利润"]):
        k = calc_kpi(user, db)
        d = k.get("data", {}) if isinstance(k, dict) else {}
        if d:
            return (f"📊 经营概况\n  营收: ¥{d.get('revenue',0):,.0f}\n  成本: ¥{d.get('cost',0):,.0f}\n"
                    f"  毛利: ¥{d.get('profit',0):,.0f} (毛利率{d.get('gross_margin_pct',0):.1f}%)\n"
                    f"  应收余额: ¥{d.get('ar_balance',0):,.0f}\n  库存价值: ¥{d.get('inventory_value',0):,.0f}\n"
                    f"  订单: {d.get('order_count',0)} 工单: {d.get('work_order_count',0)} 完工: {d.get('completion_count',0)}")

    if any(kw in text_lower for kw in ["回款预警", "催款预警"]):
        scheds = payment_schedules(30, user, db)
        sd = scheds.model_dump() if hasattr(scheds, 'model_dump') else scheds
        rows = sd.get("data", [])
        overdue = [s for s in rows if s.get("status") == "OVERDUE"]
        soon = [s for s in rows if s.get("status") == "DUE_SOON"]
        lines = ["⚠️ 回款预警"]
        if overdue:
            lines.append(f"  🔴 已逾期 {len(overdue)} 笔:")
            for s in overdue:
                lines.append(f"    · {s.get('customer_name','?')} ¥{s['expected_amount']:,.0f} 到期{s.get('due_date','')[:10]}")
        if soon:
            lines.append(f"  🟡 即将到期 {len(soon)} 笔:")
            for s in soon:
                lines.append(f"    · {s.get('customer_name','?')} ¥{s['expected_amount']:,.0f} 到期{s.get('due_date','')[:10]}")
        if not overdue and not soon:
            lines.append("  当前无逾期或即将到期的回款节点 ✅")
        return "\n".join(lines)

    if any(kw in text_lower for kw in ["应收账龄", "账龄分析"]):
        r = receivable_aging(user, db)
        rd = r.model_dump() if hasattr(r, 'model_dump') else r
        s = rd.get("data", {}).get("summary", {})
        return (f"📅 应收账龄分析\n  总余额: ¥{s.get('total',0):,.0f}\n"
                f"  · 0-30天: ¥{s.get('0-30天',0):,.0f}\n  · 31-60天: ¥{s.get('31-60天',0):,.0f}\n"
                f"  · 61-90天: ¥{s.get('61-90天',0):,.0f}\n  · 90天以上: ¥{s.get('90天以上',0):,.0f}\n"
                f"  · 未到期: ¥{s.get('未到期',0):,.0f}")
    return None


# ============ API ============
class AnalyzeIn(BaseModel):
    text: str
    history: list = []  # 对话历史[{"role":"user"/"ai","text":"..."}]


@router.post("/analyze")
def analyze(body: AnalyzeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """AI分析: 自然语言 → LLM意图解析 → 透视 → LLM报告生成"""
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "请输入分析指令")

    # 1. 通用问题(KPI/预警/账龄)直接查库,不走LLM
    general = _format_general(text, db, user)
    if general:
        return Resp.ok({"type": "general", "query_text": text, "reply": general})

    # 2. LLM意图解析
    intent = None
    llm_used = False
    try:
        schema = _build_schema_text()
        intent = llm.parse_intent(text, schema, body.history)
        # 校验
        intent = _validate_intent(intent)
        if intent:
            llm_used = True
    except Exception as e:
        logger.warning(f"LLM意图解析失败: {e}")

    # 3. LLM意图模糊 → 追问
    if not intent and not llm_used:
        # 调LLM生成追问问题
        question = _ask_clarify(text, body.history)
        return Resp.ok({"type": "clarify", "query_text": text, "reply": question})

    # 4. 执行透视
    result = build_pivot(
        db, dataset=intent["dataset"], rows_dim=intent["rows_dim"],
        cols_dim=intent.get("cols_dim"), metric=intent["metric"],
        agg=intent["agg"], filters=intent.get("filters", []),
    )
    if "error" in result:
        return Resp.ok({"type": "error", "query_text": text, "reply": f"分析出错: {result['error']}"})

    # 6. LLM报告生成
    reply = None
    try:
        reply = llm.generate_report(text, result)
    except Exception as e:
        logger.warning(f"LLM报告生成失败,降级格式化: {e}")
    if not reply:
        reply = _simple_format(result)

    return Resp.ok({
        "type": "pivot",
        "query_text": text,
        "reply": reply,
        "pivot_data": result,
        "llm_used": llm_used,
    })


def _validate_intent(intent: dict) -> Optional[dict]:
    """校验意图参数的合法性,返回None表示无效"""
    if not intent or not all(k in intent for k in ["dataset", "rows_dim", "metric", "agg"]):
        return None
    valid_ds = list_datasets()
    if intent.get("dataset") not in valid_ds:
        logger.warning(f"LLM返回的dataset无效: {intent.get('dataset')}")
        return None
    ds_cfg = valid_ds[intent["dataset"]]
    valid_dims = {d["key"] for d in ds_cfg["dims"]} | {d["key"] for d in ds_cfg["time_dims"]}
    valid_metrics = {m["key"] for m in ds_cfg["metrics"]}
    if intent.get("rows_dim") not in valid_dims:
        logger.warning(f"LLM返回的rows_dim无效: {intent.get('rows_dim')}")
        return None
    if intent.get("metric") not in valid_metrics:
        logger.warning(f"LLM返回的metric无效: {intent.get('metric')}")
        return None
    return intent


def _ask_clarify(text: str, history: list) -> str:
    """LLM意图模糊时,生成追问问题"""
    ctx = ""
    for h in history[-3:]:
        role = "用户" if h.get("role") == "user" else "AI"
        ctx += f"\n{role}: {h.get('text','')}"
    prompt = f"对话历史:{ctx}\n\n用户最新消息: {text}\n\n"
    prompt += "用户想进行数据分析,但意图不明确。请根据对话上下文,生成1个简短追问帮助确定分析方向。"
    prompt += "追问要具体,给出2-3个选项让用户选择。直接输出问题,不要解释。"
    try:
        msg = [{"role": "user", "content": prompt}]
        return llm.chat(settings.DEEPSEEK_MODEL_FAST, msg, temperature=0.3, max_tokens=200)
    except:
        return "请问你想分析哪个方面？订单、财务、工单还是成本？"


def _simple_format(d: dict) -> str:
    """LLM失败时的简单格式化"""
    lines = [f"📊 {d.get('dataset_label','')}分析 — 按{d.get('rows_label','')}分组"]
    for row in d.get("table", []):
        dim = row.get("dim", "")
        vals = {k: v for k, v in row.items() if k != "dim"}
        lines.append(f"  · {dim}: " + " | ".join(f"{k}={v:,.0f}" for k, v in vals.items()))
    return "\n".join(lines)


@router.get("/help")
def help_cmd(user: User = Depends(get_current_user)):
    return Resp.ok({"examples": [
        "分析订单状态分布", "开票类型分析", "各公司订单",
        "这个月收款情况", "应收分析", "付款分析",
        "各车间产量", "工单情况", "成本结构分析",
        "商机阶段分布", "库存流水分析",
        "经营概况", "回款预警", "应收账龄分析",
        "哪家客户欠款最多", "这个月各车间产量对比,找出异常",
    ]})
