"""Child-safe web search for TeeBot.

Works **without any API key** by default:
  - DuckDuckGo Instant Answer (free)
  - Wikipedia summary (free)

Optional paid providers (only if a key is present): Metaso, Tavily.

The tool returns short, age-appropriate notes for the LLM to narrate — never
raw adult/violent content, and never as a free-form browser for the child.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
from config.logger import setup_logging
from plugins_func.register import (
    Action,
    ActionResponse,
    ToolType,
    register_function,
)

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

# Keep tool turns snappy; long provider timeouts make Live feel "stuck".
DEFAULT_TIMEOUT_SEC = 4.0
DEFAULT_MAX_RESULTS = 2
MAX_QUERY_CHARS = 200
MAX_SNIPPET_CHARS = 280
MAX_ANSWER_CHARS = 500

_DEFAULT_DESCRIPTION = (
    "Child-safe web search for learning facts and current kid-appropriate "
    "information. Call when the child explicitly asks to look something up "
    "online or needs a real-world fact (e.g. 'tìm trên mạng', 'search online', "
    "'con muốn biết', 'what is', 'ai là', 'tại sao', 'bao nhiêu', 'tin gì mới về'). "
    "Do not search adult, violent, graphic, self-harm, illegal, medical advice, "
    "legal, financial, extremist, hateful, sexual, or private personal data. "
    "Prefer short, age-appropriate summaries the robot can speak aloud."
)

WEB_SEARCH_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": _DEFAULT_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search keywords or a short question in Vietnamese or English, "
                        "e.g. 'vì sao bầu trời xanh', 'who was Marie Curie', "
                        "'thủ đô của Nhật Bản'."
                    ),
                }
            },
            "required": ["query"],
        },
    },
}

# English + Vietnamese unsafe cues. Keep keyword matching simple and high-recall;
# regex patterns cover multi-word self-harm / weapon instructions.
CHILD_UNSAFE_QUERY_KEYWORDS = (
    # English
    "adult",
    "porn",
    "sex",
    "nude",
    "xxx",
    "nsfw",
    "violent",
    "violence",
    "gore",
    "blood",
    "crime",
    "murder",
    "suicide",
    "self harm",
    "self-harm",
    "kill",
    "weapon",
    "drug",
    "cocaine",
    "heroin",
    "gambling",
    "casino",
    "terror",
    "extremist",
    "hate",
    # Vietnamese (no accents + common accented forms via normalize later)
    "khiêu dâm",
    "khieu dam",
    "sex",
    "khiêu gợi",
    "khieu goi",
    "tự tử",
    "tu tu",
    "tự sát",
    "tu sat",
    "giết người",
    "giet nguoi",
    "giết",
    "giet",
    "súng",
    "sung",
    "bom",
    "ma túy",
    "ma tuy",
    "cờ bạc",
    "co bac",
    "đánh bạc",
    "danh bac",
    "khiêu khích bạo lực",
    "bao luc",
    "bạo lực",
    "mạng khiêu dâm",
)


CHILD_UNSAFE_QUERY_PATTERNS = (
    re.compile(
        r"\bhow\s+to\s+(?:make|build|create)\s+(?:a\s+)?"
        r"(?:gun|rifle|pistol|knife|bomb|weapon|explosive)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:hurt|harm|injure|kill)\s+"
        r"(?:someone|somebody|a\s+person|a\s+child|a\s+kid|an\s+animal)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:hurt|harm|injure|kill|poison|starve|cut|hang|choke|drown|burn|stab|shoot)\s+"
        r"(?:myself|yourself)\b",
        re.I,
    ),
    re.compile(r"\bcut\s+(?:my|your)\s+wrist\b", re.I),
    re.compile(r"\boverdose\b", re.I),
    re.compile(r"\bdrink\s+bleach\b", re.I),
    re.compile(r"\b(?:take|swallow)\s+(?:all\s+)?(?:(?:my|your)\s+)?pills\b", re.I),
    re.compile(r"\bjump\s+off\s+(?:a\s+)?(?:bridge|building|roof|window)\b", re.I),
    re.compile(
        r"\bmy\s+(?:full|real)\s+name\s+is\s+[A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){1,3}\b",
        re.I,
    ),
    re.compile(
        r"\b(?:end\s+(?:my|your)\s+life|take\s+(?:my|your)\s+own\s+life|unalive\s+(?:myself|yourself))\b",
        re.I,
    ),
    re.compile(r"\b(?:secretly\s+meet|without\s+telling\s+(?:your\s+)?parents?)\b", re.I),
    # Vietnamese self-harm / weapon instruction patterns (accent-stripped checked too)
    re.compile(r"cách\s+(?:làm|chế|tạo)\s+(?:bom|súng|vũ\s*khí)", re.I),
    re.compile(r"cach\s+(?:lam|che|tao)\s+(?:bom|sung|vu\s*khi)", re.I),
    re.compile(r"(?:làm|lam)\s+(?:sao|thế\s+nào|the\s+nao)\s+(?:để|de)\s+(?:tự\s*tử|tu\s*tu|chết|chet)", re.I),
    re.compile(r"(?:tự\s*hại|tu\s*hai|tự\s*tổn\s*thương|tu\s*ton\s*thuong)", re.I),
)

_WS_RE = re.compile(r"\s+")
_VI_ACCENT_MAP = str.maketrans(
    {
        "à": "a",
        "á": "a",
        "ả": "a",
        "ã": "a",
        "ạ": "a",
        "ă": "a",
        "ằ": "a",
        "ắ": "a",
        "ẳ": "a",
        "ẵ": "a",
        "ặ": "a",
        "â": "a",
        "ầ": "a",
        "ấ": "a",
        "ẩ": "a",
        "ẫ": "a",
        "ậ": "a",
        "è": "e",
        "é": "e",
        "ẻ": "e",
        "ẽ": "e",
        "ẹ": "e",
        "ê": "e",
        "ề": "e",
        "ế": "e",
        "ể": "e",
        "ễ": "e",
        "ệ": "e",
        "ì": "i",
        "í": "i",
        "ỉ": "i",
        "ĩ": "i",
        "ị": "i",
        "ò": "o",
        "ó": "o",
        "ỏ": "o",
        "õ": "o",
        "ọ": "o",
        "ô": "o",
        "ồ": "o",
        "ố": "o",
        "ổ": "o",
        "ỗ": "o",
        "ộ": "o",
        "ơ": "o",
        "ờ": "o",
        "ớ": "o",
        "ở": "o",
        "ỡ": "o",
        "ợ": "o",
        "ù": "u",
        "ú": "u",
        "ủ": "u",
        "ũ": "u",
        "ụ": "u",
        "ư": "u",
        "ừ": "u",
        "ứ": "u",
        "ử": "u",
        "ữ": "u",
        "ự": "u",
        "ỳ": "y",
        "ý": "y",
        "ỷ": "y",
        "ỹ": "y",
        "ỵ": "y",
        "đ": "d",
    }
)


def _strip_vi_accents(text: str) -> str:
    return str(text or "").lower().translate(_VI_ACCENT_MAP)


def _normalize_query(query: Any) -> str:
    text = _WS_RE.sub(" ", str(query or "").strip())
    if len(text) > MAX_QUERY_CHARS:
        text = text[:MAX_QUERY_CHARS].rstrip()
    return text


def _keyword_hit(haystack: str, keyword: str) -> bool:
    """Substring for multi-word phrases; word-boundary for short tokens (avoid
    false positives like Vietnamese 'sung suong' matching 'sung')."""
    needle = str(keyword or "").strip().lower()
    if not needle or not haystack:
        return False
    if " " in needle or len(needle) >= 5:
        return needle in haystack
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def _is_child_unsafe_query(query: str) -> bool:
    lowered = str(query or "").lower()
    stripped = _strip_vi_accents(lowered)
    for keyword in CHILD_UNSAFE_QUERY_KEYWORDS:
        if _keyword_hit(lowered, keyword) or _keyword_hit(stripped, _strip_vi_accents(keyword)):
            return True
    return any(pattern.search(lowered) or pattern.search(stripped) for pattern in CHILD_UNSAFE_QUERY_PATTERNS)


def _is_child_unsafe_text(text: str) -> bool:
    """Reuse query safety rules to drop unsafe result snippets."""
    return _is_child_unsafe_query(text)


def _clip(text: str, limit: int) -> str:
    value = _WS_RE.sub(" ", str(text or "").strip())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _plugin_config(conn: "ConnectionHandler") -> Dict[str, Any]:
    config = getattr(conn, "config", None) or {}
    plugins = config.get("plugins") if isinstance(config, dict) else {}
    raw = plugins.get("web_search") if isinstance(plugins, dict) else {}
    return raw if isinstance(raw, dict) else {}


def _coerce_max_results(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_RESULTS
    return max(1, min(value, 8))


def _coerce_timeout(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SEC
    if not (value > 0):
        return DEFAULT_TIMEOUT_SEC
    return min(value, 30.0)


def _preferred_language(query: str, config: Dict[str, Any]) -> str:
    configured = str(config.get("language") or config.get("lang") or "").strip().lower()
    if configured in {"vi", "en", "zh", "ja", "ko", "fr", "de", "es"}:
        return configured
    # Heuristic: Vietnamese characters or common function words.
    if re.search(r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]", query, re.I):
        return "vi"
    stripped = _strip_vi_accents(query)
    if any(token in stripped.split() for token in ("la", "gi", "tai", "sao", "bao", "nhieu", "ai", "o", "dau")):
        return "vi"
    return "en"


def _format_search_payload(
    *,
    query: str,
    provider: str,
    answer: str = "",
    items: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Build a short LLM-facing packet: summary + 1–N kid-safe sources."""
    safe_items: List[Dict[str, str]] = []
    for item in items or []:
        title = _clip(item.get("title") or "", 120)
        snippet = _clip(item.get("snippet") or item.get("summary") or item.get("content") or "", MAX_SNIPPET_CHARS)
        if not title and not snippet:
            continue
        blob = f"{title} {snippet}"
        if _is_child_unsafe_text(blob):
            continue
        safe_items.append(
            {
                "title": title or "Kết quả",
                "snippet": snippet,
                "date": _clip(item.get("date") or "", 40),
            }
        )

    safe_answer = _clip(answer, MAX_ANSWER_CHARS)
    if safe_answer and _is_child_unsafe_text(safe_answer):
        safe_answer = ""

    if not safe_answer and not safe_items:
        return (
            "Child-safe search found no suitable learning results for this query. "
            "Please answer briefly from general knowledge or ask the child to rephrase."
        )

    lines = [
        "【Kết quả tìm kiếm an toàn cho trẻ / Child-safe search results】",
        f"Nguồn tìm: {provider}",
        f"Câu hỏi: {query}",
        "Hướng dẫn kể lại: tóm tắt ngắn, vui, dễ hiểu cho trẻ 4–10 tuổi; "
        "không đọc link; không thêm nội dung người lớn/bạo lực.",
    ]
    if safe_answer:
        lines.append(f"Tóm tắt: {safe_answer}")
    if safe_items:
        lines.append("Chi tiết:")
        for index, item in enumerate(safe_items, 1):
            lines.append(f"{index}. {item['title']}")
            if item.get("date"):
                lines.append(f"   Ngày: {item['date']}")
            if item.get("snippet"):
                lines.append(f"   Ghi chú: {item['snippet']}")
    return "\n".join(lines)


