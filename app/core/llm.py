"""DeepSeek LLM 客户端 - AI分析模块专用
职责分离: 意图解析用flash, 报告生成用pro
"""
import json
import logging
import httpx
from app.config import settings
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)


def _fmt_val(v):
    if isinstance(v, (int, float)):
        return f"{v:,.0f}"
    return str(v)


def _build_endpoint(base: str) -> str:
    """智能拼接DeepSeek请求URL:
    - 用户填写完整endpoint (含/chat/completions 或 /v1/chat/completions): 直接用
    - 用户填写base domain (https://api.deepseek.com 或 https://api.deepseek.com/v1): 自动补全
    - 统一去末尾斜杠, 避免重复路径段
    """
    if not base:
        raise RuntimeError("DEEPSEEK_BASE_URL 未配置")
    s = base.strip().rstrip("/")
    if s.endswith("/chat/completions"):
        return s
    # 补 /chat/completions (无论是 https://api.deepseek.com 还是带/v1的)
    return f"{s}/chat/completions"


def chat(model: str, messages: list, temperature: float = 0.3,
         max_tokens: int = 2000, timeout: float = 60.0) -> str:
    """调用 DeepSeek 对话接口,返回文本内容
    V4-Pro是reasoning model,可能content为空但reasoning_content有值
    """
    if not settings.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    endpoint = _build_endpoint(settings.DEEPSEEK_BASE_URL)
    logger.info(f"[LLM] endpoint={endpoint} model={model} msgs={len(messages)}")
    resp = httpx.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    msg = resp.json()["choices"][0]["message"]
    content = msg.get("content", "") or ""
    # V4 reasoning model: content可能为空,回退到reasoning_content
    if not content.strip():
        content = msg.get("reasoning_content", "") or ""
    return content


def chat_json(model: str, messages: list, **kw) -> dict:
    """调用LLM并解析JSON输出(容错: 提取首个{...}块)"""
    raw = chat(model, messages, **kw)
    logger.info(f"LLM原始返回(前500字符): {raw[:500]!r}")
    # 容错: LLM 可能包裹 ```json ... ``` 或多余文本
    s = raw.strip()
    if not s:
        logger.warning("LLM返回空内容,检查finish_reason和usage")
        raise ValueError("LLM返回空内容")
    if s.startswith("```"):
        s = s.split("```")[1]
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
    # 提取首个 {...}
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        s = s[start:end + 1]
    return json.loads(s)


