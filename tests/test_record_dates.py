"""Кнопка «Вчера» не сдвигает календарную дату из-за часового пояса телефона."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("timezone", [
    "Europe/Moscow", "UTC", "America/New_York", "America/Los_Angeles",
    "Europe/Berlin", "Asia/Vladivostok", "Pacific/Auckland",
])
def test_record_yesterday_is_previous_calendar_day_in_every_timezone(timezone):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки JavaScript нужен Node.js")
    source = (Path(__file__).resolve().parents[1] / "miniapp" / "app.js").read_text()
    start = source.index("function yesterdayOf(")
    end = source.index("Object.assign(ACT", start)
    cases = {
        "2026-11-18": "2026-11-17",  # репро владельца: в Москве получалось 16-е
        "2026-11-01": "2026-10-31",
        "2027-01-01": "2026-12-31",
        "2024-03-01": "2024-02-29",
        "2025-03-01": "2025-02-28",
        "2026-03-09": "2026-03-08",  # переход на летнее время в США
        "2026-03-30": "2026-03-29",  # переход в Европе
        "2026-10-05": "2026-10-04",  # переход в Новой Зеландии
    }
    script = source[start:end] + "\nconsole.log(JSON.stringify(Object.fromEntries("
    script += json.dumps(list(cases)) + ".map(day => [day, yesterdayOf(day)]))));"
    result = subprocess.run([node, "-e", script], env={**os.environ, "TZ": timezone},
                            check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == cases
