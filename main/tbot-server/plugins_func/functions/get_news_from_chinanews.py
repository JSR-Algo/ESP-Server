import random
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler


TAG = __name__
logger = setup_logging()

GET_NEWS_FROM_CHINANEWS_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "get_news_from_chinanews",
        "description": (
            "Call when user requests view or listen to news (e.g.'Get news''What news today')."
            "User can specify news type, such as social news, tech news, international news, etc."
            "If not specified, default broadcast social news."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "News category, such as society, tech, international. Optional parameter. If not provided, use default category",
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


def fetch_news_from_rss(rss_url):
    """Get news list from RSS source"""
    try:
        response = requests.get(rss_url)
        response.raise_for_status()

        # ParseXML
        root = ET.fromstring(response.content)

        # Find AllitemElement (news item)
        news_items = []
        for item in root.findall(".//item"):
            title = (
                item.find("title").text if item.find("title") is not None else "noneTitle"
            )
            link = item.find("link").text if item.find("link") is not None else "#"
            description = (
                item.find("description").text
                if item.find("description") is not None
                else "noneDescription"
            )
            pubDate = (
                item.find("pubDate").text
                if item.find("pubDate") is not None
                else "UnknownTime"
            )

            news_items.append(
                {
                    "title": title,
                    "link": link,
                    "description": description,
                    "pubDate": pubDate,
                }
            )

        return news_items
    except Exception as e:
        logger.bind(tag=TAG).error(f"GetRSSNews Failed: {e}")
        return []


def fetch_news_detail(url):
    """Get news detail page content and summarize"""
    try:
        response = requests.get(url)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")

        # Try to extract main content (selector here needs adjustment based on actual website structure)
        content_div = soup.select_one(
            ".content_desc, .content, article, .article-content"
        )
        if content_div:
            paragraphs = content_div.find_all("p")
            content = "\n".join(
                [p.get_text().strip() for p in paragraphs if p.get_text().strip()]
            )
            return content
        else:
            # If specific not foundContentarea, try get all paragraphs
            paragraphs = soup.find_all("p")
            content = "\n".join(
                [p.get_text().strip() for p in paragraphs if p.get_text().strip()]
            )
            return content[:2000]  # Limit Length
    except Exception as e:
        logger.bind(tag=TAG).error(f"Get news detail failed: {e}")
        return "Cannot get detailsContent"


def map_category(category_text):
    """Map user-input Chinese category to category key in config file"""
    if not category_text:
        return None

    # Category mapping dict, currently supports society, international, and finance news. For more types, see config file
    category_map = {
        # Society news
        "Society": "society_rss_url",
        "Social News": "society_rss_url",
        # International news
        "International": "world_rss_url",
        "International News": "world_rss_url",
        # Finance news
        "Finance": "finance_rss_url",
        "Financial News": "finance_rss_url",
        "Finance": "finance_rss_url",
        "Economy": "finance_rss_url",
    }

    # Convert to lowercase and remove spaces
    normalized_category = category_text.lower().strip()

    # Return mapping result. If no match, return original input
    return category_map.get(normalized_category, category_text)


@register_function(
    "get_news_from_chinanews",
    GET_NEWS_FROM_CHINANEWS_FUNCTION_DESC,
    ToolType.SYSTEM_CTL,
)
def get_news_from_chinanews(
    conn: "ConnectionHandler",
    category: str = None,
    detail: bool = False,
    lang: str = "zh_CN",
):
    """Get news and randomly select one to broadcast, or get details of previous news item"""
    try:
        # If detail is True, get detailed content of previous news item
        if detail:
            if (
                not hasattr(conn, "last_news_link")
                or not conn.last_news_link
                or "link" not in conn.last_news_link
            ):
                return ActionResponse(
                    Action.REQLLM,
                    "Sorry, no recently queried news found. Please get one news item first.",
                    None,
                )

            link = conn.last_news_link.get("link")
            title = conn.last_news_link.get("title", "UnknownTitle")

            if link == "#":
                return ActionResponse(
                    Action.REQLLM, "Sorry, no available link for this news to get detailsContent.", None
                )

            logger.bind(tag=TAG).debug(f"Get news details: {title}, URL={link}")

            # Get news details
            detail_content = fetch_news_detail(link)

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
                f"DetailedContent: {detail_content}\n\n"
                f"(Please summarize above newsContentSummarize, extract keyInfoBroadcast to user in natural, smooth way,"
                f"Do not mention this is summary. Tell it like complete news story)"
            )

            return ActionResponse(Action.REQLLM, detail_report, None)

        # Otherwise, get news list and randomly select one
        # Get RSS URL from config
        rss_config = conn.config.get("plugins", {}).get("get_news_from_chinanews", {})
        default_rss_url = rss_config.get(
            "default_rss_url", "https://www.chinanews.com.cn/rss/society.xml"
        )

        # Map user input category to category key in config
        mapped_category = map_category(category)

        # If category provided, try get corresponding from configURL
        rss_url = default_rss_url
        if mapped_category and mapped_category in rss_config:
            rss_url = rss_config[mapped_category]

        logger.bind(tag=TAG).info(
            f"Get News: Original Category={category}, Mapped Category={mapped_category}, URL={rss_url}"
        )

        # Get news list
        news_items = fetch_news_from_rss(rss_url)

        if not news_items:
            return ActionResponse(
                Action.REQLLM, "Sorry, failed to get newsInfoPlease try again later.", None
            )

        # Randomly select one news item
        selected_news = random.choice(news_items)

        # Save current news link to connection object for later detail query
        if not hasattr(conn, "last_news_link"):
            conn.last_news_link = {}
        conn.last_news_link = {
            "link": selected_news.get("link", "#"),
            "title": selected_news.get("title", "UnknownTitle"),
        }

        # Build news report
        news_report = (
            f"Based on following data, use{lang}Respond to user's news query request:\n\n"
            f"NewsTitle: {selected_news['title']}\n"
            f"Publish Time: {selected_news['pubDate']}\n"
            f"NewsContent: {selected_news['description']}\n"
            f"(Broadcast this news to user naturally and fluently. Can appropriatelySummary content,"
            f"Read news directly, no extra unnecessaryContent."
            f"If user asks for more details, tell user can say'Please introduce this news in detail'Get MoreContent)"
        )

        return ActionResponse(Action.REQLLM, news_report, None)

    except Exception as e:
        logger.bind(tag=TAG).error(f"Get news error: {e}")
        return ActionResponse(
            Action.REQLLM, "Sorry, error occurred while getting newsErrorPlease try again later.", None
        )
