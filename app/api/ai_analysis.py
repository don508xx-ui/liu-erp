"""
AI分析模块 - 持久化对话 + 记忆管理
================================
1. 对话持久化: SQLite存储会话/消息,刷新不丢
2. 记忆管理: 自动提取用户偏好注入LLM prompt
3. 意图解析 → 透视执行 → 报告生成
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import inspect as sa_inspect
from pydantic import BaseModel
from typing import Optional
import json
import logging
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from app.core.db import get_db, engine, SessionLocal
from app.core.auth import get_current_user
from app.models.system import User
from app.models.ai import AIConversation, AIMessage, AIMemory
from app.schemas import Resp
from app.core.pivot import build_pivot, list_datasets
from app.api.analysis import kpi as calc_kpi, receivable_aging, payment_schedules
from app.core import llm
from app.core import web_search as ws
from app.config import settings

router = APIRouter(prefix="/api/ai", tags=["ai_analysis"])
logger = logging.getLogger(__name__)


# ============ 会话管理 ============
class ConvCreate(BaseModel):
    title: str = "新对话"

class ConvRename(BaseModel):
    title: str


@router.get("/conversations")
def list_conversations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    convs = db.query(AIConversation).filter(
        AIConversation.user_id == user.id
    ).order_by(AIConversation.updated_at.desc()).all()
    return Resp.ok([{
        "id": c.id,
        "title": c.title,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "message_count": len(c.messages),
    } for c in convs])


@router.post("/conversations")
def create_conversation(body: ConvCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = AIConversation(user_id=user.id, title=body.title or "新对话")
    db.add(c)
    db.commit()
    db.refresh(c)
    return Resp.ok({"id": c.id, "title": c.title})


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = db.query(AIConversation).filter(AIConversation.id == conv_id, AIConversation.user_id == user.id).first()
    if not c:
        raise HTTPException(404, "会话不存在")
    db.delete(c)
    db.commit()
    return Resp.ok({"ok": True})


@router.put("/conversations/{conv_id}")
def rename_conversation(conv_id: int, body: ConvRename, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = db.query(AIConversation).filter(AIConversation.id == conv_id, AIConversation.user_id == user.id).first()
    if not c:
        raise HTTPException(404, "会话不存在")
    c.title = body.title
    db.commit()
    return Resp.ok({"ok": True})


@router.get("/conversations/{conv_id}/messages")
def load_messages(conv_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = db.query(AIConversation).filter(AIConversation.id == conv_id, AIConversation.user_id == user.id).first()
    if not c:
        raise HTTPException(404, "会话不存在")
    msgs = db.query(AIMessage).filter(
        AIMessage.conversation_id == conv_id
    ).order_by(AIMessage.created_at.asc()).all()
    return Resp.ok([{
        "id": m.id,
        "role": m.role,
        "text": m.content,
        "type": m.data_type,
        "extra": m.extra,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    } for m in msgs])


# ============ 记忆管理 ============
@router.get("/memories")
def list_memories(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    mems = db.query(AIMemory).filter(
        AIMemory.user_id == user.id, AIMemory.is_active == True
    ).order_by(AIMemory.hit_count.desc()).all()
    return Resp.ok([{
        "id": m.id,
        "category": m.category,
        "content": m.content,
        "keywords": m.keywords,
        "hit_count": m.hit_count,
    } for m in mems])


@router.delete("/memories/{mem_id}")
def delete_memory(mem_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = db.query(AIMemory).filter(AIMemory.id == mem_id, AIMemory.user_id == user.id).first()
    if not m:
        raise HTTPException(404, "记忆不存在")
    m.is_active = False
    db.commit()
    return Resp.ok({"ok": True})


def _extract_memory(text: str, reply: str, user_id: int, db: Session):
    """从对话中提取用户偏好/模式,存入记忆"""
    patterns = [
        (r"(?:以后|记住|默认|习惯|偏好|总是|一律|都要|请|帮我).*?(?:按|用|以|要|走|选)", "pattern"),
        (r"(?:我喜欢|我偏好|我习惯|我倾向)", "preference"),
        (r"(?:不要|别|避免|禁止).*?(?:格式|排序|分组|显示|展示)", "preference"),
    ]
    content = text.strip()
    for pat, cat in patterns:
        import re
        m = re.search(pat, content)
        if m:
            mem = AIMemory(
                user_id=user_id,
                category=cat,
                content=content[:200],
                keywords=",".join(set(content[i:i+2] for i in range(len(content)-1))),
            )
            db.add(mem)
            db.commit()
            return


def _load_user_memories(user_id: int, db: Session) -> list:
    """加载用户活跃记忆"""
    return db.query(AIMemory).filter(
        AIMemory.user_id == user_id, AIMemory.is_active == True
    ).order_by(AIMemory.hit_count.desc()).limit(20).all()


def _build_memory_prompt(memories: list) -> str:
    """构建记忆注入文本"""
    if not memories:
        return ""
    lines = ["\n\n## 用户偏好/习惯(请遵循):"]
    for m in memories:
        lines.append(f"- [{m.category}] {m.content}")
    return "\n".join(lines)


# ============ 核心分析逻辑 ============
_db_schema_cache = {"text": None, "ts": 0}

def _build_db_schema_text() -> str:
    """动态从数据库反射所有表结构，5分钟缓存"""
    now = _time.time()
    if _db_schema_cache["text"] and (now - _db_schema_cache["ts"]) < 300:
        return _db_schema_cache["text"]

    insp = sa_inspect(engine)
    tables = sorted(insp.get_table_names())
    # 排除系统内部表
    sys_prefix = ("ai_", "audit_", "notification_", "event_log", "agent_")
    biz_tables = [t for t in tables if not t.startswith(sys_prefix)]

    lines = [f"数据库共{len(tables)}张表(其中业务表{len(biz_tables)}张)，完整表结构如下:"]
    for t in biz_tables:
        cols = insp.get_columns(t)
        col_list = ", ".join(c["name"] for c in cols)
        lines.append(f"  {t}({len(cols)}列): {col_list}")

    text = "\n".join(lines)
    _db_schema_cache["text"] = text
    _db_schema_cache["ts"] = now
    return text


def _build_schema_text(query: str = "") -> str:
    """构建LLM可用的数据源schema(检索式注入，对齐顶尖Text2SQL的schema linking)

    核心数据源始终完整注入；自动数据源按用户问题关键词检索top-N注入。
    避免全量schema导致的token膨胀和注意力稀释(RSL-SQL/GRAST-SQL实践)。
    """
    from app.core.pivot import _datasets, _auto_datasets
    manual = _datasets()
    auto = _auto_datasets()

    def fmt_ds(key, v, compact=False):
        dims = []
        for dk, dv in v["dims"].items():
            lbl = dv["label"] if isinstance(dv, dict) else dv
            dims.append(f"{dk}({lbl})")
        # 跨表JOIN维度(关联表字段)
        for jk, jv in v.get("joins", {}).items():
            dims.append(f"{jk}({jv['label']})")
        metrics = []
        for mk, mv in v["metrics"].items():
            lbl = mv["label"] if isinstance(mv, dict) else mv
            metrics.append(f"{mk}({lbl})")
        tf = v["time_field"]
        if tf:
            dims += [f"{tf}:month", f"{tf}:quarter", f"{tf}:year"]
        if compact:
            return f"- {key}({v['label']}): 维度[{','.join(dims[:8])}] 指标[{','.join(metrics[:6])}]"
        return f"- {key}({v['label']}): 维度[{','.join(dims)}] 指标[{','.join(metrics)}]"

    lines = ["【可分析数据源(全部支持透视查询)】"]
    # 1. 核心数据源始终完整注入
    for key, v in manual.items():
        lines.append(fmt_ds(key, v))

    # 2. 自动数据源: 按问题关键词检索 top-N(不超过12个)
    if query.strip():
        q = query.lower()
        scored = []
        for key, v in auto.items():
            # 候选词: 中文label + 英文表名 + 中文维度label
            cands = [v["label"], key, key.rstrip("s")]
            cands += [dv["label"] if isinstance(dv, dict) else str(dv)
                      for dv in v["dims"].values()][:6]
            score = sum(1 for c in cands if c and str(c).lower() in q)
            if score > 0:
                scored.append((score, key, v))
        scored.sort(key=lambda x: -x[0])
        top = scored[:12]
        if top:
            lines.append("- 以下为与您问题相关的业务表(自动生成,可直接分析):")
            for _, key, v in top:
                lines.append(fmt_ds(key, v, compact=True))
    else:
        # 无问题上下文时只列自动数据源清单(不展开字段)
        if auto:
            names = [f"{k}({v['label']})" for k, v in auto.items()]
            lines.append("- 其他业务表数据源(可直接分析): " + ", ".join(names[:40]))

    schema_text = "\n".join(lines)

    # 3. 能力边界说明
    schema_text += "\n\n分析能力说明:"
    schema_text += "\n- 以上数据源(含自动生成)都支持维度x指标透视分析"
    schema_text += "\n- 客户/供应商/公司等主数据表可直接统计数量、按字段分组"
    schema_text += "\n- 支持时间维度分组: 月/季/年/周"
    schema_text += "\n- 支持外键模糊查询(如按客户名/销售名筛选，用like操作)"
    schema_text += "\n- 支持枚举值中文转英文(如'草稿'→'DRAFT')"
    schema_text += "\n- 支持跨表关联维度(如订单按客户行业/客户名称分析，维度中标注了来源)"
    schema_text += "\n- 不支持自定义SQL或子查询"
    schema_text += "\n- 聚合方式: count(计数)/sum(求和)/avg(平均)"
    schema_text += "\n- 筛选操作符: eq/ne/gt/lt/ge/le/like/in/between"
    return schema_text


def _overview_analysis(db: Session, memory_prompt: str, overview_tasks: list = None,
                       web_context: str = "") -> tuple:
    """综合分析: 根据LLM生成的分析任务动态执行多个分析并汇总报告"""
    if web_context:
        web_context = "【网络参考数据(用于行业现状对比，非本系统数据，仅供参考)】\n" + web_context

    # 无任务时不做任何预设分析(避免硬编码业务板块), 直接提示
    if not overview_tasks:
        return "未获得有效的分析任务，无法执行综合分析。请换一种描述方式，如'分析订单情况'或'查看财务收支'。", None

    def _run_one(task):
        # 每个任务用独立 Session, 支持线程并行读库
        s = SessionLocal()
        try:
            result = build_pivot(
                s, dataset=task["dataset"], rows_dim=task["rows_dim"],
                cols_dim=None, metric=task["metric"],
                agg=task["agg"], filters=task.get("filters", []),
            )
            if "error" in result:
                return {"failed": {"alias": task.get("alias", task["dataset"]), "reason": result["error"]}}
            if result.get("table"):
                result["_alias"] = task.get("alias", result.get("dataset_label", ""))
                return {"ok": result}
            return {"failed": {"alias": task.get("alias", task["dataset"]), "reason": "查询结果为空"}}
        except Exception as e:
            logger.warning(f"综合分析 {task['dataset']} 失败: {e}")
            return {"failed": {"alias": task.get("alias", task["dataset"]), "reason": str(e)}}
        finally:
            s.close()

    results = []
    failed_tasks = []
    with ThreadPoolExecutor(max_workers=len(overview_tasks) or 1) as pool:
        for out in pool.map(_run_one, overview_tasks):
            if "ok" in out:
                results.append(out["ok"])
            else:
                failed_tasks.append(out["failed"])

    if not results:
        # 明确告知用户为什么查不到
        reasons = "; ".join(f"{f['alias']}({f['reason']})" for f in failed_tasks) if failed_tasks else "未知原因"
        return f"无法完成综合分析。原因: {reasons}。请检查数据是否已录入，或尝试指定具体分析方向(如'分析订单情况'、'查看财务收支')。", None

    # 构建数据上下文
    data_blocks = []
    for r in results:
        block = f"\n## {r.get('_alias', r.get('dataset_label', ''))}\n"
        block += f"数据源: {r.get('dataset_label', '')}\n"
        block += f"指标: {r.get('metric_label', '')} ({r.get('agg', '')})\n"
        # 添加图表推荐信息
        chart_rec = r.get("chart_recommend", {})
        if chart_rec and chart_rec.get("show"):
            block += f"📊 图表推荐: {chart_rec.get('reason', '数据适合可视化')}\n"
        elif chart_rec:
            block += f"📊 图表建议: {chart_rec.get('reason', '')}\n"
        block += "数据明细:\n"
        for row in r.get("table", [])[:15]:
            dim = row.get("dim", "")
            vals = {k: v for k, v in row.items() if k != "dim"}
            block += f"  {dim}: " + " | ".join(f"{k}={_fmt_val(v)}" for k, v in vals.items()) + "\n"
        data_blocks.append(block)

    # 如果有部分失败，也告知
    failed_note = ""
    if failed_tasks:
        failed_note = "\n\n以下分析项未能执行: " + ", ".join(f"{f['alias']}({f['reason']})" for f in failed_tasks)

    system_prompt = """你是资深企业经营分析师（CFO级别）。请基于提供的数据，生成一份有深度的经营状况分析报告。

