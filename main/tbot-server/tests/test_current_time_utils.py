from datetime import datetime

from core.utils import current_time


class _FixedDateTime:
    @classmethod
    def now(cls):
        return datetime(2026, 6, 19, 7, 8, 9)


class _Lunar:
    def __init__(self, value, godType=None):
        self.value = value
        self.godType = godType
        self.lunarYearCn = "Dragon"
        self.lunarMonthCn = "FifthMonth"
        self.lunarDayCn = "FirstDay"


def test_current_time_date_weekday_and_info_are_formatted(monkeypatch):
    monkeypatch.setattr(current_time, "datetime", _FixedDateTime)
    monkeypatch.setattr(current_time.cnlunar, "Lunar", _Lunar)

    assert current_time.get_current_time() == "07:08"
    assert current_time.get_current_date() == "2026-06-19"
    assert current_time.get_current_weekday() == "Friday"
    assert current_time.get_current_lunar_date() == "DragonyearFifthMontFirstDay"
    assert current_time.get_current_time_info() == (
        "07:08",
        "2026-06-19",
        "Friday",
        "DragonyearFifthMontFirstDay",
    )


def test_current_lunar_date_failure_returns_fallback(monkeypatch):
    monkeypatch.setattr(current_time, "datetime", _FixedDateTime)

    def raise_lunar(*_args, **_kwargs):
        raise RuntimeError("lunar unavailable")

    monkeypatch.setattr(current_time.cnlunar, "Lunar", raise_lunar)

    assert current_time.get_current_lunar_date() == "Get lunar date failed"
