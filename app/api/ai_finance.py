"""
AI财务专职助手 - 全盘复用AI经营分析能力，仅切换为财务/税务专项定位
================================================================
定位：财务专职助手。只做一件事——检查财务问题、发现漏洞、给出财务/税务建议与方案。
- 会话与经营助手隔离(scope=finance)
- 意图解析仍复用云端 llm.parse_intent / parse_intent_stream(硬约束)
- 透视查询复用 build_pivot
- 报告/对话 prompt 换成"财务审计+税务顾问"视角，主动找问题、给方案
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import json
import logging
from datetime import datetime
from app.core.db import get_db, SessionLocal
from app.core.auth import get_current_user
from app.models.system import User
from app.models.ai import AIConversation, AIMessage
from app.schemas import Resp
from app.api import ai_analysis as A
from app.core.pivot import build_pivot
from app.core import llm
from app.config import settings

router = APIRouter(prefix="/api/ai-finance", tags=["ai_finance"])
logger = logging.getLogger(__name__)

SCOPE = "finance"

ASSISTANT_DP = """
你是一名资深的企业财务顾问兼税务师(总账会计+税务师双重视角)，服务对象是半桶水财务/总经理。
你的唯一职责：帮助这家企业【检查财务问题、发现漏洞、给出财务和税务建议与解决方案】。
一切输出必须基于下方提供的真实查询数据，禁止编造内部数字。
"""


def _now_str():
    from app.core import llm as _l
    try:
        return _l._now_context()
    except Exception:
        return ""


def _fin_overview_prompt() -> str:
    """财务专职综合诊断的系统提示(替代经营的 overview prompt)"""
    return ASSISTANT_DP + """
请以财务审计+税务合规的视角，对公司财务数据进行整体"体检"，输出一份财务问题诊断报告。

必须包含:
1. 【整体判断】财务状况一句话结论(现金流是否健康、是否有雷)
2. 【已发现问题】用⚠️逐条列出发现的财务问题或异常(如: 应收过大、账龄超期、收支失衡、凭证缺失、返工/退货成本异常、调价合规风险等)，每条都要有具体数据佐证
3. 【税务风险】用🚨指出可能存在的税务风险点(如: 大额现金收支未入账、主体间往来不清、开票与收款主体不匹配、费用报销不合规、无票支出等)，说明风险依据
4. 【建议方案】用✅给出可落地的财务+税务优化方案(分优先级: 立即/短期/中期)
5. 【合规提醒】需要注意的合规事项

规则:
- 只基于下方数据，数据不足就明确说，禁止推测
- 用markdown表格/列表，关键数字加粗
- 用中文，通俗，让半桶水财务看得懂
- 不要生成代码块图表
"""


def _fin_report_system() -> str:
    """单任务财务报告的 system prompt(替代经营的 report prompt)"""
    return ASSISTANT_DP + """
对以下查询结果做财务专业解读，但重点放在【发现财务/税务问题 + 给出建议】上。

结构:
1. 数据结论(一句话)
2. ⚠️ 从中发现的财务/税务问题或异常(有数据佐证)
3. 🚨 相关的税务风险点
4. ✅ 改进建议与处理方案

