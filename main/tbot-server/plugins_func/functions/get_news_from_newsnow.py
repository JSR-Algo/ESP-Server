import random
import requests
import json
from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler


TAG = __name__
logger = setup_logging()

CHANNEL_MAP = {
    "V2EX": "v2ex-share",
    "Zhihu": "zhihu",
    "Weibo": "weibo",
    "Lianhe Zaobao": "zaobao",
    "Coolapk": "coolapk",
    "MKTNews": "mktnews-flash",
    "Wallstreetcn": "wallstreetcn-quick",
    "36Kr": "36kr-quick",
    "Douyin": "douyin",
    "Hupu": "hupu",
    "Baidu Tieba": "tieba",
    "Toutiao": "toutiao",
    "IT Home": "ithome",
    "The Paper": "thepaper",
    "Sputnik News": "sputniknewscn",
    "Reference News": "cankaoxiaoxi",
    "PCBeta": "pcbeta-windows11",
    "Cailian Press": "cls-depth",
    "Xueqiu": "xueqiu-hotstock",
    "Gelonhui": "gelonghui",
    "Fab Finance": "fastbull-express",
    "Solidot": "solidot",
    "Hacker News": "hackernews",
    "Product Hunt": "producthunt",
    "Github": "github-trending-today",
    "Bilibili": "bilibili-hot-search",
    "Kuaishou": "kuaishou",
    "Reliable News": "kaopu",
    "Jin10 Data": "jin10",
    "Baidu Hot Search": "baidu",
    "Nowcoder": "nowcoder",
    "sspai": "sspai",
    "Juejin": "juejin",
    "Phoenix News": "ifeng",
    "Chongbuluo": "chongbuluo-latest",
}

# Default NewsSourceDictionary, used when config not specified
DEFAULT_NEWS_SOURCES = "The Paper;Baidu Hot Search;Cailian Press"

def _get_newsnow_config(conn):
    """Get newsnow plugin config from connection config, use conn.common_config first, fall back to conn.config"""
    # Prefer fromPublic configGet (keep localconfig.yamlconfig)
    common_plugins = getattr(conn, "common_config", {}).get("plugins", {})
    common_newsnow = common_plugins.get("get_news_from_newsnow", {})
    common_sources = common_newsnow.get("news_sources", "")
    if isinstance(common_sources, str) and common_sources.strip():
        return common_sources

    # Fallback get from connection config
    plugins = conn.config.get("plugins", {})
    newsnow = plugins.get("get_news_from_newsnow", {})
    sources = newsnow.get("news_sources", "")
    if isinstance(sources, str) and sources.strip():
        return sources

    return ""

def get_news_sources_from_config(conn):
    """Get news source string from config"""
    try:
        result = _get_newsnow_config(conn)
        if result:
            logger.bind(tag=TAG).debug(f"Using configured news source: {result}")
            return result

        logger.bind(tag=TAG).debug("News source config not found, using default config")
        return DEFAULT_NEWS_SOURCES

    except Exception as e:
        logger.bind(tag=TAG).error(f"Failed to get news source config: {e}; using default config")
        return DEFAULT_NEWS_SOURCES


# Get available news sources from default configName(runtime byget_news_sources_from_configdynamic get)
example_sources_str = DEFAULT_NEWS_SOURCES.replace(";", ",")

GET_NEWS_FROM_NEWSNOW_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "get_news_from_newsnow",
        "description": (
            "Child-safe news lookup. Call only when the child explicitly asks for "
            "news. Prefer science, technology, school, culture, sports, and light "
            "general-interest updates. Do not read adult, violent, graphic, crime, "
            "war, self-harm, hateful, extremist, medical, legal, financial, or "
            "private personal data content."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": f"News source standardChineseName, for example{example_sources_str}etc. Optional parameter, use default news source if not provided",
                },
                "detail": {
                    "type": "boolean",
                    "description": "Whether get detailsContent, default isfalse. If istrue, then get details of previous newsContent",
                },
                "lang": {
                    "type": "string",
                    "description": "Return language code used by user, e.g. zh_CN/zh_HK/en_US/ja_JP, default zh_CN",
                },
            },
            "required": ["lang"],
        },
    },
}

