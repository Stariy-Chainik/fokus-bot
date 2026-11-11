"""«💬 Написать руководителю» (решение владельца 11.11.2026): пожелание, замечание, благодарность или вопрос
от родителя уходит **только руководителю студии** в Telegram — педагоги и другие администраторы его не видят,
в ленту событий оно не попадает. Под сообщением кнопка «↩️ Ответить»: руководитель пишет ответ в бот одним
сообщением, он уходит родителю туда, откуда тот писал (Telegram или MAX). Журнал — лист `parent_feedback`.

Получатели: `FEEDBACK_TG_IDS`; пусто — Telegram руководителя (педагог из `OWNER_TEACHER_IDS` в листе users),
если и его нет — все администраторы. Не чаще одного обращения в `RATE_SEC` от одного родителя.
"""
from __future__ import annotations

import logging
import time

from bot.repositories.parent_feedback_repo import ParentFeedback
from config.settings import settings

from .parent_notifier import fmt_addr, parse_addr, resolve_notifier

logger = logging.getLogger(__name__)

KINDS = {"wish": "Пожелание", "remark": "Замечание", "thanks": "Благодарность", "question": "Вопрос"}
KIND_ICONS = {"wish": "💡", "remark": "⚠️", "thanks": "🙏", "question": "❓"}
MAX_LEN = 2000
RATE_SEC = 600
_last_sent: dict[str, float] = {}          # адрес родителя → monotonic последнего обращения


class RateLimited(Exception):
    """Родитель писал меньше RATE_SEC назад."""


def kind_label(kind: str) -> str:
    return f"{KIND_ICONS.get(kind, '💬')} {KINDS.get(kind, 'Сообщение')}"


def clean_text(raw) -> str:
    """Текст обращения: пробелы по краям, не длиннее MAX_LEN; пусто — «»."""
    text = "\n".join(line.rstrip() for line in str(raw or "").strip().splitlines()).strip()
    return text[:MAX_LEN]


def kinds_view() -> list[dict]:
    return [{"id": k, "label": v, "icon": KIND_ICONS[k]} for k, v in KINDS.items()]


async def recipients(user_repo) -> list[int]:
    """tg_id получателей: FEEDBACK_TG_IDS → руководитель (OWNER_TEACHER_IDS) → администраторы."""
    if settings.feedback_tg_id_set:
        return sorted(settings.feedback_tg_id_set)
    users = await user_repo.get_all()
    owners = [u.tg_id for u in users if u.teacher_id and u.teacher_id in settings.owner_teacher_id_set]
    if owners:
        return owners
    return [u.tg_id for u in users if u.is_admin]


async def _telegram_name(bot, tg_id: int) -> str:
    try:
        chat = await bot.get_chat(tg_id)
        name = " ".join(x for x in (getattr(chat, "first_name", None), getattr(chat, "last_name", None)) if x)
        username = getattr(chat, "username", None)
        return f"{name} (@{username})" if name and username else name or (f"@{username}" if username else "")
    except Exception:
        return ""


def director_text(row, student, group_names: list[str]) -> str:
    platform = "MAX" if row.parent_addr.startswith("m") else "Telegram"
    who = row.parent_name or f"родитель в {platform}"
    child = f"{student.name}" if student is not None else row.student_id
    groups = f" ({', '.join(group_names)})" if group_names else ""
    return (f"{kind_label(row.kind)} от родителя\n"
            f"{who} · {platform}\nРебёнок: {child}{groups}\n\n{row.text}")


def reply_rows(feedback_id: str) -> list:
    from bot.screens.types import cb
    return [[cb("↩️ Ответить", f"fbreply:{feedback_id}")]]


async def submit(*, feedback_repo, user_repo, student_repo, student_group_repo, group_repo, client_repo, bot,
                 addr, student, kind: str, text: str, parent_name: str = "") -> ParentFeedback:
    """Записать обращение и отправить руководителю. RateLimited — писал меньше RATE_SEC назад;
    ValueError — не тот вид или пустой текст."""
    if kind not in KINDS:
        raise ValueError("bad_kind")
    text = clean_text(text)
    if not text:
        raise ValueError("empty")
    key = fmt_addr(addr)
    now = time.monotonic()
    if now - _last_sent.get(key, -RATE_SEC) < RATE_SEC:
        raise RateLimited()
    if not parent_name:
        client = await client_repo.get_by_id(student.client_id) if client_repo is not None and student.client_id else None
        parent_name = client.name if client is not None else ""
        if not parent_name and addr[0] == "tg" and bot is not None:
            parent_name = await _telegram_name(bot, addr[1])
    row = await feedback_repo.add(student.student_id, key, parent_name, kind, text)
    _last_sent[key] = now
    if bot is not None:
        groups = {g.group_id: g.name for g in await group_repo.get_all(include_archived=True)} if group_repo else {}
        gids = await student_group_repo.get_groups_for_student(student.student_id) if student_group_repo else []
        names = [groups.get(g, g) for g in gids]
        from bot.screens.adapters import to_aiogram_markup
        message = director_text(row, student, names)
        kb = to_aiogram_markup(reply_rows(row.feedback_id))
        for tg_id in await recipients(user_repo):
            try:
                await bot.send_message(tg_id, message, reply_markup=kb)
            except Exception as exc:                    # обращение записано — доставка вспомогательная
                logger.warning("Обращение %s руководителю %s не ушло: %s", row.feedback_id, tg_id, exc)
    return row


async def reply(*, feedback_repo, feedback_id: str, text: str, actor: int, tg_bot=None) -> tuple:
    """Ответ руководителя: статус «отвечено», родителю — сообщение туда, откуда он писал.
    Возвращает (строка | None, доставлено)."""
    text = clean_text(text)
    row = await feedback_repo.get_by_id(feedback_id)
    if row is None or not text:
        return None, False
    if not await feedback_repo.set_reply(feedback_id, text, actor):
        return None, False
    addr = parse_addr(row.parent_addr)
    delivered = await resolve_notifier(tg_bot).send(
        addr, f"💬 Ответ руководителя студии на ваше обращение «{row.text[:60]}{'…' if len(row.text) > 60 else ''}»:"
              f"\n\n{text}")
    logger.info("Ответ на обращение %s от %s: доставлено=%s", feedback_id, actor, delivered)
    return row, delivered


def view(rows: list) -> list[dict]:
    """Обращения для кабинета родителя: вид, текст, ответ."""
    return [{"id": r.feedback_id, "ts": r.ts, "studentId": r.student_id, "kind": r.kind,
             "kindLabel": KINDS.get(r.kind, "Сообщение"), "icon": KIND_ICONS.get(r.kind, "💬"),
             "text": r.text, "answered": r.answered, "reply": r.reply, "repliedAt": r.replied_at}
            for r in rows]
