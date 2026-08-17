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

    system_prompt = """你是ERP系统的智能助手。根据用户消息和对话上下文，判断应该执行什么操作。

操作类型:
1. "chat": 闲聊、打招呼、问身份/能力、常识性问题、表达感谢等非数据分析请求
   - 返回格式: {"action": "chat"}

2. "discuss": 对话历史中有[pivot]分析结果，用户在追问、讨论、评论该数据
   - 返回格式: {"action": "discuss"}

3. "clarify": 用户想进行数据分析但意图不明确，需要追问
   - 返回格式: {"action": "clarify", "message": "追问内容"}

4. "overview": 用户要求分析整体经营状况、公司全面分析、综合分析等跨多个业务板块的请求
   - 返回格式: {"action": "overview", "tasks": [...]}
   - tasks是分析任务数组，每个任务格式同analyze的参数: {"dataset":"...","rows_dim":"...","metric":"...","agg":"...","filters":[],"alias":"分析项中文名"}
   - 根据用户关注点动态选择3-6个分析任务，覆盖最相关的业务板块
   - 例如"销售情况"→多选订单相关分析；"财务状况"→选财务相关分析；"整体经营"→覆盖订单+工单+财务+库存

5. "analyze": 用户明确想查询或分析某一个业务数据
   - 返回格式: {"action": "analyze", "dataset": "...", "rows_dim": "...", "cols_dim": null, "metric": "...", "agg": "...", "filters": []}

关键映射规则:
- 当用户提到人名(如"王销售"、"张三")时，使用sales_user_id筛选字段，格式: {"field": "sales_user_id", "op": "like", "value": "王"}
- 当用户提到客户名称时，使用customer_id筛选字段
- filter格式: {"field": "字段key", "op": "eq/like/in", "value": "值"}
- 人名查询用like操作，输入姓氏或关键字
- 时间筛选: {"field": "created_at", "op": "ge", "value": "2026-08-01"}

示例:
- "王销售的业绩" → {"action": "analyze", "dataset": "orders", "rows_dim": "sales_user_id", "metric": "total_amount", "agg": "sum", "filters": [{"field": "sales_user_id", "op": "like", "value": "王"}]}
- "客户A的订单" → {"action": "analyze", "dataset": "orders", "rows_dim": "customer_id", "metric": "total_amount", "agg": "sum", "filters": [{"field": "customer_id", "op": "like", "value": "客户A"}]}
- "整体经营状况" → {"action": "overview", "tasks": [{"dataset":"orders","rows_dim":"status","metric":"total_amount","agg":"sum","alias":"订单状态分布"},{"dataset":"orders","rows_dim":"created_at:month","metric":"total_amount","agg":"sum","alias":"月度销售趋势"},{"dataset":"work_orders","rows_dim":"status","metric":"plan_qty","agg":"sum","alias":"工单状态分布"},{"dataset":"finance_docs","rows_dim":"doc_type","metric":"amount","agg":"sum","alias":"财务收支分类"},{"dataset":"inventory_txns","rows_dim":"txn_type","metric":"quantity","agg":"sum","alias":"库存流转分析"}]}

严格规则:
- 先判断是chat/discuss/clarify/overview/analyze中的哪一种
- 当用户说"整体"、"全面"、"综合"、"经营状况"、"公司情况"等关键词时，判断为overview
- overview时tasks必须包含alias字段(中文描述)
- 如果是analyze，dataset/rows_dim/cols_dim/metric必须用数据源里的英文key
- 如果用户请求的数据无法通过现有数据源查询，判断为clarify并在message中说明原因
- 只输出JSON，不要解释"""

    user_prompt = f"可用数据源:\n{schema}\n\n{ctx}用户消息: {text}\n\n请判断操作类型并返回JSON:"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return chat_json(settings.DEEPSEEK_MODEL_FAST, messages, temperature=0.0, max_tokens=8192)


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
