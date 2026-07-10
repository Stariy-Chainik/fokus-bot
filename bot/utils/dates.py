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


def last_periods(n: int) -> list[str]:
    """Последние n периодов (YYYY-MM), от текущего месяца назад.

    Единый генератор для пикеров периодов (счета, зарплаты, статистика и т.п.).
    """
    from dateutil.relativedelta import relativedelta  # локально: dateutil тяжёлый на импорт
    from datetime import date as _date
    today = _date.today()
    return [(today - relativedelta(months=i)).strftime("%Y-%m") for i in range(n)]
