"""Состояние фоновых задач процесса для `/health/status` и автоалерты в Telegram."""
from __future__ import annotations

import asyncio
import html
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

TASK_LABELS: dict[str, str] = {
    "cache_warmer": "Обновление кэша Google Sheets",
    "rate_history": "История ставок педагогов",
    "lesson_reminders": "Напоминания о занятиях",
    "competition_results": "Загрузка турниров ФТСАРР",
}

_STATE: dict[str, dict[str, object]] = {}
_DEPS: dict[str, Any] = {}
_CACHED_ADMINS: list[int] = []
_BG_TASKS: set[asyncio.Task[Any]] = set()
_THRESHOLD: int = 3
_COOLDOWN_SEC: float = 3600.0


def setup(
    tg_bot: Any,
    user_repo: Any,
    *,
    threshold: int = 3,
    cooldown_sec: float = 3600.0,
) -> None:
    """Подключить доставку алертов админам при повторяющихся сбоях фоновых задач."""
    global _THRESHOLD, _COOLDOWN_SEC
    _DEPS["bot"] = tg_bot
    _DEPS["users"] = user_repo
    _THRESHOLD = max(1, threshold)
    _COOLDOWN_SEC = max(60.0, cooldown_sec)
    # Прогреваем список админов заранее, чтобы алерт ушёл даже если упадёт сам Google Sheets
    _schedule(_refresh_admins())


def _schedule(coro: Any) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    task = loop.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


async def _refresh_admins() -> list[int]:
    global _CACHED_ADMINS
    user_repo = _DEPS.get("users")
    if user_repo is None:
        return list(_CACHED_ADMINS)
    try:
        admins = [int(u.tg_id) for u in await user_repo.get_admins() if getattr(u, "tg_id", None)]
        if admins:
            _CACHED_ADMINS = admins
    except Exception:
        logger.debug("bg_health: не удалось обновить список админов, используем кэш (%d)", len(_CACHED_ADMINS))
    return list(_CACHED_ADMINS)


async def _notify_admins(text: str) -> None:
    bot = _DEPS.get("bot")
    if bot is None:
        return
    admins = await _refresh_admins()
    if not admins:
        return
    for tg_id in admins:
        try:
            await bot.send_message(tg_id, text)
        except Exception:
            logger.warning("bg_health: не удалось отправить алерт админу %s", tg_id, exc_info=True)


def _label(name: str) -> str:
    return TASK_LABELS.get(name, name)


def mark_ok(name: str) -> None:
    st = _STATE.setdefault(name, {})
    was_alerted = bool(st.get("alerted"))
    prev_errors = int(st.get("consecutiveErrors") or 0)  # type: ignore[call-overload]
    st["lastOkAt"] = round(time.time(), 1)
    st["consecutiveErrors"] = 0
    if was_alerted:
        st["alerted"] = False
        _schedule(
            _notify_admins(
                f"✅ Фоновая задача восстановилась: <b>{html.escape(_label(name))}</b>"
                f" (после {prev_errors} ошибок)"
            )
        )
    elif not _CACHED_ADMINS and _DEPS.get("users") is not None:
        _schedule(_refresh_admins())


def mark_err(name: str, exc: BaseException) -> None:
    now = time.time()
    st = _STATE.setdefault(name, {})
    err_str = f"{type(exc).__name__}: {exc}"[:200]
    st["lastError"] = err_str
    st["lastErrorAt"] = round(now, 1)
    errors = int(st.get("consecutiveErrors") or 0) + 1  # type: ignore[call-overload]
    st["consecutiveErrors"] = errors

    if _DEPS.get("bot") is None or errors < _THRESHOLD:
        return
    last_alert = float(st.get("lastAlertAt") or 0.0)  # type: ignore[arg-type]
    if not st.get("alerted") or (now - last_alert) >= _COOLDOWN_SEC:
        st["alerted"] = True
        st["lastAlertAt"] = round(now, 1)
        _schedule(
            _notify_admins(
                f"⚠️ Сбой фоновой задачи: <b>{html.escape(_label(name))}</b>"
                f" ({errors} ошибок подряд)\n<code>{html.escape(err_str)}</code>"
            )
        )


def snapshot() -> dict[str, dict[str, object]]:
    return {k: dict(v) for k, v in _STATE.items()}


def clear() -> None:
    _STATE.clear()
    _DEPS.clear()
    _CACHED_ADMINS.clear()
    _BG_TASKS.clear()
