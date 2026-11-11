"""MAX: «💬 Написать руководителю» — пожелание, замечание, благодарность или вопрос руководителю студии
(решение владельца 11.11.2026). Текст — одно сообщение в состоянии `feedback_text`; сообщение уходит
руководителю в Telegram (bot/services/parent_feedback.py), ответ приходит сюда через notifier.
"""
from __future__ import annotations
import logging

from maxapi import F
from maxapi.types import MessageCallback, MessageCreated

from bot.screens.parent_feedback import (
    feedback_kind_screen, feedback_pick_screen, feedback_prompt_screen, feedback_rate_screen, feedback_sent_screen,
)
from bot.services import parent_feedback
from bot.services.parent_notifier import max_addr
from ..render import alert, edit_screen, send_screen
from ..states import MaxParentStates
from . import router
from ._common import DEPS, require_parent

logger = logging.getLogger(__name__)


def _sender_name(event) -> str:
    user = getattr(getattr(event, "message", None), "sender", None)
    return " ".join(x for x in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if x)


@router.message_callback(F.callback.payload == "client:feedback")
async def on_feedback(event: MessageCallback, context, max_uid, student_repo):
    await context.clear()
    students = await require_parent(event, student_repo, max_uid)
    if not students:
        return
    if len(students) == 1:
        await edit_screen(event, *feedback_kind_screen(students[0]))
    else:
        await edit_screen(event, *feedback_pick_screen(students))


@router.message_callback(F.callback.payload.startswith("client_fb:"))
async def on_feedback_pick(event: MessageCallback, context, max_uid, student_repo):
    students = await require_parent(event, student_repo, max_uid)
    if not students:
        return
    sid = event.callback.payload.split(":", 1)[1]
    kid = next((s for s in students if s.student_id == sid), None)
    if kid is None:
        await alert(event, "Ребёнок не найден")
        return
    await edit_screen(event, *feedback_kind_screen(kid))


@router.message_callback(F.callback.payload.startswith("client_fbk:"))
async def on_feedback_kind(event: MessageCallback, context, max_uid, student_repo):
    students = await require_parent(event, student_repo, max_uid)
    if not students:
        return
    _, sid, kind = event.callback.payload.split(":", 2)
    kid = next((s for s in students if s.student_id == sid), None)
    if kid is None or kind not in parent_feedback.KINDS:
        await alert(event, "Не удалось начать обращение — откройте меню ещё раз")
        return
    await context.set_state(MaxParentStates.feedback_text)
    await context.update_data(fb_sid=sid, fb_kind=kind, fb_name=_sender_name(event))
    await edit_screen(event, *feedback_prompt_screen(kid, kind))


@router.message_created(F.message.body.text, MaxParentStates.feedback_text)
async def on_feedback_text(event: MessageCreated, context, max_uid, student_repo, tg_bot=None, user_repo=None,
                           student_group_repo=None, group_repo=None, client_repo=None, feedback_repo=None):
    if (event.message.body.text or "").strip().startswith("/"):        # команда — выход из ввода
        await context.clear()
        return
    data = await context.get_data()
    students = await student_repo.get_by_parent_max_id(max_uid)
    kid = next((s for s in students if s.student_id == data.get("fb_sid")), None)
    if kid is None or feedback_repo is None:
        await context.clear()
        await send_screen(event.bot, max_uid, "Не удалось отправить. Откройте «💬 Написать руководителю» в меню ещё раз.", [])
        return
    try:
        await parent_feedback.submit(
            feedback_repo=feedback_repo, user_repo=user_repo or DEPS.get("user_repo"), student_repo=student_repo,
            student_group_repo=student_group_repo, group_repo=group_repo, client_repo=client_repo, bot=tg_bot,
            addr=max_addr(max_uid), student=kid, kind=data.get("fb_kind", ""), text=event.message.body.text or "",
            parent_name=data.get("fb_name") or _sender_name(event),
        )
    except parent_feedback.RateLimited:
        await context.clear()
        await send_screen(event.bot, max_uid, *feedback_rate_screen())
        return
    except ValueError:
        await send_screen(event.bot, max_uid, "Напишите текст обращения одним сообщением.", [])
        return
    await context.clear()
    logger.info("MAX: обращение родителя %s о %s", max_uid, kid.student_id)
    await send_screen(event.bot, max_uid, *feedback_sent_screen())