CHILD_UNSAFE_NEWS_KEYWORDS = (
    "adult",
    "porn",
    "sex",
    "nude",
    "violent",
    "violence",
    "gore",
    "blood",
    "crime",
    "murder",
    "war",
    "attack",
    "suicide",
    "self harm",
    "self-harm",
    "hurt myself",
    "hurt yourself",
    "harm myself",
    "harm yourself",
    "injure myself",
    "injure yourself",
    "cut myself",
    "cut yourself",
    "cut my wrist",
    "cut your wrist",
    "hang myself",
    "hang yourself",
    "choke myself",
    "choke yourself",
    "drown myself",
    "drown yourself",
    "burn myself",
    "burn yourself",
    "stab myself",
    "stab yourself",
    "shoot myself",
    "shoot yourself",
    "overdose",
    "drink bleach",
    "take pills",
    "take all your pills",
    "swallow pills",
    "jump off a bridge",
    "jump off a building",
    "jump off a roof",
    "jump off a window",
    "poison myself",
    "poison yourself",
    "starve myself",
    "starve yourself",
    "end my life",
    "end your life",
    "take my own life",
    "take your own life",
    "unalive myself",
    "unalive yourself",
    "kill",
    "weapon",
    "drug",
    "gambling",
    "terror",
    "extremist",
    "hate",
)


def _is_child_safe_news_text(text: str) -> bool:
    lowered = str(text or "").lower()
    return not any(keyword in lowered for keyword in CHILD_UNSAFE_NEWS_KEYWORDS)


def _child_safe_news_items(news_items):
    return [
        item
        for item in news_items
        if _is_child_safe_news_text(item.get("title", ""))
    ]


def fetch_news_from_api(conn: "ConnectionHandler", source="thepaper"):
    """Get news list from API"""
    try:
        api_url = f"https://newsnow.busiyi.world/api/s?id={source}"

        news_config = conn.config.get("plugins", {}).get("get_news_from_newsnow", {})
        if news_config.get("url"):
            api_url = news_config["url"] + source

        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(api_url, headers=headers, timeout=10)
        response.raise_for_status()

        data = response.json()

        if "items" in data:
            return data["items"]
        else:
            logger.bind(tag=TAG).error(f"Get NewsAPIResponseFormatError: {data}")
            return []

    except Exception as e:
        logger.bind(tag=TAG).error(f"Get NewsAPIFail: {e}")
        return []


def fetch_news_detail(url):
    """Get news detail page content and use MarkItDown to clean HTML"""
    try:
        try:
            from markitdown import MarkItDown
        except ModuleNotFoundError:
            logger.bind(tag=TAG).warning("markitdown not installed; news detail unavailable")
            return "Cannot get detailsContent"

        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # UseMarkItDownCleanHTMLContent
        md = MarkItDown(enable_plugins=False)
        result = md.convert(response)

        # Get cleaned textContent
        clean_text = result.text_content

        # If cleanedContentis empty, returnPromptInfo
        if not clean_text or len(clean_text.strip()) == 0:
            logger.bind(tag=TAG).warning(f"Cleaned newsContentEmpty: {url}")
            return "Cannot parse news detailContent, may be special website structure orContentRestricted."

        return clean_text
    except Exception as e:
        logger.bind(tag=TAG).error(f"Get news detail failed: {e}")
        return "Cannot get detailsContent"