def chat_stream(model: str, messages: list, temperature: float = 0.3,
                max_tokens: int = 2000, timeout: float = 120.0):
    """调用 DeepSeek 对话接口流式版。
    yield (kind, text):
      - ("reasoning", 增量) 思考链
      - ("content", 增量)  正式回答
    """
    if not settings.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    endpoint = _build_endpoint(settings.DEEPSEEK_BASE_URL)

    def _delta_text(v):
        # 兼容字符串 与 数组两种 delta 返回(V4 reasoning 可能为 list)
        if isinstance(v, str):
            return v
        if isinstance(v, list):
            return "".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in v)
        return ""

    with httpx.stream(
        "POST", endpoint,
        headers={
            "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    ) as resp:
        resp.raise_for_status()
        buf = b""
        for chunk in resp.iter_bytes():
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    return
                try:
                    j = json.loads(data)
                except Exception:
                    continue
                choices = j.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                r = _delta_text(delta.get("reasoning_content") or delta.get("reasoning") or "")
                if r:
                    yield ("reasoning", r)
                c = _delta_text(delta.get("content") or "")
                if c:
                    yield ("content", c)


def parse_intent(text: str, schema: str, history: list = None) -> dict:
    """意图解析(flash): 自然语言 → 分析参数或action标识"""
    ctx = ""
    if history:
        recent = history[-6:]
        ctx = "\n对话上下文:\n"
        for h in recent:
            role = "用户" if h.get("role") == "user" else "AI"
            data_type = h.get("data_type", "")
            tag = f"[{data_type}]" if data_type else ""
            t = h.get("text", "")[:200]
            ctx += f"{tag}{role}: {t}\n"
        ctx += "\n"

    system_prompt = """你是ERP系统的智能数据分析助手。根据用户消息、对话上下文和可用数据源，自主决定需要查询哪些数据来回答，生成一个查询计划。不要预设固定操作类型，而是根据用户的实际问题灵活生成1个或多个查询任务，或判断为无需查数据的纯对话。

输出JSON，二选一:

A) 需要查询数据: {"tasks": [{ "dataset":"...", "rows_dim":"...", "cols_dim":null, "metric":"...", "agg":"...", "filters":[], "alias":"分析项中文名" }, ...]}
   - 单一明确问题 → 1个task；跨多个业务板块的综合分析(如经营概况/整体经营/全面分析) → 自主生成2-6个task覆盖相关板块
   - dataset/rows_dim/metric/agg 必须使用下方数据源schema中真实存在的英文key
   - rows_dim 支持时间维度分组(如 created_at:month/quarter/year)
   - 每个task必须带alias(中文描述，用于报告标题)

B) 无需查数据(闲聊/常识/寒暄/表达感谢/或对话历史中已有分析结果的追问讨论): {"tasks": []}
   - 追问讨论时不需要新查询，系统会带上历史分析数据供你直接解读

关键映射规则:
- 用户提到人名(如"王销售")→ filters用 {"field":"sales_user_id","op":"like","value":"姓氏"}
- 用户提到客户名→ filters用 {"field":"customer_id","op":"like","value":"客户名"}
- 时间筛选: {"field":"created_at","op":"ge","value":"2026-08-01"}
- filter格式: {"field":"字段key","op":"eq/ne/gt/lt/ge/le/like/in/between","value":"值"}

严格规则:
- 宁可少用数据源，也要确保 dataset/rows_dim/metric 都是真实存在的key，严禁捏造
- 如果用户请求的数据无法通过现有数据源查询，tasks返回[]，不要捏造查询
- 只输出JSON，不要解释"""

    user_prompt = f"可用数据源:\n{schema}\n\n{ctx}用户消息: {text}\n\n请生成查询计划并返回JSON:"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return chat_json(settings.DEEPSEEK_MODEL_FAST, messages, temperature=0.0, max_tokens=8192)


def parse_intent_stream(text: str, schema: str, history: list = None):
    """流式意图解析(flash): yield ("thinking", 增量) / ("result", 最终JSON库)。
    与 parse_intent 使用同一套 prompt, 仅改为流式输出, 消除非流式黑盒等待。"""
    ctx = ""
    if history:
        recent = history[-6:]
        ctx = "\n对话上下文:\n"
        for h in recent:
            role = "用户" if h.get("role") == "user" else "AI"
            data_type = h.get("data_type", "")
            tag = f"[{data_type}]" if data_type else ""
            t = h.get("text", "")[:200]
            ctx += f"{tag}{role}: {t}\n"
        ctx += "\n"

    system_prompt = """你是ERP系统的智能数据分析助手。根据用户消息、对话上下文和可用数据源，自主决定需要查询哪些数据来回答，生成一个查询计划。不要预设固定操作类型，而是根据用户的实际问题灵活生成1个或多个查询任务，或判断为无需查数据的纯对话。

输出JSON，二选一:

A) 需要查询数据: {"tasks": [{ "dataset":"...", "rows_dim":"...", "cols_dim":null, "metric":"...", "agg":"...", "filters":[], "alias":"分析项中文名" }, ...]}
   - 单一明确问题 → 1个task；跨多个业务板块的综合分析(如经营概况/整体经营/全面分析) → 自主生成2-6个task覆盖相关板块
   - dataset/rows_dim/metric/agg 必须使用下方数据源schema中真实存在的英文key
   - rows_dim 支持时间维度分组(如 created_at:month/quarter/year)
   - 每个task必须带alias(中文描述，用于报告标题)

B) 无需查数据(闲聊/常识/寒暄/表达感谢/或对话历史中已有分析结果的追问讨论): {"tasks": []}
   - 追问讨论时不需要新查询，系统会带上历史分析数据供你直接解读

关键映射规则:
- 用户提到人名(如"王销售")→ filters用 {"field":"sales_user_id","op":"like","value":"姓氏"}
- 用户提到客户名→ filters用 {"field":"customer_id","op":"like","value":"客户名"}
- 时间筛选: {"field":"created_at","op":"ge","value":"2026-08-01"}
- filter格式: {"field":"字段key","op":"eq/ne/gt/lt/ge/le/like/in/between","value":"值"}

严格规则:
- 宁可少用数据源，也要确保 dataset/rows_dim/metric 都是真实存在的key，严禁捏造
- 如果用户请求的数据无法通过现有数据源查询，tasks返回[]，不要捏造查询
- 你的思考推理过程请保持简短精炼，不要冗长陈列推理步骤
- 只输出JSON，不要解释"""

    user_prompt = f"可用数据源:\n{schema}\n\n{ctx}用户消息: {text}\n\n请生成查询计划并返回JSON:"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    acc_content = ""
    for kind, delta in chat_stream(settings.DEEPSEEK_MODEL_FAST, messages, temperature=0.0,
                                   max_tokens=8192, timeout=60.0):
        if kind == "reasoning":
            yield ("thinking", delta)
        else:
            acc_content += delta
    # 剥离可能的 markdown 代码块
    import json as _json
    block = acc_content.strip()
    if block.startswith("```"):
        block = block.strip("`")
        if "json" in block[:5]:
            block = block[block.find("\n") + 1:].rstrip("`").strip()
    try:
        parsed = _json.loads(block)
        yield ("result", parsed)
    except Exception:
        yield ("result", None)


def generate_report(text: str, pivot_data: dict, memory_prompt: str = "") -> str:
    """报告生成(pro): 透视数据 → 专业分析报告"""
    # 构造数据明细(控制token, 截断超长表格)
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

    system_content = "你是资深财务分析师。严格遵循以下规则:\n"
    system_content += "1. 绝对不编造数据——报告中每个数字必须来自下方数据明细,不添加任何数据中没有的数值\n"
    system_content += "2. 数据不足以回答的问题,明确说'数据不足以支撑该分析',不要推测或编造\n"
    system_content += "3. 不做趋势分析(数据是单期快照,无历史对比),不做预测,不做假设性分析\n"
    system_content += "4. 只分析已提供的数据,不补充外部知识或行业常识结论\n"
    system_content += "5. 报告结构:一句话结论→关键指标解读→风险提示(用⚠️)→行动建议\n"
    system_content += "6. 数据展示要求:\n"
    system_content += "   - 当涉及多项数据对比时,必须使用markdown表格呈现,格式:| 列1 | 列2 | 列3 |\n"
    system_content += "   - 当数据是时间序列或对比关系时,在报告末尾使用mermaid语法生成图表,支持的图表类型:bar(柱状图)、pie(饼图)、line(折线图)\n"
    system_content += "   - mermaid语法示例: ```mermaid\\nbar-chart\\n    title 标题\\n    x-axis 类别\\n    y-axis 数值\\n    bar 类别1 : 数值1\\n    bar 类别2 : 数值2\\n```\n"
    system_content += "   - 饼图示例: ```mermaid\\npie title 标题\\n    '类别1' : 数值1\\n    '类别2' : 数值2\\n```\n"
    system_content += "   - 折线图示例: ```mermaid\\nline-chart\\n    title 标题\\n    x-axis 标签\\n    y-axis 数值\\n    line 系列1\\n       点1,点2,点3\\n```\n"
    system_content += "7. 用中文,简洁有力,直接输出markdown,不要寒暄。"
    if memory_prompt:
        system_content += memory_prompt

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"用户问题: {text}\n\n"
                                    f"数据源: {pivot_data.get('dataset_label','')}\n"
                                    f"行维度: {pivot_data.get('rows_label','')}\n"
                                    f"列维度: {pivot_data.get('cols_label','无')}\n"
                                    f"指标: {pivot_data.get('metric_label','')} (聚合: {pivot_data.get('agg','')})\n\n"
                                    f"数据明细:\n{data_block}\n\n"
                                    f"请仅基于以上数据生成分析报告。"},
    ]
    reply = chat(settings.DEEPSEEK_MODEL_PRO, messages, temperature=0.3, max_tokens=16384, timeout=120.0)
    return reply
