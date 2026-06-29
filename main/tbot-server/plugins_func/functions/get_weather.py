"""Weather lookup tool backed by Open-Meteo (https://open-meteo.com).

Open-Meteo is free and requires **no API key**, so this tool works out of the
box without any secret to provision. It also ships a companion geocoding API,
so we resolve a place name -> lat/lon -> forecast with two keyless JSON calls
(no HTML scraping, global coverage including Vietnam).

The tool keeps the historical contract intact:
  - registered as ``get_weather`` with ``ToolType.SYSTEM_CTL``
  - signature ``get_weather(conn, location=None, lang="vi", city=None)``
  - returns ``ActionResponse(Action.REQLLM, <report>, None)`` so the model
    narrates the result back to the child in their language.
"""

import requests
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
            "Get the current weather and a 7-day forecast for a place. "
            "Call this whenever the user asks about weather, temperature, rain, "
            "wind or the forecast — both for the local/current location and for "
            "any city the user names (e.g. 'thời tiết hôm nay', 'thời tiết Hà Nội', "
            "'Đà Nẵng có mưa không'). "
            "If no location is given, leave 'location' empty to use the device's "
            "own location. If the user names a province, use its capital city."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": (
                        "Place name to look up, e.g. 'Hà Nội', 'Da Nang', 'Tokyo'. "
                        "Optional — omit it to use the device's current location."
                    ),
                },
                "city": {
                    "type": "string",
                    "description": (
                        "Backward-compatible alias for location. Prefer location "
                        "for new calls."
                    ),
                },
                "lang": {
                    "type": "string",
                    "description": (
                        "Language code for place names, e.g. vi / en / zh / ja. "
                        "Optional, defaults to Vietnamese."
                    ),
                },
            },
        },
    },
}

HEADERS = {
    "User-Agent": "tbot-server/get_weather (+https://github.com)",
}

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_SEC = 3

# WMO weather interpretation codes -> human text.
# https://open-meteo.com/en/docs (see "WMO Weather interpretation codes")
WEATHER_CODE_MAP_EN = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

WEATHER_CODE_MAP_VI = {
    0: "Trời quang",
    1: "Phần lớn quang đãng",
    2: "Có mây rải rác",
    3: "Nhiều mây",
    45: "Sương mù",
    48: "Sương mù đóng băng",
    51: "Mưa phùn nhẹ",
    53: "Mưa phùn vừa",
    55: "Mưa phùn dày",
    56: "Mưa phùn băng nhẹ",
    57: "Mưa phùn băng dày",
    61: "Mưa nhỏ",
    63: "Mưa vừa",
    65: "Mưa to",
    66: "Mưa băng nhẹ",
    67: "Mưa băng to",
    71: "Tuyết rơi nhẹ",
    73: "Tuyết rơi vừa",
    75: "Tuyết rơi dày",
    77: "Hạt tuyết",
    80: "Mưa rào nhẹ",
    81: "Mưa rào vừa",
    82: "Mưa rào dữ dội",
    85: "Mưa tuyết nhẹ",
    86: "Mưa tuyết dày",
    95: "Dông",
    96: "Dông kèm mưa đá nhẹ",
    99: "Dông kèm mưa đá to",
}


def _short_lang(lang: str) -> str:
    """Normalise codes like ``zh_CN`` / ``vi-VN`` -> ``zh`` / ``vi``."""
    if not lang:
        return "vi"
    code = lang.replace("-", "_").split("_")[0].strip().lower()
    return code or "vi"


def _describe_code(code, lang_short: str) -> str:
    try:
        code_int = int(code)
    except (TypeError, ValueError):
        return "Unknown"
    table = WEATHER_CODE_MAP_VI if lang_short == "vi" else WEATHER_CODE_MAP_EN
    return table.get(code_int, "Unknown")


def _http_get_json(url: str, params: dict):
    response = requests.get(
        url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT_SEC
    )
    response.raise_for_status()
    return response.json()


def fetch_city_info(location: str, lang_short: str):
    """Resolve a place name to a geocoding record via Open-Meteo (keyless)."""
    data = _http_get_json(
        GEOCODE_URL,
        {"name": location, "count": 1, "language": lang_short, "format": "json"},
    )
    results = data.get("results") or []
    return results[0] if results else None


def fetch_forecast(latitude: float, longitude: float):
    """Fetch current conditions + 7-day daily forecast (keyless)."""
    return _http_get_json(
        FORECAST_URL,
        {
            "latitude": latitude,
            "longitude": longitude,
            "current": (
                "temperature_2m,relative_humidity_2m,apparent_temperature,"
                "weather_code,wind_speed_10m"
            ),
            "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "timezone": "auto",
            "forecast_days": 7,
            "temperature_unit": "celsius",
            "wind_speed_unit": "kmh",
        },
    )