def _search_metaso(api_key: str, query: str, max_results: int, timeout_sec: float) -> str:
    url = "https://metaso.cn/api/v1/search"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "q": query,
        "size": max_results,
        "stream": False,
        "scope": "webpage",
        "includeSummary": True,
        "includeRawContent": False,
        "conciseSnippet": True,
    }
    response = requests.post(url, json=payload, headers=headers, timeout=timeout_sec)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        data = {}

    webpages = data.get("webpages") or []
    items: List[Dict[str, str]] = []
    if isinstance(webpages, list):
        for item in webpages[:max_results]:
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "title": str(item.get("title") or ""),
                    "snippet": str(item.get("summary") or item.get("snippet") or ""),
                    "date": str(item.get("date") or ""),
                }
            )
    answer = str(data.get("answer") or data.get("summary") or "")
    return _format_search_payload(query=query, provider="metaso", answer=answer, items=items)


def _search_tavily(api_key: str, query: str, max_results: int, timeout_sec: float) -> str:
    url = "https://api.tavily.com/search"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": True,
        "include_raw_content": False,
        "topic": "general",
    }
    response = requests.post(url, json=payload, headers=headers, timeout=timeout_sec)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        data = {}

    results = data.get("results") or []
    items: List[Dict[str, str]] = []
    if isinstance(results, list):
        for item in results[:max_results]:
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "title": str(item.get("title") or ""),
                    "snippet": str(item.get("content") or ""),
                }
            )
    answer = str(data.get("answer") or "")
    return _format_search_payload(query=query, provider="tavily", answer=answer, items=items)


