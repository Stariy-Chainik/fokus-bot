"""Раздача фронта Mini App (папка miniapp/ в корне репозитория) по адресу /app/."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)

# aiohttp берёт типы из своей таблицы, и .webp в ней нет (на проде логотип уходил как application/octet-stream)
_EXTRA_TYPES = {".webp": "image/webp"}

MINIAPP_DIR = Path(__file__).resolve().parents[2] / "miniapp"
_NO_CACHE = {"Cache-Control": "no-store"}
# Шрифты и скрипт Telegram (папки fonts/, vendor/) не меняются от деплоя к деплою — пусть телефон держит их у себя:
# не качать 180 КБ на каждое открытие кабинета (14.10.2026)
_LONG_CACHE = {"Cache-Control": "public, max-age=604800"}
_CACHED_DIRS = ("fonts", "vendor")
_VERSION_EXTS = frozenset({".html", ".js"})


def miniapp_version(directory: Path) -> str:
    """Срез версий всех HTML/JS файлов кабинета (mtime_ns + size): меняется при правке любого файла,
    а не только app.js — открытый в Telegram кабинет перегрузится после деплоя teacher.js/parent.js/index.html."""
    h = hashlib.sha256()
    for p in sorted(directory.iterdir()):
        if p.is_file() and p.suffix in _VERSION_EXTS:
            st = p.stat()
            h.update(f"{p.name}:{st.st_mtime_ns}:{st.st_size};".encode())
    return h.hexdigest()[:16]


def register_miniapp_static(app: web.Application, directory: Path = MINIAPP_DIR) -> None:
    if not directory.is_dir():
        logger.warning("Mini App: папка %s не найдена — фронт не раздаётся", directory)
        return

    async def index(_request: web.Request) -> web.StreamResponse:
        return web.FileResponse(directory / "index.html", headers=_NO_CACHE)

    async def version(_request: web.Request) -> web.Response:
        return web.json_response({"version": miniapp_version(directory)}, headers=_NO_CACHE)

    async def asset(request: web.Request) -> web.StreamResponse:
        """Файлы кабинета — без кеша: после деплоя у всех сразу новая версия."""
        name = request.match_info["name"]
        path = (directory / name).resolve()
        if directory.resolve() not in path.parents or not path.is_file():
            raise web.HTTPNotFound
        headers = dict(_LONG_CACHE if name.split("/", 1)[0] in _CACHED_DIRS else _NO_CACHE)
        if path.suffix in _EXTRA_TYPES:
            headers["Content-Type"] = _EXTRA_TYPES[path.suffix]
        return web.FileResponse(path, headers=headers)

    app.router.add_get("/app", index)
    app.router.add_get("/app/", index)
    app.router.add_get("/app/version", version)
    app.router.add_get("/app/{name:[A-Za-z0-9_.-]+}", asset)
    app.router.add_get("/app/{name:(?:fonts|vendor)/[A-Za-z0-9_.-]+}", asset)     # шрифт и скрипт Telegram — свои копии
    logger.info("Mini App: фронт раздаётся из %s по /app/ (без кеша)", directory)