def _format_place(city_info: dict) -> str:
    name = city_info.get("name") or ""
    parts = [name]
    admin1 = city_info.get("admin1")
    country = city_info.get("country")
    if admin1 and admin1 != name:
        parts.append(admin1)
    if country:
        parts.append(country)
    return ", ".join(p for p in parts if p)


def build_weather_report(city_info: dict, forecast: dict, lang_short: str) -> str:
    place = _format_place(city_info)
    current = forecast.get("current") or {}
    daily = forecast.get("daily") or {}

    cur_code = _describe_code(current.get("weather_code"), lang_short)
    temp = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    humidity = current.get("relative_humidity_2m")
    wind = current.get("wind_speed_10m")

    if lang_short == "vi":
        report = f"Thời tiết tại {place}.\n"
        now_bits = [f"Hiện tại: {cur_code}"]
        if temp is not None:
            now_bits.append(f"{temp}°C")
        if feels is not None:
            now_bits.append(f"cảm giác như {feels}°C")
        if humidity is not None:
            now_bits.append(f"độ ẩm {humidity}%")
        if wind is not None:
            now_bits.append(f"gió {wind} km/h")
        report += ", ".join(now_bits) + ".\n\nDự báo 7 ngày:\n"
    else:
        report = f"Weather for {place}.\n"
        now_bits = [f"Now: {cur_code}"]
        if temp is not None:
            now_bits.append(f"{temp}°C")
        if feels is not None:
            now_bits.append(f"feels like {feels}°C")
        if humidity is not None:
            now_bits.append(f"humidity {humidity}%")
        if wind is not None:
            now_bits.append(f"wind {wind} km/h")
        report += ", ".join(now_bits) + ".\n\n7-day forecast:\n"

    days = daily.get("time") or []
    codes = daily.get("weather_code") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    for idx, day in enumerate(days[:7]):
        code_text = _describe_code(codes[idx] if idx < len(codes) else None, lang_short)
        high = highs[idx] if idx < len(highs) else None
        low = lows[idx] if idx < len(lows) else None
        if low is not None and high is not None:
            report += f"- {day}: {code_text}, {low}~{high}°C\n"
        else:
            report += f"- {day}: {code_text}\n"

    return report


@register_function("get_weather", GET_WEATHER_FUNCTION_DESC, ToolType.SYSTEM_CTL)
def get_weather(
    conn: "ConnectionHandler",
    location: str = None,
    lang: str = "vi",
    city: str = None,
):
    from core.utils.cache.manager import cache_manager, CacheType

    weather_config = conn.config.get("plugins", {}).get("get_weather", {})
    default_location = weather_config.get("default_location", "Ho Chi Minh City")
    client_ip = conn.client_ip
    lang_short = _short_lang(lang)

    if city and not location:
        location = city

    # Prefer the user-provided location; otherwise resolve from client IP, then
    # fall back to the configured default city.
    if not location:
        if client_ip:
            cached_ip_info = cache_manager.get(CacheType.IP_INFO, client_ip)
            if cached_ip_info:
                location = cached_ip_info.get("city")
            else:
                ip_info = get_ip_info(client_ip, logger)
                if ip_info:
                    cache_manager.set(CacheType.IP_INFO, client_ip, ip_info)
                    location = ip_info.get("city")
        if not location:
            location = default_location

    # Serve a cached full report when available.
    weather_cache_key = f"full_weather_om_{location}_{lang_short}"
    cached_weather_report = cache_manager.get(CacheType.WEATHER, weather_cache_key)
    if cached_weather_report:
        return ActionResponse(Action.REQLLM, cached_weather_report, None)

    try:
        city_info = fetch_city_info(location, lang_short)
        if not city_info:
            msg = (
                f"Không tìm thấy địa điểm: {location}. Hãy xác nhận lại tên thành phố."
                if lang_short == "vi"
                else f"No matching place found: {location}. Please confirm the city name."
            )
            return ActionResponse(Action.REQLLM, msg, None)

        forecast = fetch_forecast(city_info["latitude"], city_info["longitude"])
        weather_report = build_weather_report(city_info, forecast, lang_short)
    except Exception as e:  # network / API / parse failures -> graceful message
        logger.bind(tag=TAG).error(f"get_weather failed for {location!r}: {e}")
        msg = (
            "Xin lỗi, hiện không lấy được thông tin thời tiết. Bé thử lại sau nhé."
            if lang_short == "vi"
            else "Sorry, weather information is unavailable right now. Please try again later."
        )
        return ActionResponse(Action.REQLLM, msg, None)

    cache_manager.set(CacheType.WEATHER, weather_cache_key, weather_report)
    return ActionResponse(Action.REQLLM, weather_report, None)