核心要求:
1. 必须有分析解读，不能只列数据。每个数据都要说明其业务含义
2. 结构:
   - 【经营总览】一句话总结当前经营状况好坏
   - 【各板块深度分析】每个业务板块要说明: 数据表现如何、好/在哪里、问题出在哪里
   - 【板块联动分析】分析订单、生产、财务之间的关联和因果关系
   - 【关键发现】用⚠️标注3-5个最重要的发现或风险点
   - 【行动建议】用✅标注具体的、可执行的改进建议

3. 分析维度:
   - 横向对比: 各状态/类别之间的占比是否合理
   - 纵向趋势: 月度数据是增长还是下滑
   - 结构质量: 收入结构、客户结构、业务结构是否健康
   - 风险识别: 账龄、滞销、积压等潜在风险

4. 格式:
   - 用markdown格式
   - 关键数字加粗
   - 用表格展示数据对比，不要使用mermaid或代码块生成图表

5. 用中文,专业但不晦涩,直接输出报告,不要寒暄

6. 思考推理规则(thinking):只输出3个关键判断要点,每个要点一行,严禁过程罗列('首先...其次...然后...最后')、严禁自我验算对话('等等/让我想想/我再检查'),直接给数据解读结论。

7. 图表使用规则: 如果系统为某项分析推荐了图表(下方会标注📊),在报告相应位置用'📈 建议查看下方图表'引导用户,但不要生成任何图表代码(系统会自动渲染)。"""

    if memory_prompt:
        system_prompt += memory_prompt

    data_text = "\n".join(data_blocks) + failed_note
    if web_context:
        data_text += f"\n\n{web_context}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"以下是公司各业务板块的最新数据:\n\n{data_text}\n\n请结合内部数据，生成经营状况综合报告。若有网络参考数据，可与其做行业现状对比。"}
    ]

    try:
        reply = llm.chat(settings.DEEPSEEK_MODEL_PRO, messages, temperature=0.3, max_tokens=16384, timeout=120.0)
        if not reply or not reply.strip():
            reply = _simple_format(results[0]) + failed_note
    except Exception as e:
        logger.warning(f"综合报告生成失败: {e}")
        reply = _simple_format(results[0]) + failed_note

    # 构建综合pivot数据供后续discuss使用
    overview_data = {
        "dataset_label": "经营综合",
        "rows_label": "多维度",
        "cols_label": "多数据源",
        "metric_label": "综合指标",
        "agg": "多聚合",
        "table": [],
        "_overview_results": [
            {
                "alias": r.get("_alias"),
                "dataset": r.get("dataset_label"),
                "metric_label": r.get("metric_label", ""),
                "agg": r.get("agg", ""),
                "rows_label": r.get("rows_label", ""),
                "chart": r.get("chart"),
                "chart_recommend": r.get("chart_recommend"),
                "table": r.get("table", [])[:10],
            }
            for r in results
        ],
    }

    return reply, overview_data


def _detect_intent(text: str, history: list, memory_prompt: str = "") -> str:
    """LLM判断意图类型: analyze(新数据分析) / discuss(基于之前分析讨论) / chat(自然对话)"""
    ctx = ""
    for h in history[-8:]:
        role = "用户" if h.get("role") == "user" else "AI"
        content = h.get("text", "")[:200]
        data_type = h.get("data_type", "")
        tag = f"[{data_type}]" if data_type else ""
        ctx += f"\n{tag} {role}: {content}"
    
    prompt = f"""请判断用户的意图类型。