def _wikipedia_lang(language: str) -> str:
    lang = (language or "en").split("-")[0].lower()
    if lang in {"vi", "en", "zh", "ja", "ko", "fr", "de", "es"}:
        return "zh" if lang == "zh" else lang
    return "en"


def _search_wikipedia(query: str, max_results: int, language: str, timeout_sec: float) -> str:
    """Free educational search (no API key). Uses Wikipedia OpenSearch + summary."""
    lang = _wikipedia_lang(language)
    headers = {
        "User-Agent": "tbot-server/web_search (+https://tjbot.vn; child companion robot)",
        "Accept": "application/json",
    }
    open_url = (
        f"https://{lang}.wikipedia.org/w/api.php"
        f"?action=opensearch&search={quote(query)}&limit={max_results}"
        f"&namespace=0&format=json"
    )
    open_resp = requests.get(open_url, headers=headers, timeout=timeout_sec)
    open_resp.raise_for_status()
    payload = open_resp.json()
    titles: List[str] = []
    if isinstance(payload, list) and len(payload) >= 2 and isinstance(payload[1], list):
        titles = [str(t) for t in payload[1] if str(t).strip()]

    items: List[Dict[str, str]] = []
    answer = ""
    for title in titles[:max_results]:
        summary_url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(title, safe='')}"
        try:
            summary_resp = requests.get(summary_url, headers=headers, timeout=timeout_sec)
            if summary_resp.status_code == 404:
                continue
            summary_resp.raise_for_status()
            body = summary_resp.json()
            if not isinstance(body, dict):
                continue
            if body.get("type") == "disambiguation":
                continue
            extract = str(body.get("extract") or body.get("description") or "").strip()
            display = str(body.get("title") or title)
            if not extract:
                continue
            if not answer:
                answer = extract
            items.append({"title": display, "snippet": extract})
        except requests.RequestException:
            continue

    if not items and language != "en" and lang != "en":
        # Retry once in English for better coverage of global facts.
        return _search_wikipedia(query, max_results, "en", timeout_sec)

    return _format_search_payload(query=query, provider=f"wikipedia/{lang}", answer=answer, items=items)