@register_function(
    "get_news_from_newsnow",
    GET_NEWS_FROM_NEWSNOW_FUNCTION_DESC,
    ToolType.SYSTEM_CTL,
)
def get_news_from_newsnow(
    conn: "ConnectionHandler",
    source: str = "The Paper",
    detail: bool = False,
    lang: str = "zh_CN",
):
    """Get news and randomly select one to broadcast, or get details of previous news item"""
    try:
        # Get currently configured news sources
        news_sources = get_news_sources_from_config(conn)

        # If detail is True, get details of previous news item
        detail = str(detail).lower() == "true"
        if detail:
            if (
                not hasattr(conn, "last_newsnow_link")
                or not conn.last_newsnow_link
                or "url" not in conn.last_newsnow_link
            ):
                return ActionResponse(
                    Action.REQLLM,
                    "Sorry, no recently queried news found. Please get one news item first.",
                    None,
                )

            url = conn.last_newsnow_link.get("url")
            title = conn.last_newsnow_link.get("title", "UnknownTitle")
            source_id = conn.last_newsnow_link.get("source_id", "thepaper")
            source_name = CHANNEL_MAP.get(source_id, "UnknownSource")

            if not _is_child_safe_news_text(title):
                return ActionResponse(
                    Action.REQLLM,
                    "Child-safe news cannot provide details for adult, violent, "
                    "crime, war, self-harm, or graphic content.",
                    None,
                )

            if not url or url == "#":
                return ActionResponse(
                    Action.REQLLM, "Sorry, no available link for this news to get detailsContent.", None
                )

            logger.bind(tag=TAG).debug(
                f"Get news details: {title}, Source: {source_name}, URL={url}"
            )

            # Get news details
            detail_content = fetch_news_detail(url)

            if not detail_content or detail_content == "Cannot get detailsContent":
                return ActionResponse(
                    Action.REQLLM,
                    f"Sorry, cannot get details of {title}, link may be invalid or website structure changed.",
                    None,
                )

            # Build detail report
            detail_report = (
                f"Based on following data, use{lang}Respond to user's news detail query request:\n\n"
                f"NewsTitle: {title}\n"
                # f"NewsSource: {source_name}\n"
                f"DetailedContent: {detail_content}\n\n"
                f"(Please summarize above newsContentSummarize, extract keyInfoBroadcast to user in natural, smooth way,"
                f"Do not mention this is summary. Tell it like complete news)"
            )

            return ActionResponse(Action.REQLLM, detail_report, None)

        # Otherwise, get news list and randomly choose one
        # willChineseNameConvert to EnglishID
        english_source_id = None

        # Check inputChineseNameWhether in configured news sources
        news_sources_list = [
            name.strip() for name in news_sources.split(";") if name.strip()
        ]
        if source in news_sources_list:
            # If inputChineseNameIn configured news sources, at CHANNEL_MAP Find corresponding English inID
            english_source_id = CHANNEL_MAP.get(source)

        # If corresponding English not foundID,use default source
        if not english_source_id:
            logger.bind(tag=TAG).warning(f"Invalid news source: {source},use default sourceThe Paper")
            english_source_id = "thepaper"
            source = "The Paper"

        logger.bind(tag=TAG).info(f"Get News: News source={source}({english_source_id})")

        # Get news list
        news_items = fetch_news_from_api(conn, english_source_id)

        if not news_items:
            return ActionResponse(
                Action.REQLLM,
                f"Sorry, failed from{source}Got newsInfoPlease try later or try other news source.",
                None,
            )

        news_items = _child_safe_news_items(news_items)
        if not news_items:
            return ActionResponse(
                Action.REQLLM,
                "Child-safe news could not find a suitable story from this source. "
                "Please ask for science, school, sports, culture, or technology news.",
                None,
            )

        # Randomly select one news item
        selected_news = random.choice(news_items)

        # Save current news link to connection object for later detail query
        if not hasattr(conn, "last_newsnow_link"):
            conn.last_newsnow_link = {}
        conn.last_newsnow_link = {
            "url": selected_news.get("url", "#"),
            "title": selected_news.get("title", "UnknownTitle"),
            "source_id": english_source_id,
        }

        # Build news report
        news_report = (
            f"Based on following data, use{lang}Respond to user's news query request:\n\n"
            f"NewsTitle: {selected_news['title']}\n"
            # f"NewsSource: {source}\n"
            f"(Broadcast this news to user naturally and fluentlyTitle,"
            f"PromptUser can request detailedContent, then get news detailsContent.)"
        )

        return ActionResponse(Action.REQLLM, news_report, None)

    except Exception as e:
        logger.bind(tag=TAG).error(f"Get news error: {e}")
        return ActionResponse(
            Action.REQLLM, "Sorry, error occurred while getting newsErrorPlease try again later.", None
        )
