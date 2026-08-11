"""DeepSeek LLM 客户端 - AI分析模块专用
职责分离: 意图解析用flash, 报告生成用pro
"""
import json
import logging
import httpx
from app.config import settings
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)


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
    """调用 DeepSeek 对话接口,返回文本内容"""
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
    return resp.json()["choices"][0]["message"]["content"]


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
    """意图解析(flash): 自然语言 → pivot参数JSON"""
    ctx = ""
    if history:
        # 取最近3轮对话(6条消息)
        recent = history[-6:]
        ctx = "\n对话上下文:\n"
        for h in recent:
            role = "用户" if h.get("role") == "user" else "AI"
            t = h.get("text", "")[:200]  # 截断避免超长
            ctx += f"{role}: {t}\n"
        ctx += "\n"

    messages = [
        {"role": "system", "content": "你是ERP数据分析助手。根据用户问题,从可用数据源中选出最合适的,输出透视分析参数JSON。"
         "严格要求:dataset/rows_dim/cols_dim/metric必须用数据源里的英文key,不要用中文。只输出JSON,不要解释。"},
        {"role": "user", "content": f"可用数据源:\n{schema}\n\n{ctx}用户问题: {text}\n\n"
                                    "输出JSON(字段值必须用上面的英文key):\n"
                                    "{\"dataset\":\"orders\",\"rows_dim\":\"status\",\"cols_dim\":null,"
                                    "\"metric\":\"total_amount\",\"agg\":\"sum\",\"filters\":[]}"},
    ]
    return chat_json(settings.DEEPSEEK_MODEL_FAST, messages, temperature=0.1, max_tokens=4096)


def generate_report(text: str, pivot_data: dict) -> str:
    """报告生成(pro): 透视数据 → 专业分析报告"""
    # 构造数据明细(控制token, 截断超长表格)
    table = pivot_data.get("table", [])
    if len(table) > 30:
        table = table[:30]

    lines = []
    for row in table:
        dim = row.get("dim", "")
        vals = {k: v for k, v in row.items() if k != "dim"}
        val_str = " | ".join(f"{k}={v:,.0f}" for k, v in vals.items())
        lines.append(f"  {dim}: {val_str}")

    data_block = "\n".join(lines) if lines else "  (无数据)"

    messages = [
        {"role": "system", "content": "你是资深财务分析师。严格遵循以下规则:\n"
         "1. 绝对不编造数据——报告中每个数字必须来自下方数据明细,不添加任何数据中没有的数值\n"
         "2. 数据不足以回答的问题,明确说'数据不足以支撑该分析',不要推测或编造\n"
         "3. 不做趋势分析(数据是单期快照,无历史对比),不做预测,不做假设性分析\n"
         "4. 只分析已提供的数据,不补充外部知识或行业常识结论\n"
         "5. 报告结构:一句话结论→关键指标解读→风险提示(用⚠️)→行动建议\n"
         "用中文,简洁有力,直接输出markdown,不要寒暄。"},
        {"role": "user", "content": f"用户问题: {text}\n\n"
                                    f"数据源: {pivot_data.get('dataset_label','')}\n"
                                    f"行维度: {pivot_data.get('rows_label','')}\n"
                                    f"列维度: {pivot_data.get('cols_label','无')}\n"
                                    f"指标: {pivot_data.get('metric_label','')} (聚合: {pivot_data.get('agg','')})\n\n"
                                    f"数据明细:\n{data_block}\n\n"
                                    f"请仅基于以上数据生成分析报告。"},
    ]
    reply = chat(settings.DEEPSEEK_MODEL_PRO, messages, temperature=0.3, max_tokens=1500)
    return reply
