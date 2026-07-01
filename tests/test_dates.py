"""Характеризующие тесты для bot/utils/dates.py."""
from bot.utils.dates import (
    format_date_display, period_month_from_date, display_period,
    format_date_short_with_wd,
)


def test_format_date_display():
    assert format_date_display("2026-04-23") == "23.04.2026"


def test_period_month_from_date():
    assert period_month_from_date("2026-04-23") == "2026-04"


def test_display_period():
    assert display_period("2026-04") == "04.2026"


def test_format_date_short_with_wd():
    # 23 апреля 2026 — четверг.
    assert format_date_short_with_wd("2026-04-23") == "23 апр, чт"
    assert format_date_short_with_wd("2026-01-01") == "1 янв, чт"
