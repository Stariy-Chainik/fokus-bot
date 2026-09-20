"""Раздача фронта Mini App (папка miniapp/ в корне репозитория) по адресу /app/."""
from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)

MINIAPP_DIR = Path(__file__).resolve().parents[2] / "miniapp"
_NO_CACHE = {"Cache-Control": "no-store"}


def register_miniapp_static(app: web.Application, directory: Path = MINIAPP_DIR) -> None:
    if not directory.is_dir():
        logger.warning("Mini App: папка %s не найдена — фронт не раздаётся", directory)
        return

    async def index(_request: web.Request) -> web.StreamResponse:
        return web.FileResponse(directory / "index.html", headers=_NO_CACHE)

    async def asset(request: web.Request) -> web.StreamResponse:
        """Файлы кабинета — без кеша: после деплоя у всех сразу новая версия."""
        name = request.match_info["name"]
        path = (directory / name).resolve()
        if directory.resolve() not in path.parents or not path.is_file():
            raise web.HTTPNotFound
        return web.FileResponse(path, headers=_NO_CACHE)

    app.router.add_get("/app", index)
    app.router.add_get("/app/", index)
    app.router.add_get("/app/{name:[A-Za-z0-9_.-]+}", asset)
    logger.info("Mini App: фронт раздаётся из %s по /app/ (без кеша)", directory)