对话历史:{ctx}

用户最新消息: {text}

类型说明:
- "analyze": 用户想查询或分析业务数据(订单、财务、工单、库存等)
- "discuss": 对话历史中有[pivot]分析结果，用户在追问、讨论该数据
- "chat": 闲聊、打招呼、问身份/能力、常识性问题

请直接返回这三个词中的一个: analyze / discuss / chat
不要返回任何其他内容。"""
    
    try:
        msg = [{"role": "user", "content": prompt}]
        result = llm.chat(settings.DEEPSEEK_MODEL_FAST, msg, temperature=0.0, max_tokens=5)
        result = result.strip().lower()
        if "discuss" in result:
            return "discuss"
        if "analyze" in result:
            return "analyze"
        return "chat"
    except:
        return "chat"


def _get_last_pivot_data(history: list) -> dict:
    """从对话历史中找到最近一次pivot分析的数据"""
    for h in reversed(history):
        if h.get("data_type") == "pivot" and h.get("pivot_data"):
            return h["pivot_data"]
    return None


def _discuss_reply(text: str, history: list, memory_prompt: str = "") -> str:
    """基于之前的分析数据进行讨论"""
    pivot_data = _get_last_pivot_data(history)
    
    if not pivot_data:
        return "抱歉,我没有找到之前的分析数据。请先告诉我您想分析什么,我可以为您生成新的分析报告。"
    
    ctx = ""
    for h in history[-6:]:
        role = "用户" if h.get("role") == "user" else "AI"
        data_type = h.get("data_type", "")
        tag = f"[{data_type}]" if data_type else ""
        ctx += f"\n{tag} {role}: {h.get('text', '')[:200]}"
    
    # 构建数据上下文
    dataset_label = pivot_data.get('dataset_label', '')
    rows_label = pivot_data.get('rows_label', '')
    cols_label = pivot_data.get('cols_label', '无')
    metric_label = pivot_data.get('metric_label', '')
    agg = pivot_data.get('agg', '')
    table = pivot_data.get('table', [])
    
    data_context = f"""
最近分析的数据:
- 数据源: {dataset_label}
- 行维度: {rows_label}
- 列维度: {cols_label}
- 指标: {metric_label} (聚合方式: {agg})
- 数据明细:
"""
    for row in table[:20]:
        dim = row.get("dim", "")
        vals = {k: v for k, v in row.items() if k != "dim"}
        data_context += f"  {dim}: " + " | ".join(f"{k}={_fmt_val(v)}" for k, v in vals.items()) + "\n"
    
    system_prompt = f"""你是资深财务分析师。基于下面提供的最近分析数据,回答用户的问题。
