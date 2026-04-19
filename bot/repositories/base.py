from __future__ import annotations
"""
Базовый репозиторий.

Ключевые решения для продакшена:
- Все public-методы async: gspread-вызовы уходят в asyncio.to_thread(),
  event loop не блокируется.
- TTL-кеш (30 сек) на get_all_records: снижает нагрузку на Sheets API
  (лимит 60 req/min). Инвалидируется при любой записи.
- Retry с backoff для HTTP 429 / 503: временные сбои API не долетают до пользователя.
"""
import asyncio
import logging
import time
from typing import Any

import gspread
import requests.exceptions
from urllib3.exceptions import ProtocolError

from .sheets_client import SheetsClient

logger = logging.getLogger(__name__)

_CACHE_TTL: float = 300.0     # секунды кеширования чтений (инвалидируется при любой записи)
_RETRY_ATTEMPTS: int = 3
_RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 503})
_RETRY_NETWORK_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    ProtocolError,
)


def _with_retry(func, *args, **kwargs):
    """
    Синхронный retry для gspread-вызовов внутри потока.
    При 429/503 и сетевых разрывах делает экспоненциальный backoff: 5с → 10с → 20с.
    """
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return func(*args, **kwargs)
        except gspread.exceptions.APIError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", 0)
            if status in _RETRY_STATUSES and attempt < _RETRY_ATTEMPTS - 1:
                wait = 5 * (2 ** attempt)
                logger.warning(
                    "Sheets API error %s, retry %d/%d in %ds",
                    status, attempt + 1, _RETRY_ATTEMPTS, wait,
                )
                time.sleep(wait)
            else:
                raise
        except _RETRY_NETWORK_ERRORS as exc:
            if attempt < _RETRY_ATTEMPTS - 1:
                wait = 5 * (2 ** attempt)
                logger.warning(
                    "Network error, retry %d/%d in %ds: %s",
                    attempt + 1, _RETRY_ATTEMPTS, wait, exc,
                )
                time.sleep(wait)
            else:
                raise


class BaseRepository:
    # Кеш разделяется между всеми инстансами: sheet_name → (records, timestamp)
    _cache: dict[str, tuple[list, float]] = {}

    def __init__(self, client: SheetsClient, sheet_name: str) -> None:
        self._client = client
        self._sheet_name = sheet_name

    @property
    def _ws(self) -> gspread.Worksheet:
        return self._client.get_worksheet(self._sheet_name)

    # ── Синхронные хелперы (выполняются внутри потока) ────────────────────────

    def _sync_all_records(self) -> list[dict[str, Any]]:
        t0 = time.monotonic()
        try:
            result = _with_retry(self._ws.get_all_records, default_blank=None)
            logger.info("SHEETS read %s: %d rows in %.0f ms",
                        self._sheet_name, len(result), (time.monotonic() - t0) * 1000)
            return result
        except Exception as exc:
            logger.error("Ошибка чтения листа %s: %s", self._sheet_name, exc)
            raise

    def _sync_append_row(self, values: list[Any]) -> None:
        t0 = time.monotonic()
        try:
            _with_retry(self._ws.append_row, values, value_input_option="USER_ENTERED")
            logger.info("SHEETS append %s in %.0f ms",
                        self._sheet_name, (time.monotonic() - t0) * 1000)
        except Exception as exc:
            logger.error("Ошибка записи в лист %s: %s", self._sheet_name, exc)
            raise

    def _sync_update_row(self, row_index: int, values: list[Any]) -> None:
        t0 = time.monotonic()
        try:
            _with_retry(self._ws.update, f"A{row_index}", [values])
            logger.info("SHEETS update_row %s row=%d in %.0f ms",
                        self._sheet_name, row_index, (time.monotonic() - t0) * 1000)
        except Exception as exc:
            logger.error("Ошибка обновления строки %d в листе %s: %s", row_index, self._sheet_name, exc)
            raise

    def _sync_delete_row(self, row_index: int) -> None:
        t0 = time.monotonic()
        try:
            _with_retry(self._ws.delete_rows, row_index)
            logger.info("SHEETS delete_row %s row=%d in %.0f ms",
                        self._sheet_name, row_index, (time.monotonic() - t0) * 1000)
        except Exception as exc:
            logger.error("Ошибка удаления строки %d в листе %s: %s", row_index, self._sheet_name, exc)
            raise

    def _sync_update_cell(self, row_index: int, col: int, value: Any) -> None:
        t0 = time.monotonic()
        try:
            _with_retry(self._ws.update_cell, row_index, col, value)
            logger.info("SHEETS update_cell %s (%d,%d) in %.0f ms",
                        self._sheet_name, row_index, col, (time.monotonic() - t0) * 1000)
        except Exception as exc:
            logger.error(
                "Ошибка обновления ячейки (%d,%d) в листе %s: %s",
                row_index, col, self._sheet_name, exc,
            )
            raise

    # ── Инвалидация кеша ──────────────────────────────────────────────────────

    def _invalidate_cache(self) -> None:
        BaseRepository._cache.pop(self._sheet_name, None)

    # ── Async public helpers (вызываются из async-методов репозиториев) ───────

    async def _all_records(self) -> list[dict[str, Any]]:
        """Читает все записи листа с TTL-кешированием."""
        now = time.monotonic()
        cached = BaseRepository._cache.get(self._sheet_name)
        if cached and (now - cached[1]) < _CACHE_TTL:
            return cached[0]
        records: list[dict[str, Any]] = await asyncio.to_thread(self._sync_all_records)
        BaseRepository._cache[self._sheet_name] = (records, now)
        return records

    async def _append_row(self, values: list[Any]) -> None:
        await asyncio.to_thread(self._sync_append_row, values)
        self._invalidate_cache()

    async def _find_row_index(self, col_header: str, value: str) -> int | None:
        """
        Ищет строку по значению в колонке col_header.
        Использует кешированные данные — без лишнего API-вызова.
        Возвращает 1-based row index (с учётом заголовка) или None.
        """
        records = await self._all_records()
        for i, row in enumerate(records):
            if str(row.get(col_header) or "").strip() == str(value):
                return i + 2  # строка 1 = заголовок, данные с 2
        return None

    async def _update_row(self, row_index: int, values: list[Any]) -> None:
        await asyncio.to_thread(self._sync_update_row, row_index, values)
        self._invalidate_cache()

    async def _delete_row(self, row_index: int) -> None:
        await asyncio.to_thread(self._sync_delete_row, row_index)
        self._invalidate_cache()

    async def _update_cell(self, row_index: int, col: int, value: Any) -> None:
        await asyncio.to_thread(self._sync_update_cell, row_index, col, value)
        self._invalidate_cache()
