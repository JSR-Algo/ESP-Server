from datetime import datetime
import cnlunar
from plugins_func.register import register_function, ToolType, ActionResponse, Action

get_lunar_function_desc = {
    "type": "function",
    "function": {
        "name": "get_lunar",
        "description": (
            "For lunar calendar and almanac info for specific date."
            "User can specify query content, such as: lunar date, Heavenly Stems and Earthly Branches, solar terms, zodiac, constellation, Eight Characters, dos and don'ts, etc."
            "If no query content is specified, default query is sexagenary year and lunar date."
            "For'What is lunar date today','Today's lunar date'For basic queries like this, use directlycontextInInfoDo not call this tool."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date to query, format isYYYY-MM-DD, for example2024-01-01If not provided, use current date",
                },
                "query": {
                    "type": "string",
                    "description": "to QueryContent, for exampleOvercastCalendar date, heavenly stems and earthly branches, festivals, solar terms, zodiac, constellation, bazi, do/don't, etc.",
                },
            },
            "required": [],
        },
    },
}


@register_function("get_lunar", get_lunar_function_desc, ToolType.WAIT)
def get_lunar(date=None, query=None):
    """
    Get current lunar calendar info, Heavenly Stems and Earthly Branches, solar terms, zodiac, constellation, Bazi, almanac do/don't info
    """
    from core.utils.cache.manager import cache_manager, CacheType

    # If date parameter provided, use specified date; otherwise use current date
    if date:
        try:
            now = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return ActionResponse(
                Action.REQLLM,
                f"Date FormatError, please useYYYY-MM-DDFormat, e.g.:2024-01-01",
                None,
            )
    else:
        now = datetime.now()

    current_date = now.strftime("%Y-%m-%d")

    # If query is None, use default text
    if query is None:
        query = "Default query sexagenary year and lunar date"

    # Try get lunar info from cache
    lunar_cache_key = f"lunar_info_{current_date}"
    cached_lunar_info = cache_manager.get(CacheType.LUNAR, lunar_cache_key)
    if cached_lunar_info:
        return ActionResponse(Action.REQLLM, cached_lunar_info, None)

    response_text = f"According to FollowingInfoRespond to user's query request, and provide with{query}RelatedInfo:\n"

    lunar = cnlunar.Lunar(now, godType="8char")
    response_text += (
        "Lunar calendarInfo:\n"
        "%syear%s%s\n" % (lunar.lunarYearCn, lunar.lunarMonthCn[:-1], lunar.lunarDayCn)
        + "Heavenly stems and earthly branches: %syear %smonth %sday\n" % (lunar.year8Char, lunar.month8Char, lunar.day8Char)
        + "Chinese zodiac: belongs to%s\n" % (lunar.chineseYearZodiac)
        + "Eight characters: %s\n"
        % (
            " ".join(
                [lunar.year8Char, lunar.month8Char, lunar.day8Char, lunar.twohour8Char]
            )
        )
        + "Today's Festival: %s\n"
        % (
            ",".join(
                filter(
                    None,
                    (
                        lunar.get_legalHolidays(),
                        lunar.get_otherHolidays(),
                        lunar.get_otherLunarHolidays(),
                    ),
                )
            )
        )
        + "Today's Solar Term: %s\n" % (lunar.todaySolarTerms)
        + "Next Solar Term: %s %syear%smonth%sday\n"
        % (
            lunar.nextSolarTerm,
            lunar.nextSolarTermYear,
            lunar.nextSolarTermDate[0],
            lunar.nextSolarTermDate[1],
        )
        + "This year's solar terms: %s\n"
        % (
            ", ".join(
                [
                    f"{term}({date[0]}month{date[1]}day)"
                    for term, date in lunar.thisYearSolarTermsDic.items()
                ]
            )
        )
        + "Zodiac Clash: %s\n" % (lunar.chineseZodiacClash)
        + "Constellation: %s\n" % (lunar.starZodiac)
        + "Nayin: %s\n" % lunar.get_nayin()
        + "Pengzu Taboos: %s\n" % (lunar.get_pengTaboo(delimit=", "))
        + "Duty day: %sPosition\n" % lunar.get_today12DayOfficer()[0]
        + "Deity: %s(%s)\n"
        % (lunar.get_today12DayOfficer()[1], lunar.get_today12DayOfficer()[2])
        + "Twenty-eight mansions: %s\n" % lunar.get_the28Stars()
        + "Auspicious Direction: %s\n" % " ".join(lunar.get_luckyGodsDirection())
        + "Today's Fetus God: %s\n" % lunar.get_fetalGod()
        + "suitable: %s\n" % ",".join(lunar.goodThing[:10])
        + "avoid: %s\n" % ",".join(lunar.badThing[:10])
        + "(Default returns ganzhi year and lunar date; only when requesting do/don't queryInfoReturn today's dos and don'ts only when)"
    )

    # Cache Lunar CalendarInfo
    cache_manager.set(CacheType.LUNAR, lunar_cache_key, response_text)

    return ActionResponse(Action.REQLLM, response_text, None)