def _search_duckduckgo(query: str, max_results: int, timeout_sec: float) -> str:
    """Free Instant Answer API — no API key required."""
    url = "https://api.duckduckgo.com/"
    params = {
        "q": query,
        "format": "json",
        "no_html": "1",
        "skip_disambig": "1",
    }
    headers = {
        "User-Agent": "tbot-server/web_search (+https://tjbot.vn; child companion robot)",
        "Accept": "application/json",
    }
    response = requests.get(url, params=params, headers=headers, timeout=timeout_sec)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        data = {}

    answer = str(
        data.get("AbstractText")
        or data.get("Abstract")
        or data.get("Answer")
        or data.get("Definition")
        or ""
    ).strip()
    heading = str(data.get("Heading") or data.get("AnswerType") or "").strip()

    items: List[Dict[str, str]] = []
    if answer:
        items.append({"title": heading or "DuckDuckGo", "snippet": answer})

    related = data.get("RelatedTopics") or []
    if isinstance(related, list):
        for entry in related:
            if len(items) >= max_results:
                break
            if not isinstance(entry, dict):
                continue
            # Nested topic groups.
            if isinstance(entry.get("Topics"), list):
                for sub in entry["Topics"]:
                    if len(items) >= max_results:
                        break
                    if not isinstance(sub, dict):
                        continue
                    text = str(sub.get("Text") or "").strip()
                    if not text:
                        continue
                    title = text.split(" - ", 1)[0][:80]
                    items.append({"title": title, "snippet": text})
                continue
            text = str(entry.get("Text") or "").strip()
            if not text:
                continue
            title = text.split(" - ", 1)[0][:80]
            items.append({"title": title, "snippet": text})

    return _format_search_payload(
        query=query,
        provider="duckduckgo",
        answer=answer,
        items=items[:max_results],
    )


def _provider_api_key(config: Dict[str, Any], provider: str) -> str:
    provider = provider.lower()
    specific = {
        "metaso": config.get("metaso_api_key") or config.get("api_key"),
        "tavily": config.get("tavily_api_key") or config.get("api_key"),
    }.get(provider)
    return str(specific or "").strip()


# Free providers that never need a key.
_FREE_PROVIDERS = ("duckduckgo", "wikipedia")
_KEYED_PROVIDERS = ("metaso", "tavily")


def _default_free_chain_names() -> List[str]:
    return ["duckduckgo", "wikipedia"]


