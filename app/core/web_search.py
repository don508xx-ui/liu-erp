"""Web搜索工具 - 使用ddgs(DuckDuckGo)免费搜索，无需API key"""
import logging
import time

logger = logging.getLogger(__name__)

# 触发联网搜索的关键词
WEB_SEARCH_KEYWORDS = [
    "行业", "市场", "对比", "现状", "趋势", "行情", "价格走势",
    "竞争对手", "竞品", "行业平均", "行业水平", "benchmark",
    "最新", "新闻", "政策", "法规", "标准",
    "搜一下", "搜索", "网上查", "查一下", "百度", "google",
    "同比", "环比", "宏观", "经济", "GDP", "PMI",
    "电镀", "表面处理", "涂层", "精密制造", "行业报告",
    "营收", "利润", "增长", "市场份额", "规模",
]


def should_search_web(text: str) -> bool:
    """判断是否需要联网搜索"""
    t = text.lower()
    # 显式搜索指令
    if any(kw in t for kw in ["搜一下", "搜索", "网上查", "查一下", "百度", "帮我查", "联网"]):
        return True
    # 隐式: 数据分析 + 行业对比类词汇
    data_kw = ["经营", "分析", "销售", "订单", "业绩", "营收", "利润", "成本"]
    web_kw = ["行业", "市场", "对比", "趋势", "现状", "行情", "竞争对手", "竞品", "行业平均", "水平", "benchmark"]
    has_data = any(kw in t for kw in data_kw)
    has_web = any(kw in t for kw in web_kw)
    return has_data and has_web


def search(query: str, max_results: int = 5) -> list:
    """执行web搜索，返回 [{title, url, snippet}]"""
    try:
        from ddgs import DDGS
        with DDGS(timeout=10) as ddgs:
            results = list(ddgs.text(query, max_results=max_results, region="cn-zh"))
        out = []
        for r in results[:max_results]:
            out.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", "")[:300],
            })
        logger.info(f"[web_search] '{query}' -> {len(out)} results")
        return out
    except Exception as e:
        logger.warning(f"[web_search] failed: {e}")
        return []


def format_results(results: list) -> str:
    """将搜索结果格式化为LLM可用的文本"""
    if not results:
        return ""
    lines = ["## 联网搜索结果(供参考，引用时请标注来源)", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}")
        lines.append(f"    URL: {r['url']}")
        if r['snippet']:
            lines.append(f"    摘要: {r['snippet']}")
        lines.append("")
    lines.append("注意: 以上为网络公开信息，仅供对比参考。你的核心分析必须基于系统内真实业务数据。")
    return "\n".join(lines)
