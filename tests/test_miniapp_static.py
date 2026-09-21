"""Проверки фронта Mini App без браузера: файл целиком выполняется, а не падает при загрузке.

`ACT` и `SCREENS` объявлены через `const` в app.js: любое присваивание `ACT.x = …`
выше объявления роняет весь скрипт (ReferenceError), кабинет не открывается ни у кого.
Один раз так и случилось — поэтому порядок проверяется тестом.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

MINIAPP = Path(__file__).resolve().parents[1] / "miniapp"
APP = MINIAPP / "app.js"
ROLE_FILES = ["teacher.js", "parent.js", "athlete.js"]


def _lines(text: str) -> list[str]:
    return text.splitlines()


@pytest.mark.parametrize("name", ["ACT", "SCREENS"])
def test_declaration_comes_before_use(name: str) -> None:
    lines = _lines(APP.read_text(encoding="utf-8"))
    decl = next(i for i, ln in enumerate(lines) if re.match(rf"const {name}\b", ln))
    used = [i + 1 for i, ln in enumerate(lines[:decl]) if re.match(rf"{name}[.\[]", ln)]
    assert not used, f"{name} используется до объявления в app.js, строки: {used}"


def test_role_files_are_loaded_after_app() -> None:
    """Экраны ролей пишут в ACT/SCREENS из app.js — он должен подключаться первым."""
    html = (MINIAPP / "index.html").read_text(encoding="utf-8")
    order = [m.group(1) for m in re.finditer(r'<script src="([a-z]+\.js)"></script>', html)]
    assert order[0] == "app.js" and set(ROLE_FILES) <= set(order), order


def test_screens_have_their_actions() -> None:
    """Каждая кнопка data-act имеет обработчик: опечатку в имени иначе видно только в бою."""
    sources = [APP.read_text(encoding="utf-8")] + [(MINIAPP / f).read_text(encoding="utf-8") for f in ROLE_FILES]
    joined = "\n".join(sources)
    declared = set(re.findall(r"ACT\.([A-Za-z0-9_]+)\s*=", joined))
    declared |= set(re.findall(r"^\s{2}([A-Za-z0-9_]+):\s*(?:async\s*)?\(", joined, re.M))
    used = set(re.findall(r"data-act=\"([A-Za-z0-9_]+)\"", joined))
    used |= set(re.findall(r"data-act=\\?\"?\$\{?([A-Za-z0-9_]+)", joined)) - {"act", "a"}
    missing = {a for a in used if a not in declared and not a.startswith("$")}
    assert not missing, f"нет обработчиков: {sorted(missing)}"