def _build_provider_chain(
    config: Dict[str, Any],
    *,
    query: str,
    max_results: int,
    timeout_sec: float,
    language: str,
) -> List[Tuple[str, Callable[[], str]]]:
    """Build ordered search backends. Always includes free providers so search
    works with an empty config and no secrets."""
    primary = str(config.get("provider") or "free").strip().lower()
    if primary in {"", "auto", "free", "none", "keyless"}:
        # Prefer optional paid key when present, else free-only.
        if _provider_api_key(config, "tavily"):
            primary = "tavily"
        elif _provider_api_key(config, "metaso"):
            primary = "metaso"
        else:
            primary = "duckduckgo"

    # If operator pinned a keyed provider without a key, downgrade to free.
    if primary in _KEYED_PROVIDERS and not _provider_api_key(config, primary):
        logger.bind(tag=TAG).info(
            f"web_search provider={primary} has no API key; using free providers"
        )
        primary = "duckduckgo"

    raw_fallbacks = config.get("fallback_providers")
    if isinstance(raw_fallbacks, str):
        fallbacks = [part.strip().lower() for part in raw_fallbacks.split(",") if part.strip()]
    elif isinstance(raw_fallbacks, list):
        fallbacks = [str(part).strip().lower() for part in raw_fallbacks if str(part).strip()]
    else:
        fallbacks = list(_default_free_chain_names())

    ordered: List[str] = []
    for name in [primary, *fallbacks, *_default_free_chain_names()]:
        if name and name not in ordered:
            ordered.append(name)

    chain: List[Tuple[str, Callable[[], str]]] = []
    for name in ordered:
        if name == "metaso":
            key = _provider_api_key(config, "metaso")
            if not key:
                continue
            chain.append(
                (
                    "metaso",
                    lambda k=key: _search_metaso(k, query, max_results, timeout_sec),
                )
            )
        elif name == "tavily":
            key = _provider_api_key(config, "tavily")
            if not key:
                continue
            chain.append(
                (
                    "tavily",
                    lambda k=key: _search_tavily(k, query, max_results, timeout_sec),
                )
            )
        elif name == "duckduckgo":
            chain.append(
                (
                    "duckduckgo",
                    lambda: _search_duckduckgo(query, max_results, timeout_sec),
                )
            )
        elif name == "wikipedia":
            chain.append(
                (
                    "wikipedia",
                    lambda: _search_wikipedia(query, max_results, language, timeout_sec),
                )
            )
    # Hard guarantee: never return an empty chain when free providers exist.
    if not chain:
        chain = [
            (
                "duckduckgo",
                lambda: _search_duckduckgo(query, max_results, timeout_sec),
            ),
            (
                "wikipedia",
                lambda: _search_wikipedia(query, max_results, language, timeout_sec),
            ),
        ]
    return chain


@register_function("web_search", WEB_SEARCH_FUNCTION_DESC, ToolType.SYSTEM_CTL)
def web_search(conn: "ConnectionHandler", query: str = None):
    query = _normalize_query(query)
    logger.bind(tag=TAG).info(f"web_search called | query={query!r}")

    if not query:
        return ActionResponse(
            Action.REQLLM,
            "Child asked to search but gave no keywords. Ask a short safe learning question.",
            None,
        )
    if _is_child_unsafe_query(query):
        return ActionResponse(
            Action.REQLLM,
            "Child-safe search cannot search adult, violent, illegal, self-harm, "
            "or graphic content. Please ask a safer learning question.",
            None,
        )

    config = _plugin_config(conn)
    max_results = _coerce_max_results(config.get("max_results", DEFAULT_MAX_RESULTS))
    timeout_sec = _coerce_timeout(config.get("timeout_sec") or config.get("timeout") or DEFAULT_TIMEOUT_SEC)
    language = _preferred_language(query, config)

    chain = _build_provider_chain(
        config,
        query=query,
        max_results=max_results,
        timeout_sec=timeout_sec,
        language=language,
    )

    logger.bind(tag=TAG).info(
        f"web_search plan | providers={[name for name, _ in chain]} | "
        f"max_results={max_results} | timeout={timeout_sec} | lang={language}"
    )

    last_error = "unknown"
    for name, search_fn in chain:
        try:
            result_text = search_fn()
            if not result_text or "no suitable learning results" in result_text.lower():
                last_error = f"{name}:empty"
                logger.bind(tag=TAG).info(f"web_search empty from provider={name}")
                continue
            logger.bind(tag=TAG).info(f"web_search success provider={name}")
            return ActionResponse(Action.REQLLM, result_text, None)
        except requests.exceptions.Timeout:
            last_error = f"{name}:timeout"
            logger.bind(tag=TAG).warning(f"web_search timeout provider={name}")
        except requests.exceptions.RequestException as exc:
            last_error = f"{name}:http"
            logger.bind(tag=TAG).warning(f"web_search http failure provider={name}: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            last_error = f"{name}:error"
            logger.bind(tag=TAG).error(f"web_search error provider={name}: {exc}")

    return ActionResponse(
        Action.REQLLM,
        "Child-safe search could not fetch results right now "
        f"(last={last_error}). Answer briefly from general knowledge "
        "or invite the child to try a simpler question.",
        None,
    )
