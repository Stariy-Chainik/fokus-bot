from datetime import datetime


DATE_FMT = "%Y-%m-%d"
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"
DISPLAY_FMT = "%d.%m.%Y"


def now_str() -> str:
    """Текущее время для хранения в таблице: YYYY-MM-DD HH:MM:SS"""
    return datetime.now().strftime(DATETIME_FMT)


def format_date_display(value: str) -> str:
    """Переводит YYYY-MM-DD → ДД.ММ.ГГГГ для отображения в боте."""
    return datetime.strptime(value, DATE_FMT).strftime(DISPLAY_FMT)


def period_month_from_date(value: str) -> str:
    """Возвращает YYYY-MM (период) из строки YYYY-MM-DD."""
    return value[:7]


def display_period(period_month: str) -> str:
    """Переводит YYYY-MM → ММ.ГГГГ для отображения."""
    dt = datetime.strptime(period_month, "%Y-%m")
    return dt.strftime("%m.%Y")


MONTHS_RU = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def month_name_ru(month: int) -> str:
    """Полное русское название месяца по 1-based номеру (1→Январь … 12→Декабрь)."""
    return MONTHS_RU[month - 1]


_MONTHS_RU_SHORT = [
    "янв", "фев", "мар", "апр", "май", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
]
_WEEKDAYS_RU_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def format_date_short_with_wd(value: str) -> str:
    """Переводит YYYY-MM-DD → «23 апр, чт» — для заголовков группировки по дате."""
    dt = datetime.strptime(value, DATE_FMT)
    return f"{dt.day} {_MONTHS_RU_SHORT[dt.month - 1]}, {_WEEKDAYS_RU_SHORT[dt.weekday()]}"


def current_period() -> str:
    """Текущий месяц в формате YYYY-MM."""
    from datetime import date as _date
    return _date.today().strftime("%Y-%m")


def next_period() -> str:
    """Следующий месяц в формате YYYY-MM."""
    from dateutil.relativedelta import relativedelta
    from datetime import date as _date
    return (_date.today() + relativedelta(months=1)).strftime("%Y-%m")


def last_periods(n: int) -> list[str]:
    """Последние n периодов (YYYY-MM), от текущего месяца назад.

    Единый генератор для пикеров периодов (счета, зарплаты, статистика и т.п.).
    """
    from dateutil.relativedelta import relativedelta  # локально: dateutil тяжёлый на импорт
    from datetime import date as _date
    today = _date.today()
    return [(today - relativedelta(months=i)).strftime("%Y-%m") for i in range(n)]


def period_label(period_month: str) -> str:
    """«Сентябрь 2026» — подпись месяца словами (экраны родителя, история оплат, списки занятий)."""
    year, month = period_month.split("-")
    return f"{month_name_ru(int(month))} {year}"


def periods_since(start: str, end: str | None = None) -> list[str]:
    """Месяцы YYYY-MM от `start` по `end` (по умолчанию текущий) включительно, по порядку.

    Пустой или кривой `start`, как и `start` позже `end`, — только `end`. Для «сколько должен всего»:
    остаток по месяцам с PARENT_BILLS_SINCE_PERIOD по текущий.
    """
    end = end or current_period()
    try:
        y, m = (int(x) for x in start.split("-")[:2])
    except (ValueError, AttributeError):
        return [end]
    out: list[str] = []
    while f"{y:04d}-{m:02d}" <= end and len(out) < 240:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out or [end]
