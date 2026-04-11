from datetime import datetime, date


DATE_FMT = "%Y-%m-%d"
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"
DISPLAY_FMT = "%d.%m.%Y"


def now_str() -> str:
    """Текущее время для хранения в таблице: YYYY-MM-DD HH:MM:SS"""
    return datetime.now().strftime(DATETIME_FMT)


def today_str() -> str:
    """Сегодняшняя дата для хранения в таблице: YYYY-MM-DD"""
    return date.today().strftime(DATE_FMT)


def parse_date(value: str) -> date:
    """Парсит строку YYYY-MM-DD в объект date."""
    return datetime.strptime(value, DATE_FMT).date()


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
