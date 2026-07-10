"""Страховка от «оборванных кнопок»: каждый литеральный callback_data
из клавиатур должен матчиться каким-то хендлером (== / startswith / in_).

Статический анализ исходников bot/ — ловит опечатки и потери при разбиении
файлов. Динамические callback (собранные из переменных) не проверяются.
"""
import re
from pathlib import Path

BOT = Path(__file__).resolve().parent.parent / "bot"

# Паттерны регистраций хендлеров
RE_EQ = re.compile(r'F\.data\s*==\s*"([^"]+)"')
RE_SW = re.compile(r'F\.data\.startswith\("([^"]+)"\)')
RE_IN = re.compile(r'F\.data\.in_\(\{([^}]+)\}\)')

# Литеральные callback_data кнопок: "..." или f"...{...}"
RE_BTN = re.compile(r'callback_data=(?:f?)"([^"{]+)')

# Динамика/спецслучаи, которые матчить не нужно:
SKIP_BUTTONS = {
    "noop",  # есть хендлер, но и без него безвреден
}


def _collect():
    eq, sw, buttons = set(), set(), set()
    for path in BOT.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        eq.update(RE_EQ.findall(src))
        sw.update(RE_SW.findall(src))
        for group in RE_IN.findall(src):
            eq.update(re.findall(r'"([^"]+)"', group))
        for prefix in RE_BTN.findall(src):
            buttons.add((prefix, str(path.relative_to(BOT))))
    return eq, sw, buttons


def test_every_button_has_handler():
    eq, sw, buttons = _collect()
    unmatched = []
    for prefix, where in sorted(buttons):
        if prefix in SKIP_BUTTONS:
            continue
        ok = (
            prefix in eq
            or any(prefix.startswith(p) for p in sw)
            # кнопка без параметров может быть префиксом полного литерала ==
            or any(e.startswith(prefix) for e in eq)
        )
        if not ok:
            unmatched.append(f"{prefix!r}  ({where})")
    assert not unmatched, "Кнопки без хендлера:\n  " + "\n  ".join(unmatched)