规则:
1. 只能基于提供的数据回答,不要编造数字
2. 如果数据不足以回答,明确说明
3. 用中文,简洁有力,可以用markdown表格呈现数据对比,不要使用mermaid
4. 在数据中发现异常或值得注意的点时,主动指出
"""
    if memory_prompt:
        system_prompt += memory_prompt
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{data_context}\n\n对话历史:{ctx}\n\n用户追问: {text}"}
    ]
    
    try:
        return llm.chat(settings.DEEPSEEK_MODEL_PRO, messages, temperature=0.3, max_tokens=16384, timeout=120.0)
    except Exception as e:
        logger.warning(f"Discuss回复生成失败: {e}")
        return "抱歉,基于之前数据分析时出现错误。您可以重新提出分析需求。"


def _chat_reply(text: str, history: list, memory_prompt: str = "") -> str:
    """LLM生成自然对话回复，含数据库表结构信息"""
    ctx = ""
    for h in history[-5:]:
        role = "user" if h.get("role") == "user" else "assistant"
        ctx += f"\n{role}: {h.get('text','')[:200]}"

    db_schema = _build_db_schema_text()
    now_ctx = llm._now_context()
    system_prompt = f"""你是ERP系统的智能助手。用中文简短、友好地回答用户问题。

{now_ctx}

以下是系统数据库的完整表结构信息（动态获取，实时准确）:

{db_schema}