规则:
- 只基于本次查询数据，不编造
- 用markdown表格辅助对比，关键数字加粗
- 用中文通俗表达
"""


def _fin_chat_system(web_context: str = "") -> str:
    db_schema = A._build_db_schema_text()
    now_ctx = _now_str()
    sys = (
        "你是这家企业的财务专职顾问兼税务师。你拥有对财务系统全部数据的实时查询能力。\n\n"
        f"{now_ctx}\n\n"
        f"以下是系统数据库完整表结构(动态获取,实时准确):\n\n{db_schema}\n\n"
        "你的职责：检查财务问题、发现漏洞、给出财务和税务建议与解决方案。\n"
        "能力与边界:\n"
        "1. 用户问具体财务数据(应收、收款、付款、凭证、报销、返工成本、调价、工资等)时，你会触发查询并给出基于真实数据的结论。\n"
        "2. 用户问财务/税务知识或请你就某业务如何记账、如何规避税务风险时，直接专业解答并给方案。\n"
        "3. 对时间问题(今天几号/本月等)直接用上方【系统时间】回答。\n"
        "4. 绝不说\"我不能访问数据\"——你能查就能答。\n"
        "5. 具体数值必须基于真实查询，不编造。\n"
    )
    if web_context:
        sys += "6. 已联网搜索相关信息(见用户消息中'联网搜索结果'),可引用但须注明'据网络公开信息'。\n"
    sys += "当发现财务问题/税务风险时主动、明确地指出，并给出可落地建议。思考过程保持简短。"
    return sys


def _fin_discuss_system(web_context: str = "") -> str:
    now_ctx = _now_str()
    sys = (
        ASSISTANT_DP + f"{now_ctx}\n"
        "基于下方最近分析的数据回答用户追问。规则:\n"
        "1. 只能基于给定数据回答,不编造内部数字\n"
        "2. 主动指出数据中暴露的财务/税务问题与风险\n"
        "3. 给遇到问题时给出财务/税务处理建议\n"
        "4. 用中文通俗表达,可用markdown表格,不要用mermaid"
    )
    if web_context:
        sys += "\n5. 可引用联网信息但不编造内部数"
    sys += "\n6. 思考过程保持简短精炼。"
    return sys


def _fin_overview(db: Session, memory_prompt: str, tasks: list, web_context: str = "") -> tuple:
    """财务综合体检: 复用 A._overview_analysis 的执行逻辑，但换财务诊断 prompt"""
    from app.core.pivot import build_pivot
    from app.core.db import SessionLocal
    from concurrent.futures import ThreadPoolExecutor

    if not tasks:
        return "未获得有效的分析任务，无法执行财务诊断。请换一种描述方式，如'检查应收情况'或'看看这个月财务收支'。", None

    def _run_one(task):
        s = SessionLocal()
        try:
            result = build_pivot(
                s, dataset=task["dataset"], rows_dim=task["rows_dim"],
                cols_dim=None, metric=task["metric"], agg=task["agg"],
                filters=task.get("filters", []),
            )
            if "error" in result:
                return {"failed": {"alias": task.get("alias", task["dataset"]), "reason": result["error"]}}
            if result.get("table"):
                result["_alias"] = task.get("alias", result.get("dataset_label", ""))
                return {"ok": result}
            return {"failed": {"alias": task.get("alias", task["dataset"]), "reason": "查询结果为空"}}
        except Exception as e:
            logger.warning(f"财务诊断 {task['dataset']} 失败: {e}")
            return {"failed": {"alias": task.get("alias", task["dataset"]), "reason": str(e)}}
        finally:
            s.close()

    results, failed = [], []
    with ThreadPoolExecutor(max_workers=len(tasks) or 1) as pool:
        for out in pool.map(_run_one, tasks):
            if "ok" in out:
                results.append(out["ok"])
            else:
                failed.append(out["failed"])

    if not results:
        reasons = "; ".join(f"{f['alias']}({f['reason']})" for f in failed) or "未知原因"
        return f"无法完成财务诊断。原因: {reasons}。请检查数据是否录入，或尝试具体方向(如'检查应收账款''分析本月收款').", None

    data_blocks = []
    for r in results:
        block = f"\n## {r.get('_alias', r.get('dataset_label',''))}\n"
        block += f"数据源: {r.get('dataset_label','')} | 指标: {r.get('metric_label','')} ({r.get('agg','')})\n"
        block += "数据明细:\n"
        for row in r.get("table", [])[:15]:
            dim = row.get("dim", "")
            vals = {k: v for k, v in row.items() if k != "dim"}
            block += f"  {dim}: " + " | ".join(f"{k}={A._fmt_val(v)}" for k, v in vals.items()) + "\n"
        data_blocks.append(block)
    failed_note = ""
    if failed:
        failed_note = "\n\n以下项未能执行: " + ", ".join(f"{f['alias']}({f['reason']})" for f in failed)

    sys = _fin_overview_prompt()
    if memory_prompt:
        sys += memory_prompt
    data_text = "\n".join(data_blocks) + failed_note
    if web_context:
        data_text += f"\n\n{web_context}"
    messages = [
        {"role": "system", "content": sys},
        {"role": "user", "content": f"以下是公司财务数据:\n\n{data_text}\n\n请做财务+税务问题诊断。"},
    ]
    try:
        reply = llm.chat(settings.DEEPSEEK_MODEL_PRO, messages, temperature=0.3, max_tokens=16384, timeout=120.0)
        if not reply or not reply.strip():
            reply = A._simple_format(results[0]) + failed_note
    except Exception as e:
        logger.warning(f"财务诊断报告生成失败: {e}")
        reply = A._simple_format(results[0]) + failed_note

    overview_data = {
        "dataset_label": "财务诊断", "rows_label": "多维度", "cols_label": "多数据源",
        "metric_label": "多指标", "agg": "多聚合", "table": [],
        "_overview_results": [{
            "alias": r.get("_alias"), "dataset": r.get("dataset_label"),
            "metric_label": r.get("metric_label", ""), "agg": r.get("agg", ""),
            "rows_label": r.get("rows_label", ""), "chart": r.get("chart"),
            "chart_recommend": r.get("chart_recommend"), "table": r.get("table", [])[:10],
        } for r in results],
    }
    return reply, overview_data


class AnalyzeIn(BaseModel):
    text: str
    conversation_id: Optional[int] = None
    history: list = []


# ============ 会话管理(scope=finance 隔离) ============
class ConvCreate(BaseModel):
    title: str = "新对话"
class ConvRename(BaseModel):
    title: str


@router.get("/conversations")
def list_conversations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    convs = db.query(AIConversation).filter(
        AIConversation.user_id == user.id, AIConversation.scope == SCOPE
    ).order_by(AIConversation.updated_at.desc()).all()
    return Resp.ok([{"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat() if c.updated_at else None,
                     "message_count": len(c.messages)} for c in convs])


@router.post("/conversations")
def create_conversation(body: ConvCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = AIConversation(user_id=user.id, title=body.title or "新对话", scope=SCOPE)
    db.add(c); db.commit(); db.refresh(c)
    return Resp.ok({"id": c.id, "title": c.title})


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = db.query(AIConversation).filter(AIConversation.id == conv_id, AIConversation.user_id == user.id).first()
    if not c: raise HTTPException(404, "会话不存在")
    db.delete(c); db.commit()
    return Resp.ok({"ok": True})


@router.put("/conversations/{conv_id}")
def rename_conversation(conv_id: int, body: ConvRename, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = db.query(AIConversation).filter(AIConversation.id == conv_id, AIConversation.user_id == user.id).first()
    if not c: raise HTTPException(404, "会话不存在")
    c.title = body.title; db.commit()
    return Resp.ok({"ok": True})


@router.get("/conversations/{conv_id}/messages")
def load_messages(conv_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = db.query(AIConversation).filter(AIConversation.id == conv_id, AIConversation.user_id == user.id).first()
    if not c: raise HTTPException(404, "会话不存在")
    msgs = db.query(AIMessage).filter(AIMessage.conversation_id == conv_id).order_by(AIMessage.created_at.asc()).all()
    return Resp.ok([{"id": m.id, "role": m.role, "text": m.content, "type": m.data_type,
                     "extra": m.extra, "created_at": m.created_at.isoformat() if m.created_at else None} for m in msgs])


@router.get("/memories")
def list_memories(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    mems = db.query(A.models.AIMemory).filter(A.models.AIMemory.user_id == user.id, A.models.AIMemory.is_active == True).all() if hasattr(A, "models") else []
    return Resp.ok([])


def _finance_stream_gen(body: AnalyzeIn, auth_user_id: int):
    sess = SessionLocal()
    ai_answer_buf = []
    try:
        text = body.text.strip()
        conv = None; conv_id = body.conversation_id
        if conv_id:
            conv = sess.query(AIConversation).filter(AIConversation.id == conv_id, AIConversation.user_id == auth_user_id).first()
        if not conv_id or not conv:
            conv = AIConversation(user_id=auth_user_id, title=text[:20] or "新对话", scope=SCOPE)
            sess.add(conv); sess.flush(); conv_id = conv.id
        sess.add(AIMessage(conversation_id=conv_id, role="user", content=text)); sess.commit()

        db_history = sess.query(AIMessage).filter(AIMessage.conversation_id == conv_id).order_by(AIMessage.created_at.desc()).limit(10).all()
        db_history.reverse()
        history_for_llm = []
        for m in db_history:
            entry = {"role": m.role, "text": m.content, "data_type": m.data_type or ""}
            if m.extra:
                try: entry["pivot_data"] = json.loads(m.extra)
                except Exception: pass
            history_for_llm.append(entry)

        memories = A._load_user_memories(auth_user_id, sess)
        memory_prompt = A._build_memory_prompt(memories)

        yield A._sse("stage", {"stage": "intent", "msg": "财务助手正在理解你的问题..."})

        schema = A._build_schema_text(text)
        intent_result = None
        for ikind, ival in llm.parse_intent_stream(text, schema, history_for_llm):
            if ikind == "thinking":
                yield A._sse("thinking", {"delta": ival})
            elif ikind == "result":
                intent_result = ival
        intent_result = intent_result or {}

        reply_text, data_type, result, llm_used, err_msg = "", "", None, False, ""

        from app.core import web_search as ws
        web_ctx, web_results = "", []
        if ws.should_search_web(text):
            yield A._sse("stage", {"stage": "web", "msg": "正在联网搜索最新财税政策..."})
            web_results = ws.search(text + " 财税政策 行业现状", max_results=5)
            if web_results:
                web_ctx = ws.format_results(web_results)
                yield A._sse("web", {"results": web_results})

        tasks = intent_result.get("tasks") if isinstance(intent_result, dict) else None

        # 多任务: 财务综合体检, 并行执行
        if tasks and len(tasks) > 1:
            yield A._sse("stage", {"stage": "query", "msg": "正在执行财务综合体检..."})
            reply_text, overview_data = _fin_overview(sess, memory_prompt, tasks, web_context=web_ctx)
            if not overview_data:
                data_type = "chat"
                yield A._sse("answer", {"delta": reply_text})
            else:
                data_type = "pivot"; result = overview_data
                yield A._sse("stage", {"stage": "report", "msg": "正在生成财务诊断报告..."})
                yield A._sse("answer", {"delta": reply_text})

        elif tasks and len(tasks) == 1:
            intent = A._validate_intent(dict(tasks[0]))
            if intent:
                llm_used = True
                yield A._sse("stage", {"stage": "query", "msg": "正在查询财务数据..."})
                yield A._sse("plan", A._plan_payload(intent))
                result = build_pivot(sess, dataset=intent["dataset"], rows_dim=intent["rows_dim"],
                                     cols_dim=intent.get("cols_dim"), metric=intent["metric"],
                                     agg=intent["agg"], filters=intent.get("filters", []))
                if "error" in result:
                    reply_text = f"查询出错: {result['error']}。请换个角度。"
                    data_type = "error"; err_msg = reply_text
                    yield A._sse("answer", {"delta": reply_text})
                elif not result.get("table"):
                    reply_text = f"查询完成但无匹配数据。数据源: {result.get('dataset_label','')}\n筛选可能过严,请放宽。"
                    data_type = "error"; err_msg = reply_text
                    yield A._sse("answer", {"delta": reply_text})
                else:
                    yield A._sse("stage", {"stage": "report", "msg": "正在出具财务/税务建议..."})
                    # 复用报告 prompt 的数据块构造，仅把 system 换成财务专职
                    _, user_c = A._build_report_messages(text, result, "", web_context=web_ctx)
                    sys = _fin_report_system()
                    if memory_prompt:
                        sys += memory_prompt
                    data_type = "pivot"
                    yield from A._emit_report(system_content=sys, user_content=user_c,
                                              model=settings.DEEPSEEK_MODEL_PRO,
                                              temperature=0.3, max_tokens=16384, timeout=120.0,
                                              reply_holder=None, text_holder=ai_answer_buf)
            else:
                tasks = None

        # 无任务: 财务专职对话
        if not tasks:
            yield A._sse("stage", {"stage": "report", "msg": "正在回复..."})
            pd = A._get_last_pivot_data(history_for_llm)
            if pd:
                sys, user_c = A._discuss_messages(text, history_for_llm, pd, "", web_context=web_ctx)
                sys = _fin_discuss_system(web_context=web_ctx)
                data_type = "discuss"
                yield from A._emit_report(system_content=sys, user_content=user_c,
                                          model=settings.DEEPSEEK_MODEL_PRO,
                                          temperature=0.3, max_tokens=8192, timeout=120.0,
                                          reply_holder=None, text_holder=ai_answer_buf)
            else:
                sys = _fin_chat_system(web_context=web_ctx)
                user_c = f"对话历史:{A._hist_ctx(history_for_llm)}\n\n"
                if web_ctx: user_c += f"{web_ctx}\n\n"
                user_c += f"用户: {text}"
                data_type = "chat"
                yield from A._emit_report(system_content=sys, user_content=user_c,
                                          model=settings.DEEPSEEK_MODEL_PRO,
                                          temperature=0.7, max_tokens=8192, timeout=60.0,
                                          reply_holder=None, text_holder=ai_answer_buf)

        content_out = "".join(ai_answer_buf) or reply_text or err_msg or ""
        extra_json = None
        if data_type == "pivot" and result:
            extra_json = json.dumps(result, ensure_ascii=False)
        sess.add(AIMessage(conversation_id=conv_id, role="ai", content=content_out, data_type=data_type, extra=extra_json))
        conv.updated_at = datetime.utcnow()
        try: A._extract_memory(text, content_out, auth_user_id, sess)
        except Exception: pass
        sess.commit()

        done = {"conversation_id": conv_id, "type": data_type, "query_text": text}
        if data_type == "pivot" and result:
            done["pivot_data"] = result; done["llm_used"] = llm_used
        elif data_type == "discuss":
            done["pivot_data"] = A._get_last_pivot_data(history_for_llm)
        yield A._sse("done", done)
    except Exception as e:
        logger.warning(f"[ai-finance] 失败: {e}", exc_info=True)
        yield A._sse("answer", {"delta": f"财务分析失败: {e}"})
        yield A._sse("done", {"conversation_id": None, "type": "error", "query_text": body.text})
    finally:
        sess.close()


@router.post("/stream")
def finance_stream(body: AnalyzeIn, user: User = Depends(get_current_user)):
    return StreamingResponse(_finance_stream_gen(body, user.id), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})