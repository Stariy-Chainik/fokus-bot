"""Лента изменений для администратора: одна точка записи для бота, кабинета и MAX.

Всё, что меняет данные школы (оплаты, занятия, заявки, выплаты, ученики, группы,
педагоги), пишет строку в лист `activity_log` через `record()`. Тексты содержат
идентификаторы (STU-/GRP-/TCH-), имена подставляет API ленты при показе — так запись
не зависит от того, знает ли вызывающий код имена. Лента вспомогательная: если лист
недоступен или не настроен (тесты, скрипты), основной сценарий не прерывается.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

PAYMENT, LESSON, QUEUE, PAYOUT, FINANCE, STUDENT, GROUP, TEACHER = (
    "payment", "lesson", "queue", "payout", "finance", "student", "group", "teacher",
)

_repo = None


def setup(repo) -> None:
    """Подключить репозиторий ленты (в __main__); None — запись выключена."""
    global _repo
    _repo = repo


async def record(kind: str, text: str, actor: int | None = 0, ref: str = "") -> None:
    """Записать событие. Ошибка листа — только предупреждение в лог."""
    if _repo is None:
        return
    try:
        await _repo.add(kind, text, int(actor or 0), ref)
    except Exception as exc:                      # лента вспомогательная — сценарий важнее
        logger.warning("Лента изменений: не записали «%s»: %s", text[:60], exc)
