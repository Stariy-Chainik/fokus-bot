"""Видео к домашнему заданию (решение владельца 02.11.2026).

Спортсмен присылает ролик в чат бота Telegram: кнопка «📹 Отправить видео» у задания в кабинете просит бота
написать в чат, какое задание ждёт видео (`POST /api/athlete/tasks/{tid}/video`), и открывает чат. Файл хранит
Telegram: в задании (`athlete_tasks.video_file_ids`) остаётся только ссылка `kind:file_id`, сервер ролик не
скачивает и не хранит — видео любого размера не нагружает сервер и не идёт через слабую связь Россия ↔ Hetzner.
Педагог получает видео сразу в Telegram с кнопкой «📒 Открыть задание» (кабинет, `?open=hw&bid=`) и может
запросить его снова из кабинета (`POST /api/teacher/homework/tasks/{tid}/video/send`).

Ссылка: `video:<file_id>` — обычное видео, `note:<file_id>` — кружок, `doc:<file_id>` — ролик файлом.
"""
from __future__ import annotations

import logging
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from config.settings import settings

logger = logging.getLogger(__name__)

VIDEO, NOTE, DOC = "video", "note", "doc"


def is_video_document(document) -> bool:
    """Ролик, отправленный файлом (без сжатия) — mime video/*."""
    return bool(document is not None and (getattr(document, "mime_type", "") or "").startswith("video/"))


def video_ref(message) -> str | None:
    """Ссылка на видео из сообщения Telegram; None — в сообщении нет видео."""
    if getattr(message, "video", None) is not None:
        return f"{VIDEO}:{message.video.file_id}"
    if getattr(message, "video_note", None) is not None:
        return f"{NOTE}:{message.video_note.file_id}"
    document = getattr(message, "document", None)
    if document is not None and is_video_document(document):
        return f"{DOC}:{document.file_id}"
    return None


def split_ref(ref: str) -> tuple[str, str]:
    kind, sep, file_id = ref.partition(":")
    return (kind, file_id) if sep and kind in (VIDEO, NOTE, DOC) else (VIDEO, ref)


async def send_ref(bot, chat_id: int, ref: str, caption: str | None = None, reply_markup=None):
    """Переслать ролик по ссылке: Telegram отдаёт файл по file_id, качать не нужно."""
    kind, file_id = split_ref(ref)
    if kind == NOTE:        # у кружка подписи нет — текст отдельным сообщением
        msg = await bot.send_video_note(chat_id, file_id, reply_markup=reply_markup)
        if caption:
            await bot.send_message(chat_id, caption, reply_markup=reply_markup)
        return msg
    if kind == DOC:
        return await bot.send_document(chat_id, file_id, caption=caption, reply_markup=reply_markup)
    return await bot.send_video(chat_id, file_id, caption=caption, reply_markup=reply_markup)


async def bot_username(bot) -> str:
    """Имя бота для ссылки на чат (t.me/<bot>); пусто, если узнать не удалось (тесты, сбой сети)."""
    try:
        return (await bot.me()).username or ""
    except Exception:
        return ""


def open_task_markup(batch_id: str, student_id: str = "") -> InlineKeyboardMarkup | None:
    """Кнопка в кабинет педагога: выдача группе — «📒 Открыть задание» (`?open=hw&bid=`), задание из дневника
    спортсмена — «📓 Открыть дневник» (`?open=diary&sid=`, экран заданий ученика)."""
    if not settings.miniapp_url:
        return None
    if batch_id:
        text, query = "📒 Открыть задание", f"open=hw&bid={batch_id}"
    elif student_id:
        text, query = "📓 Открыть дневник", f"open=diary&sid={student_id}"
    else:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=text, web_app=WebAppInfo(url=f"{settings.miniapp_url}?{query}"))]])


def task_caption(student_name: str, title: str, exercise: str, index: int = 1, total: int = 1,
                 head: str = "📹 <b>Видео к домашнему заданию</b>") -> str:
    from bot.api.homework import short
    n = f"\nВидео {index} из {total}" if total > 1 else ""
    return f"{head}\n<b>{escape(student_name)}</b> · {title} «{escape(short(exercise))}»{n}"
