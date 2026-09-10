"""MAX-фронт: адаптер клавиатур, разбиение текста, покрытие callback хендлерами."""
import re
from pathlib import Path

import pytest

maxapi = pytest.importorskip("maxapi")

from bot.screens import cb, url  # noqa: E402
from bot.max.render import to_max_markup, split_text  # noqa: E402

MAX_DIR = Path(__file__).resolve().parent.parent / "bot" / "max" / "handlers"
RE_EQ = re.compile(r'F\.callback\.payload\s*==\s*"([^"]+)"')
RE_SW = re.compile(r'F\.callback\.payload\.startswith\("([^"]+)"\)')

# Всё, что кнопки экранов родителя в MAX (счета, оплата, вход) могут отправить назад.
EXPECTED = [
    "client:my_bills", "cl_bills_stu:", "cl_bills_more:", "client_bill:", "client_pay:",
    "pselt:", "pselgo", "pay_method:", "cash_notify:", "receipt_upload:",
    "go:home", "client:add_child", "client_reg:", "client_reg_retry",
    "client_add_req:", "client_add_retry", "glink:", "glink_none:",
]


def test_to_max_markup_buttons():
    m = to_max_markup([[cb("Счёт", "client_bill:STU-1:2026-09"), url("Оплатить", "https://pay")]])
    row = m.payload.buttons[0]
    assert row[0].payload == "client_bill:STU-1:2026-09" and row[0].text == "Счёт"
    assert row[1].url == "https://pay"
    assert to_max_markup([]) is None


def test_split_text_keeps_lines():
    text = "\n".join(f"строка {i}" for i in range(1000))
    parts = split_text(text, limit=500)
    assert all(len(p) <= 500 for p in parts) and "\n".join(parts) == text


def test_every_parent_callback_has_max_handler():
    eq, sw = set(), set()
    for path in MAX_DIR.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        eq.update(RE_EQ.findall(src))
        sw.update(RE_SW.findall(src))
    missing = [p for p in EXPECTED if p not in eq and not any(p.startswith(s) for s in sw)]
    assert not missing, f"Нет MAX-хендлеров для: {missing}"


def test_build_max_registers_handlers():
    from bot.max.app import build
    bot, dp = build("1:fake-token", {"student_repo": object()}, tg_bot=object())
    assert bot is not None and dp is not None
