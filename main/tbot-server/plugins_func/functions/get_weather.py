import requests
from bs4 import BeautifulSoup
from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from core.utils.util import get_ip_info
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

GET_WEATHER_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": (
            "Get weather for location. User should provide location, e.g. if user says Hangzhou weather, parameter is: Hangzhou."
            "If user says province, default to provincial capital. If user says neither province nor city but place name, default to provincial capital of province where place is located."
            "Important: Local 7-day weather is already provided in context. Never call this tool unless user specifies another city."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Place name, e.g. Hangzhou. Optional parameter, not passed if not provided",
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

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36"
    )
}

# Weather Code https://dev.qweather.com/docs/resource/icons/#weather-icons
WEATHER_CODE_MAP = {
    "100": "Clear",
    "101": "Cloudy",
    "102": "Partly cloudy",
    "103": "Clear to cloudy",
    "104": "Overcast",
    "150": "Clear",
    "151": "Cloudy",
    "152": "Partly cloudy",
    "153": "Clear to cloudy",
    "300": "Shower",
    "301": "Heavy shower",
    "302": "Thunder shower",
    "303": "Severe thunderstorm",
    "304": "Thunderstorm with hail",
    "305": "Light rain",
    "306": "Moderate rain",
    "307": "Heavy rain",
    "308": "Extreme rainfall",
    "309": "Drizzle/light rain",
    "310": "Torrential rain",
    "311": "Torrential rain",
    "312": "Exceptional rainstorm",
    "313": "Freezing rain",
    "314": "Light to moderate rain",
    "315": "Moderate to heavy rain",
    "316": "Heavy to torrential rain",
    "317": "Heavy rain to torrential rain",
    "318": "Heavy rainstorm to extremely heavy rainstorm",
    "350": "Shower",
    "351": "Heavy shower",
    "399": "Rain",
    "400": "Light snow",
    "401": "Moderate snow",
    "402": "Heavy snow",
    "403": "Blizzard",
    "404": "Sleet",
    "405": "Rain and snow",
    "406": "Showers with snow",
    "407": "Snow shower",
    "408": "Light to moderate snow",
    "409": "Moderate to heavy snow",
    "410": "Heavy to blizzard",
    "456": "Showers with snow",
    "457": "Snow shower",
    "499": "Snow",
    "500": "Mist",
    "501": "Fog",
    "502": "Haze",
    "503": "Blowing sand",
    "504": "Dust",
    "507": "Sandstorm",
    "508": "Severe sandstorm",
    "509": "Dense fog",
    "510": "Dense fog",
    "511": "Moderate haze",
    "512": "Heavy haze",
    "513": "Severe haze",
    "514": "Fog",
    "515": "Extremely dense fog",
    "900": "Hot",
    "901": "Cold",
    "999": "Unknown",
}


def fetch_city_info(location, api_key, api_host):
    url = f"https://{api_host}/geo/v2/city/lookup?key={api_key}&location={location}&lang=zh"
    response = requests.get(url, headers=HEADERS).json()
    if response.get("error") is not None:
        logger.bind(tag=TAG).error(
            f"Failed to get weather, reason:{response.get('error', {}).get('detail')}"
        )
        return None
    return response.get("location", [])[0] if response.get("location") else None


def fetch_weather_page(url):
    response = requests.get(url, headers=HEADERS)
    return BeautifulSoup(response.text, "html.parser") if response.ok else None


def parse_weather_info(soup):
    city_name = soup.select_one("h1.c-submenu__location").get_text(strip=True)

    current_abstract = soup.select_one(".c-city-weather-current .current-abstract")
    current_abstract = (
        current_abstract.get_text(strip=True) if current_abstract else "Unknown"
    )

    current_basic = {}
    for item in soup.select(
        ".c-city-weather-current .current-basic .current-basic___item"
    ):
        parts = item.get_text(strip=True, separator=" ").split(" ")
        if len(parts) == 2:
            key, value = parts[1], parts[0]
            current_basic[key] = value

    temps_list = []
    for row in soup.select(".city-forecast-tabs__row")[:7]:  # Take data for first 7 days
        date = row.select_one(".date-bg .date").get_text(strip=True)
        weather_code = (
            row.select_one(".date-bg .icon")["src"].split("/")[-1].split(".")[0]
        )
        weather = WEATHER_CODE_MAP.get(weather_code, "Unknown")
        temps = [span.get_text(strip=True) for span in row.select(".tmp-cont .temp")]
        high_temp, low_temp = (temps[0], temps[-1]) if len(temps) >= 2 else (None, None)
        temps_list.append((date, weather, high_temp, low_temp))

    return city_name, current_abstract, current_basic, temps_list


@register_function("get_weather", GET_WEATHER_FUNCTION_DESC, ToolType.SYSTEM_CTL)
def get_weather(conn: "ConnectionHandler", location: str = None, lang: str = "zh_CN"):
    from core.utils.cache.manager import cache_manager, CacheType

    weather_config = conn.config.get("plugins", {}).get("get_weather", {})
    api_host = weather_config.get("api_host", "mj7p3y7naa.re.qweatherapi.com")
    api_key = weather_config.get("api_key", "")
    default_location = weather_config.get("default_location", "Guangzhou")
    client_ip = conn.client_ip

    # Prefer user-providedlocationParameter
    if not location:
        # Through clientIPParse City
        if client_ip:
            # Get from cache firstIPcorresponding cityInfo
            cached_ip_info = cache_manager.get(CacheType.IP_INFO, client_ip)
            if cached_ip_info:
                location = cached_ip_info.get("city")
            else:
                # Cache miss, callAPIGet
                ip_info = get_ip_info(client_ip, logger)
                if ip_info:
                    cache_manager.set(CacheType.IP_INFO, client_ip, ip_info)
                    location = ip_info.get("city")

            if not location:
                location = default_location
        else:
            # If noneIPUse default location
            location = default_location
    # Try get full weather report from cache
    weather_cache_key = f"full_weather_{location}_{lang}"
    cached_weather_report = cache_manager.get(CacheType.WEATHER, weather_cache_key)
    if cached_weather_report:
        return ActionResponse(Action.REQLLM, cached_weather_report, None)

    # Cache miss, get real-time weather data
    city_info = fetch_city_info(location, api_key, api_host)
    if not city_info:
        return ActionResponse(
            Action.REQLLM, f"No related city found: {location}please confirm location is correct", None
        )
    soup = fetch_weather_page(city_info["fxLink"])
    if not soup:
        return ActionResponse(Action.REQLLM, None, "Request Failed")
    city_name, current_abstract, current_basic, temps_list = parse_weather_info(soup)

    weather_report = f"Location you queried is:{city_name}\n\nCurrent Weather: {current_abstract}\n"

    # Add valid current weather parameters
    if current_basic:
        weather_report += "Detailed parameters:\n"
        for key, value in current_basic.items():
            if value != "0":  # Filter invalid values
                weather_report += f"  · {key}: {value}\n"

    # Add 7-day forecast
    weather_report += "\nFuture7day forecast:\n"
    for date, weather, high, low in temps_list:
        weather_report += f"{date}: {weather}, temperature {low}~{high}\n"

    # Promptlanguage
    weather_report += "\n(If need specific day's weather, tell me date)"

    # Cache full weather report
    cache_manager.set(CacheType.WEATHER, weather_cache_key, weather_report)

    return ActionResponse(Action.REQLLM, weather_report, None)
