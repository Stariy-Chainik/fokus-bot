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


def test_page_does_not_block_on_foreign_hosts() -> None:
    """Кабинет в MAX открывался только с VPN: telegram.org и fonts.googleapis.com без VPN в России не отвечают,
    а внешний скрипт/стиль в <head> браузер ждёт до таймаута (14.10.2026). Позже так же — st.max.ru."""
    html = (MINIAPP / "index.html").read_text(encoding="utf-8")
    foreign = set(re.findall(r'(?:src|href)=\\?"(https?://[^"\\]+)', html))
    assert not foreign, foreign                 # ни одного внешнего файла: мост MAX тоже свой (vendor/)
    assert (MINIAPP / "vendor" / "max-web-app.js").is_file()
    assert (MINIAPP / "vendor" / "telegram-web-app.js").is_file() and (MINIAPP / "fonts" / "manrope.css").is_file()
    css = (MINIAPP / "fonts" / "manrope.css").read_text(encoding="utf-8")
    assert "http" not in css.split("*/", 1)[1] and all((MINIAPP / "fonts" / f).is_file()
                                                        for f in re.findall(r"url\(([^)]+)\)", css))


def test_static_serves_fonts_and_vendor_with_cache() -> None:
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from bot.api.static import register_miniapp_static

    async def run():
        app = web.Application()
        register_miniapp_static(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            font = await client.get("/app/fonts/manrope-cyrillic.woff2")
            tg = await client.get("/app/vendor/telegram-web-app.js")
            page = await client.get("/app/app.js")
            bad = await client.get("/app/fonts/..%2Fapp.js")
            return (font.status, font.headers.get("Cache-Control"), tg.status, page.headers.get("Cache-Control"),
                    bad.status)
        finally:
            await client.close()
    fs, fcache, ts, pcache, bad = asyncio.run(run())
    assert (fs, ts, bad) == (200, 200, 404) and "max-age" in fcache and pcache == "no-store"


def test_webp_served_as_image(tmp_path):
    """Анимированный логотип .webp — image/webp, а не application/octet-stream (на проде у python типа нет)."""
    import asyncio

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from bot.api.static import register_miniapp_static

    (tmp_path / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (tmp_path / "logo-loop.webp").write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")

    async def run():
        app = web.Application()
        register_miniapp_static(app, tmp_path)
        async with TestClient(TestServer(app)) as c:
            r = await c.get("/app/logo-loop.webp")
            return r.status, r.headers.get("Content-Type")
    assert asyncio.run(run()) == (200, "image/webp")