当用户询问数据库表结构、表数量、字段信息时，直接基于上述信息回答。
不要说"无法访问数据库"，你确实拥有上述表结构信息。
对于时间相关问题(如今天几号、星期几、本月等),直接使用上方【系统时间】中的信息回答,不要说"无法获取日期"。
但对于具体的业务数据记录（如某客户订单金额），需要通过分析功能查询，不要编造。"""
    if memory_prompt:
        system_prompt += memory_prompt

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"对话历史:{ctx}\n\n用户: {text}"}
    ]

    try:
        return llm.chat(settings.DEEPSEEK_MODEL_PRO, messages, temperature=0.7, max_tokens=8192, timeout=60.0)
    except Exception as e:
        logger.warning(f"Chat回复生成失败: {e}")
        return "抱歉,我暂时无法回答这个问题。如果您需要分析业务数据,请直接描述您想了解的内容。"


class AnalyzeIn(BaseModel):
    text: str
    conversation_id: Optional[int] = None
    history: list = []


@router.post("/analyze")
def analyze(body: AnalyzeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "请输入分析指令")

    # 1. 获取或创建会话
    conv_id = body.conversation_id
    if conv_id:
        conv = db.query(AIConversation).filter(
            AIConversation.id == conv_id, AIConversation.user_id == user.id
        ).first()
    if not conv_id or not conv:
        conv = AIConversation(user_id=user.id, title=text[:20] if text else "新对话")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        conv_id = conv.id

    # 2. 保存用户消息
    user_msg = AIMessage(conversation_id=conv_id, role="user", content=text)
    db.add(user_msg)

    # 3. 从DB加载历史(最近10条，带data_type和extra)
    db_history = db.query(AIMessage).filter(
        AIMessage.conversation_id == conv_id
    ).order_by(AIMessage.created_at.desc()).limit(10).all()
    db_history.reverse()
    history_for_llm = []
    for m in db_history:
        entry = {"role": m.role, "text": m.content, "data_type": m.data_type or ""}
        if m.extra:
            try:
                entry["pivot_data"] = json.loads(m.extra)
            except:
                pass
        history_for_llm.append(entry)

    # 4. 加载用户记忆注入prompt
    memories = _load_user_memories(user.id, db)
    memory_prompt = _build_memory_prompt(memories)

    # 5. LLM意图解析(带记忆) - 无意图预判，让LLM自己判断
    reply_text = ""
    data_type = ""
    result = None
    llm_used = False

    try:
        schema = _build_schema_text(text)
        intent_result = llm.parse_intent(text + memory_prompt, schema, history_for_llm)
        
        if isinstance(intent_result, dict) and intent_result.get("tasks"):
            tasks = intent_result["tasks"]
            if len(tasks) > 1:
                # 综合分析(跨板块经营概况等)
                reply_text, overview_data = _overview_analysis(db, memory_prompt, tasks, web_context="")
                data_type = "pivot"
                result = overview_data
                if not overview_data:
                    data_type = "chat"
            elif len(tasks) == 1:
                # 单一明确查询
                intent = _validate_intent(dict(tasks[0]))
                if intent:
                    llm_used = True
                    result = build_pivot(
                        db, dataset=intent["dataset"], rows_dim=intent["rows_dim"],
                        cols_dim=intent.get("cols_dim"), metric=intent["metric"],
                        agg=intent["agg"], filters=intent.get("filters", []),
                    )
                    if "error" in result:
                        reply_text = f"分析出错: {result['error']}。请检查数据源和字段是否正确，或尝试换个分析角度。"
                        data_type = "error"
                    elif not result.get("table"):
                        # 查询成功但无数据
                        reply_text = (f"查询已完成，但未找到匹配的数据。\n"
                                     f"数据源: {result.get('dataset_label','')}\n"
                                     f"维度: {result.get('rows_label','')}\n"
                                     f"筛选条件可能过严，请尝试放宽筛选范围或更换分析维度。")
                        data_type = "error"
                    else:
                        reply = None
                        try:
                            reply = llm.generate_report(text, result, memory_prompt)
                        except Exception as e:
                            logger.warning(f"LLM报告生成失败: {e}")
                        if not reply:
                            reply = _simple_format(result)
                        reply_text = reply
                        data_type = "pivot"
                else:
                    reply_text = ("抱歉，无法理解您的分析需求。您可以尝试：\n"
                                  "1. 指定数据源和维度，如\"分析各客户订单金额\"\n"
                                  "2. 指定筛选条件，如\"本月订单状态分布\"\n"
                                  "3. 请求综合分析，如\"整体经营状况\"")
                    data_type = "chat"
        else:
            # 纯对话(闲聊/追问讨论/意图不清)
            if _get_last_pivot_data(history_for_llm):
                reply_text = _discuss_reply(text, history_for_llm, memory_prompt)
                data_type = "discuss"
            else:
                reply_text = _chat_reply(text, history_for_llm, memory_prompt)
                data_type = "chat"
    except Exception as e:
        logger.warning(f"LLM意图解析失败: {e}")
        reply_text = _chat_reply(text, history_for_llm, memory_prompt)
        data_type = "chat"

    # 10. 保存AI回复(pivot数据存入extra供后续discuss使用)
    extra_json = None
    if data_type == "pivot":
        extra_json = json.dumps(result, ensure_ascii=False)
    
    ai_msg = AIMessage(
        conversation_id=conv_id, role="ai",
        content=reply_text, data_type=data_type,
        extra=extra_json
    )
    db.add(ai_msg)
    conv.updated_at = datetime.utcnow()

    # 11. 自动提取记忆
    try:
        _extract_memory(text, reply_text, user.id, db)
    except Exception:
        pass

    db.commit()

    # 12. 返回结果
    result_data = {
        "type": data_type,
        "query_text": text,
        "reply": reply_text,
        "conversation_id": conv_id,
    }
    if data_type == "pivot":
        result_data["pivot_data"] = result
        result_data["llm_used"] = llm_used
    elif data_type == "discuss":
        result_data["pivot_data"] = _get_last_pivot_data(history_for_llm)
    return Resp.ok(result_data)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _parse_json_tolerant(s: str) -> dict:
    """流式意图解析: 从累积文本中容错提取JSOV"""
    s = s.strip()
    if not s:
        return {}
    if s.startswith("```"):
        s = s.split("```")[1]
        if s.startswith("json"):
            s = s[4:]
    a, b = s.find("{"), s.rfind("}")
    if a >= 0 and b > a:
        s = s[a:b + 1]
    try:
        return json.loads(s)
    except Exception:
        return {}


def _plan_payload(intent: dict) -> dict:
    """把 intent 转成前端可直接显示的中文查询条件卡片数据"""
    try:
        from app.core.pivot import _datasets, _auto_datasets
        conf = {**_datasets(), **_auto_datasets()}.get(intent["dataset"])
    except Exception:
        conf = None
    AGGS = {"sum": "求和", "avg": "平均", "count": "计数", "max": "最大", "min": "最小"}
    TIME_SUFFIX = {"month": "按月", "quarter": "按季", "year": "按年", "day": "按天"}

    def dim_label(key):
        if not conf:
            return str(key)
        base = str(key)
        suf = ""
        if ":" in base:
            base, suf = base.rsplit(":", 1)
        for c in (conf.get("dims", {}), conf.get("time_dims", {}), conf.get("joins", {})):
            if base in c:
                x = c[base]
                lbl = x["label"] if isinstance(x, dict) else str(x)
                return lbl + (f"({TIME_SUFFIX.get(suf, suf)})" if suf else "")
        return str(key) + (f"({suf})" if suf else "")

    def metric_label(key):
        if conf and key in (conf.get("metrics", {}) or {}):
            x = conf["metrics"][key]
            return x["label"] if isinstance(x, dict) else str(x)
        return str(key)

    return {
        "dataset": intent["dataset"],
        "dataset_label": (conf.get("label", intent["dataset"]) if conf else intent["dataset"]),
        "rows_dim": intent.get("rows_dim"),
        "rows_label": dim_label(intent.get("rows_dim", "")),
        "cols_dim": intent.get("cols_dim"),
        "cols_label": dim_label(intent["cols_dim"]) if intent.get("cols_dim") else None,
        "metric": intent.get("metric"),
        "metric_label": metric_label(intent.get("metric", "")),
        "agg": intent.get("agg"),
        "agg_label": AGGS.get(intent.get("agg"), intent.get("agg", "")),
        "filters": intent.get("filters", []),
    }


def _refine_thinking(text: str) -> str:
    """精炼LLM的thinking: 删除过程罗列、自我对话、冗长推导, 只保留关键判断"""
    if not text:
        return text
    import re
    # 1. 删除行: 过程罗列/自我对话/查资料等无关行
    line_blacklist = [
        r'首先.{0,30}', r'其次.{0,30}', r'然后.{0,30}', r'最后.{0,30}',
        r'等等.{0,20}', r'让我想想.{0,20}', r'我再.{0,10}检查', r'我再.{0,10}看看',
        r'假设.{0,20}不对', r'不对.{0,15}应该', r'重新.{0,10}检查', r'再次.{0,10}确认',
        r'验证.{0,10}一下', r'这样.{0,10}看来', r'总体.{0,10}来说',
        r'需要.{0,6}查.{0,6}官.{0,2}网', r'需要.{0,6}查.{0,6}论.{0,2}坛',
        r'需要.{0,6}查.{0,6}开.{0,2}源', r'需要.{0,6}搜.{0,6}索',
        r'去.{0,2}查.{0,4}看', r'找.{0,2}相关',
        r'让我.{0,6}看.{0,6}看', r'让我.{0,6}分析.{0,6}下',
        r'我们.{0,4}可以.{0,6}看到', r'我们.{0,4}来.{0,6}分析',
        r'分析.{0,4}一下.{0,10}数据', r'看.{0,6}看.{0,10}数据',
    ]
    line_combined = "|".join(f"(?:{p})" for p in line_blacklist)
    lines = text.split("\n")
    kept = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # 整行匹配黑名单
        if re.match(line_combined, stripped, re.IGNORECASE):
            continue
        # 行内含查资料关键词也过滤
        if re.search(r'查.{0,10}官.{0,2}网|查.{0,6}论.{0,2}坛|查.{0,6}开.{0,2}源|Google|百度|Stack\s*Overflow', stripped, re.IGNORECASE):
            continue
        kept.append(line)
    result = "\n".join(kept)
    result = re.sub(r'\n{3,}', '\n\n', result)

    # 2. 对剩余内容做句子级精炼: 提取含关键判断词的句子
    if len(result) > 500:
        sentences = re.split(r'[。！？；;\n]', result)
        key_words = ['判断', '结论', '因此', '所以', '关键', '发现', '需要', '应该', '建议',
                     '必须', '考虑', '确认', '表明', '显示', '说明', '意味着',
                     '数据.{0,4}显示', '数据.{0,4}表明', '可见', '由此可见']
        filtered = []
        for s in sentences:
            s = s.strip()
            if not s or len(s) < 5:
                continue
            # 含关键判断词 或 含数据引用(数字+万/%)
            if any(w in s for w in key_words) or re.search(r'\d+\.?\d*[万%亿]', s):
                filtered.append(s)
        if len(filtered) >= 2:
            result = '\n'.join(filtered[:10])

    # 3. 删除重复的相似行(去除完全相同或高度相似的行)
    seen = set()
    final_lines = []
    for line in result.split("\n"):
        norm = re.sub(r'\s+', '', line)
        if norm and norm not in seen:
            seen.add(norm)
            final_lines.append(line)
    result = "\n".join(final_lines)
    return result


def _stream_report(system_content: str, user_content: str,
                   model: str, temperature: float = 0.3,
                   max_tokens: int = 16384, timeout: float = 120.0):
    """流式生成报告: yield ("thinking"/"content", delta) + 自动精炼thinking"""
    msgs = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]
    reasoning_buf = ""
    reasoning_flush_threshold = 40  # 每40字符精炼一次再推
    for kind, delta in llm.chat_stream(model, msgs, temperature=temperature,
                                       max_tokens=max_tokens, timeout=timeout):
        if kind == "reasoning":
            reasoning_buf += delta
            # 积攒到阈值再精炼推送
            if len(reasoning_buf) >= reasoning_flush_threshold:
                refined = _refine_thinking(reasoning_buf)
                if refined:
                    yield "thinking", refined
                reasoning_buf = ""
        else:
            # 冲刷剩余thinking
            if reasoning_buf:
                refined = _refine_thinking(reasoning_buf)
                if refined:
                    yield "thinking", refined
                reasoning_buf = ""
            yield kind, delta
    # 最终冲刷
    if reasoning_buf:
        refined = _refine_thinking(reasoning_buf)
        if refined:
            yield "thinking", refined


def _build_report_messages(text: str, pivot_data: dict, memory_prompt: str = "", web_context: str = "") -> tuple:
    """复用 generate_report 的 prompt 构造, 返回 (system, user) 供流式使用"""
    from app.core import llm as _llm
    table = pivot_data.get("table", [])
    if len(table) > 30:
        table = table[:30]
    lines = []
    for row in table:
        dim = row.get("dim", "")
        vals = {k: v for k, v in row.items() if k != "dim"}
        val_str = " | ".join(f"{k}={_fmt_val(v)}" for k, v in vals.items())
        lines.append(f"  {dim}: {val_str}")
    data_block = "\n".join(lines) if lines else "  (无数据)"

    # 获取图表推荐信息
    chart_rec = pivot_data.get("chart_recommend", {})
    chart_info = ""
    if chart_rec:
        if chart_rec.get("show"):
            chart_info = f"\n\n📊 图表推荐: 系统建议展示图表({chart_rec.get('reason', '')}),因为数据特点适合可视化呈现。"
        else:
            chart_info = f"\n\n📊 图表推荐: {chart_rec.get('reason', '')},本次分析不建议展示图表。"

    system_content = "你是资深财务分析师。严格遵循以下规则:\n"
    system_content += "1. 绝对不编造数据——报告中每个内部数字必须来自下方数据明细,不添加任何数据中没有的数值\n"
    if web_context:
        system_content += "2. 系统已联网搜索了相关行业信息(见下文'联网搜索结果'),你可以引用这些外部信息做行业对比和趋势参考,但必须注明'据网络公开信息'并标注来源编号\n"
    else:
        system_content += "2. 数据不足以回答的问题,明确说'数据不足以支撑该分析',不要推测或编造\n"
        system_content += "3. 不做趋势分析(数据是单期快照,无历史对比),不做预测,不补充外部知识或行业常识结论\n"
    system_content += "4. 报告结构:一句话结论→关键指标解读→风险提示(用⚠️)→行动建议\n"
    system_content += "5. 数据展示要求:\n"
    system_content += "   - markdown表格呈现多指标对比,格式:| 列1 | 列2 | 列3 |\n"
    system_content += "   - 如果系统推荐展示图表,在报告中用'📈 建议查看下方图表'引导用户关注,但不要生成图表代码(系统会自动渲染)\n"
    system_content += "6. 用中文,简洁有力,直接输出markdown,不要寒暄。"
    system_content += "\n7. 思考推理规则: thinking只输出3个关键判断要点,每个要点一行,严禁写'首先...其次...然后...最后'等过程罗列,严禁自我验算对话(如'等等/让我想想/我再检查下'),直接给结论。"
    system_content += "\n   错误示范: '首先看订单数据...其次看财务...然后对比...最后总结...(500字)'  ← 太长"
    system_content += "\n   正确示范: '订单额331.5万,其中236.5万未核销→回款压力大; 财务收付款比1.77→整体健康; 应付逾期98.7万→需关注账期'"
    system_content += chart_info
    if memory_prompt:
        system_content += memory_prompt

    user_content = (f"用户问题: {text}\n\n"
                    f"数据源: {pivot_data.get('dataset_label', '')}\n"
                    f"行维度: {pivot_data.get('rows_label', '')}\n"
                    f"列维度: {pivot_data.get('cols_label', '无')}\n"
                    f"指标: {pivot_data.get('metric_label', '')} (聚合: {pivot_data.get('agg', '')})\n\n"
                    f"数据明细:\n{data_block}")
    if web_context:
        user_content += f"\n\n{web_context}"
    user_content += "\n\n请基于以上数据生成分析报告。"
    return system_content, user_content


@router.post("/stream")
def analyze_stream(body: AnalyzeIn, user: User = Depends(get_current_user)):
    from app.core.db import SessionLocal
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "请输入分析指令")
    auth_user_id = user.id

    def gen():
        sess = SessionLocal()
        ai_answer_buf = []
        try:
            # ---- 1. 获取/创建会话 ----
            conv = None
            conv_id = body.conversation_id
            if conv_id:
                conv = sess.query(AIConversation).filter(
                    AIConversation.id == conv_id, AIConversation.user_id == auth_user_id
                ).first()
            if not conv_id or not conv:
                conv = AIConversation(user_id=auth_user_id, title=text[:20] if text else "新对话")
                sess.add(conv)
                sess.flush()
                conv_id = conv.id

            # ---- 2. 保存用户消息 ----
            user_msg = AIMessage(conversation_id=conv_id, role="user", content=text)
            sess.add(user_msg)
            sess.commit()

            # ---- 3. 加载历史 ----
            db_history = sess.query(AIMessage).filter(
                AIMessage.conversation_id == conv_id
            ).order_by(AIMessage.created_at.desc()).limit(10).all()
            db_history.reverse()
            history_for_llm = []
            for m in db_history:
                entry = {"role": m.role, "text": m.content, "data_type": m.data_type or ""}
                if m.extra:
                    try:
                        entry["pivot_data"] = json.loads(m.extra)
                    except Exception:
                        pass
                history_for_llm.append(entry)

            memories = _load_user_memories(auth_user_id, sess)
            memory_prompt = _build_memory_prompt(memories)

            yield _sse("stage", {"stage": "intent", "msg": "正在理解你的问题..."})

            # ---- 4. 意图解析(流式: 思考逐字可见, 消除黑盒等待) ----
            schema = _build_schema_text(text)
            intent_result = None
            for ikind, ival in llm.parse_intent_stream(text, schema, history_for_llm):
                if ikind == "thinking":
                    yield _sse("thinking", {"delta": ival})
                elif ikind == "result":
                    intent_result = ival
            intent_result = intent_result or {}

            reply_text = ""
            data_type = ""
            result = None
            llm_used = False
            err_msg = ""

            # ---- 4.5 联网搜索(按需) ----
            web_ctx = ""
            web_results = []
            need_web = ws.should_search_web(text)
            if need_web:
                yield _sse("stage", {"stage": "web", "msg": "正在联网搜索行业信息..."})
                web_results = ws.search(text + " 行业现状 数据", max_results=5)
                if web_results:
                    web_ctx = ws.format_results(web_results)
                    yield _sse("web", {"results": web_results})

            # ---- 统一查询计划: LLM自主生成tasks, 消除action硬编码 ----
            tasks = intent_result.get("tasks") if isinstance(intent_result, dict) else None

            # 多任务: 跨板块综合分析(如"经营概况"), 并行执行
            if tasks and len(tasks) > 1:
                yield _sse("stage", {"stage": "query", "msg": "正在执行综合分析..."})
                reply_text, overview_data = _overview_analysis(sess, memory_prompt, tasks, web_context=web_ctx)
                if not overview_data:
                    data_type = "chat"
                    yield _sse("answer", {"delta": reply_text})
                else:
                    data_type = "pivot"
                    result = overview_data
                    # _overview_analysis 已基于各板块真实数据生成综合报告, 直接推送
                    yield _sse("stage", {"stage": "report", "msg": "正在生成综合报告..."})
                    yield _sse("answer", {"delta": reply_text})

            # 单任务: 单一明确查询
            elif tasks and len(tasks) == 1:
                intent = _validate_intent(dict(tasks[0]))
                if intent:
                    llm_used = True
                    yield _sse("stage", {"stage": "query", "msg": "正在查询数据库..."})
                    yield _sse("plan", _plan_payload(intent))
                    result = build_pivot(
                        sess, dataset=intent["dataset"], rows_dim=intent["rows_dim"],
                        cols_dim=intent.get("cols_dim"), metric=intent["metric"],
                        agg=intent["agg"], filters=intent.get("filters", []),
                    )
                    if "error" in result:
                        reply_text = f"分析出错: {result['error']}。请检查数据源和字段是否正确,或换个分析角度。"
                        data_type = "error"
                        err_msg = reply_text
                        yield _sse("answer", {"delta": reply_text})
                    elif not result.get("table"):
                        reply_text = (f"查询已完成,但未找到匹配的数据。\n数据源: {result.get('dataset_label','')}\n"
                                      f"维度: {result.get('rows_label','')}\n筛选条件可能过严,请尝试放宽筛选范围。")
                        data_type = "error"
                        err_msg = reply_text
                        yield _sse("answer", {"delta": reply_text})
                    else:
                        yield _sse("stage", {"stage": "report", "msg": "正在生成分析报告..."})
                        sys, user_c = _build_report_messages(text, result, memory_prompt, web_context=web_ctx)
                        data_type = "pivot"
                        yield from _emit_report(system_content=sys, user_content=user_c,
                                                model=settings.DEEPSEEK_MODEL_PRO,
                                                temperature=0.3, max_tokens=16384, timeout=120.0,
                                                reply_holder=None, text_holder=ai_answer_buf)
                else:
                    # 单任务校验失败 → 回退纯对话
                    tasks = None

            # 无任务: 纯对话(闲聊/追问讨论/意图不清需澄清), 带历史分析上下文
            if not tasks:
                yield _sse("stage", {"stage": "report", "msg": "正在回复..."})
                pd = _get_last_pivot_data(history_for_llm)
                if pd:
                    sys, user_c = _discuss_messages(text, history_for_llm, pd, memory_prompt, web_context=web_ctx)
                    data_type = "discuss"
                    yield from _emit_report(system_content=sys, user_content=user_c,
                                            model=settings.DEEPSEEK_MODEL_PRO,
                                            temperature=0.3, max_tokens=8192, timeout=120.0,
                                            reply_holder=None, text_holder=ai_answer_buf)
                else:
                    sys = _chat_reply_system(text, history_for_llm, memory_prompt, web_context=web_ctx)
                    user_c = f"对话历史:{_hist_ctx(history_for_llm)}\n\n"
                    if web_ctx:
                        user_c += f"{web_ctx}\n\n"
                    user_c += f"用户: {text}"
                    data_type = "chat"
                    yield from _emit_report(system_content=sys, user_content=user_c,
                                            model=settings.DEEPSEEK_MODEL_PRO,
                                            temperature=0.7, max_tokens=8192, timeout=60.0,
                                            reply_holder=None, text_holder=ai_answer_buf)

            # ---- save AI message: 优先用流式累积, 其次用一次性文本 ----
            content_out = "".join(ai_answer_buf) or reply_text or err_msg or ""
            extra_json = None
            if data_type == "pivot" and result:
                extra_json = json.dumps(result, ensure_ascii=False)
            ai_msg = AIMessage(conversation_id=conv_id, role="ai",
                               content=content_out, data_type=data_type, extra=extra_json)
            sess.add(ai_msg)
            conv.updated_at = datetime.utcnow()
            try:
                _extract_memory(text, content_out, auth_user_id, sess)
            except Exception:
                pass
            sess.commit()
            conv_id_out = conv_id

            done_data = {"conversation_id": conv_id_out, "type": data_type, "query_text": text}
            if data_type == "pivot" and result:
                done_data["pivot_data"] = result
                done_data["llm_used"] = llm_used
            elif data_type == "discuss":
                done_data["pivot_data"] = _get_last_pivot_data(history_for_llm)
            yield _sse("done", done_data)
        except Exception as e:
            logger.warning(f"[stream] 分析失败: {e}", exc_info=True)
            yield _sse("answer", {"delta": f"分析失败: {e}"})
            yield _sse("done", {"conversation_id": None, "type": "error", "query_text": text})
        finally:
            sess.close()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _hist_ctx(history: list) -> str:
    lines = [f"{h.get('role')}: {(h.get('text') or '')[:200]}" for h in history[-5:]]
    return "\n".join(lines)


def _chat_reply_system(text: str, history: list, memory_prompt: str = "", web_context: str = "") -> str:
    db_schema = _build_db_schema_text()
    now_ctx = llm._now_context()
    sys = (f"你是ERP系统的智能助手，拥有对系统全部业务数据的实时分析能力。\n\n"
           f"{now_ctx}\n\n"
           f"以下是系统数据库的完整表结构信息(动态获取,实时准确):\n\n{db_schema}\n\n"
           f"能力与边界:\n"
           f"1. 你能基于上述表结构理解有哪些可分析的数据(订单、工单、完工、财务、库存、客户、商机等)。\n"
           f"2. 用户问具体数据(如\"本月订单总额\"\"各客户金额\")时，你会触发查询并直接给出基于真实数据的结果与结论。\n"
           f"3. 你绝不是\"只能提供查询思路\"的工具——你能查就能答，查完后直接回答用户。\n"
           f"4. 绝不允许说\"我无法读取/不能访问具体业务数据\"\"我这边拿不到数据\"这类话——那是错误表述。\n"
           f"5. 凡是具体数值回答，必须基于真实查询结果，不得编造。\n"
           f"6. 对于时间相关问题(如今天几号、星期几、本月等),直接使用上方【系统时间】中的信息回答,不要说\"无法获取日期\"之类的话。\n")
    if web_context:
        sys += "6. 系统已联网搜索了相关信息(见用户消息中'联网搜索结果'部分),你可以引用这些外部信息,但必须注明'据网络公开信息'。\n"
    sys += "你的思考推理过程请保持简短精炼,不要冗长陈列推理步骤。"
    if memory_prompt:
        sys += memory_prompt
    return sys


def _discuss_messages(text: str, history: list, pivot_data: dict, memory_prompt: str = "", web_context: str = "") -> tuple:
    ctx = _hist_ctx(history)
    dataset_label = pivot_data.get('dataset_label', '')
    rows_label = pivot_data.get('rows_label', '')
    cols_label = pivot_data.get('cols_label', '无')
    metric_label = pivot_data.get('metric_label', '')
    agg = pivot_data.get('agg', '')
    table = pivot_data.get('table', [])
    data_context = f"\n最近分析的数据:\n- 数据源: {dataset_label}\n- 行维度: {rows_label}\n- 列维度: {cols_label}\n- 指标: {metric_label} (聚合方式: {agg})\n- 数据明细:\n"
    for row in table[:20]:
        dim = row.get("dim", "")
        vals = {k: v for k, v in row.items() if k != "dim"}
        data_context += f"  {dim}: " + " | ".join(f"{k}={_fmt_val(v)}" for k, v in vals.items()) + "\n"
    now_ctx = llm._now_context()
    sys = ("你是资深财务分析师。基于下面提供的最近分析数据,回答用户的问题。\n"
           f"{now_ctx}\n"
           "规则:\n1. 只能基于提供的数据回答,不要编造内部数字\n"
           "2. 对于时间相关问题(如今天几号、星期几、本月等),直接使用上方【系统时间】中的信息回答")
    if web_context:
        sys += "2. 系统已联网搜索了相关行业信息(见下文),你可以引用做对比参考,须注明'据网络公开信息'并标注来源\n"
    else:
        sys += "2. 如果数据不足以回答,明确说明\n"
    sys += ("3. 用中文,简洁有力,可用markdown表格呈现数据对比,不要使用mermaid\n"
            "4. 在数据中发现异常或值得注意的点时,主动指出\n"
            "5. 你的思考推理过程请保持简短精炼,不要冗长陈列推理步骤\n")
    if memory_prompt:
        sys += memory_prompt
    user_c = f"{data_context}\n\n"
    if web_context:
        user_c += f"{web_context}\n\n"
    user_c += f"对话历史:{ctx}\n\n用户追问: {text}"
    return sys, user_c


def _emit_report(system_content: str, user_content: str, model: str,
                 temperature: float, max_tokens: int, timeout: float,
                 reply_holder, text_holder):
    """yield 报告阶段事件流: thinking + answer; 同时把 answer 累加到 text_holder(列表)"""
    for kind, delta in _stream_report(system_content, user_content, model,
                                      temperature=temperature, max_tokens=max_tokens, timeout=timeout):
        if kind == "reasoning":
            yield _sse("thinking", {"delta": delta})
        elif kind == "content":
            if text_holder is not None:
                text_holder.append(delta)
            yield _sse("answer", {"delta": delta})


def _validate_intent(intent: dict) -> Optional[dict]:
    if not intent or not all(k in intent for k in ["dataset", "rows_dim", "metric", "agg"]):
        return None
    valid_ds = list_datasets()
    if intent.get("dataset") not in valid_ds:
        logger.warning(f"LLM返回的dataset无效: {intent.get('dataset')}")
        return None
    ds_cfg = valid_ds[intent["dataset"]]
    valid_dims = {d["key"] for d in ds_cfg["dims"]} | {d["key"] for d in ds_cfg["time_dims"]}
    valid_metrics = {m["key"] for m in ds_cfg["metrics"]}
    rd = intent.get("rows_dim")
    if rd is not None and rd not in valid_dims:
        logger.warning(f"LLM返回的rows_dim无效: {rd}")
        return None
    if intent.get("metric") not in valid_metrics:
        logger.warning(f"LLM返回的metric无效: {intent.get('metric')}")
        return None
    return intent


def _ask_clarify(text: str, history: list, memory_prompt: str = "") -> str:
    ctx = ""
    for h in history[-3:]:
        role = "用户" if h.get("role") == "user" else "AI"
        ctx += f"\n{role}: {h.get('text','')}"
    prompt = f"对话历史:{ctx}\n\n用户最新消息: {text}\n\n"
    prompt += "用户想进行数据分析,但意图不明确。请根据对话上下文,生成1个简短追问帮助确定分析方向。"
    prompt += "追问要具体,给出2-3个选项让用户选择。直接输出问题,不要解释。"
    if memory_prompt:
        prompt += memory_prompt
    try:
        msg = [{"role": "user", "content": prompt}]
        return llm.chat(settings.DEEPSEEK_MODEL_FAST, msg, temperature=0.3, max_tokens=200)
    except:
        return "请问你想分析哪个方面？订单、财务、工单还是成本？"


def _fmt_val(v):
    if isinstance(v, (int, float)):
        return f"{v:,.0f}"
    return str(v)


def _simple_format(d: dict) -> str:
    lines = [f"📊 {d.get('dataset_label','')}分析 — 按{d.get('rows_label','')}分组"]
    for row in d.get("table", []):
        dim = row.get("dim", "")
        vals = {k: v for k, v in row.items() if k != "dim"}
        lines.append(f"  · {dim}: " + " | ".join(f"{k}={_fmt_val(v)}" for k, v in vals.items()))
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
