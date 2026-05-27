"""
Time tool module
Provides unified time retrieval functions
"""

import cnlunar
from datetime import datetime

WEEKDAY_MAP = {
    "Monday": "Monday",
    "Tuesday": "Tuesday", 
    "Wednesday": "Wednesday",
    "Thursday": "Thursday",
    "Friday": "Friday",
    "Saturday": "Saturday",
    "Sunday": "Sunday",
}


def get_current_time() -> str:
    """
    Get current time string (format: HH:MM)
    """
    return datetime.now().strftime("%H:%M")


def get_current_date() -> str:
    """
    Get today's date string (format: YYYY-MM-DD)
    """
    return datetime.now().strftime("%Y-%m-%d")


def get_current_weekday() -> str:
    """
    Get day of week for today
    """
    now = datetime.now()
    return WEEKDAY_MAP[now.strftime("%A")]


def get_current_lunar_date() -> str:
    """
    Get lunar date string
    """
    try:
        now = datetime.now()
        today_lunar = cnlunar.Lunar(now, godType="8char")
        return "%syear%s%s" % (
            today_lunar.lunarYearCn,
            today_lunar.lunarMonthCn[:-1],
            today_lunar.lunarDayCn,
        )
    except Exception:
        return "Get lunar date failed"


def get_current_time_info() -> tuple:
    """
    Get current time info
    Return: (current time string, today date, today weekday, lunar date)
    """
    current_time = get_current_time()
    today_date = get_current_date()
    today_weekday = get_current_weekday()
    lunar_date = get_current_lunar_date()
    
    return current_time, today_date, today_weekday, lunar_date
