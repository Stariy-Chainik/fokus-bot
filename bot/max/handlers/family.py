"""MAX: «👥 Кто привязан» — кто видит ребёнка и заявка администратору отвязать лишнего.

Список и заявка — общие с кабинетом Telegram (`bot/services/parent_unlink.py`), экраны —
`bot/screens/parent_family.py`. Причина необязательна: «✍️ Указать причину» ждёт одно сообщение
(состояние `unlink_reason`; «Меню» и «Отмена» его сбрасывают).
"""
from __future__ import annotations
import logging

from maxapi import F
from maxapi.types import MessageCallback, MessageCreated

from config.settings import settings
from bot.screens import cb
from bot.screens.parent_family import (
    family_pick_screen, family_screen, unlink_other_confirm_screen, unlink_reason_prompt, unlink_sent_screen,
)
from bot.services import parent_unlink
from bot.services.parent_notifier import max_addr
from ..render import edit_screen, send_screen, alert
from ..states import MaxParentStates
from . import router
from ._common import require_parent

logger = logging.getLogger(__name__)

_FAIL = {"self": "Себя — кнопкой «↩️ Это не мой ребёнок» в меню",
         "not_found": "Этот родитель уже отвязан", "unavailable": "Не получилось отправить заявку, попробуйте позже"}


def _user_info(user) -> dict:
    """Имя просящего из MAX — у адресов MAX другого источника имени нет."""
    if user is None:
        return {}
    name = " ".join(x for x in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if x)
    return {"name": name, "username": getattr(user, "username", None) or ""}


async def _child(event, max_uid, student_repo, sid: str):
    students = await require_parent(event, student_repo, max_uid)
    if not students:
        return None, []
    student = next((s for s in students if s.student_id == sid), None)
    if student is None:
        await alert(event, "Ученик не найден")
    return student, students


async def _view(student, max_uid, tg_bot, client_repo, pending_repo, activity_repo) -> dict:
    return (await parent_unlink.family_view([student], max_addr(max_uid), tg_bot, client_repo, pending_repo,
                                            activity_repo, settings.bot_token))[0]


@router.message_callback(F.callback.payload == "client:family")
async def on_family(event: MessageCallback, context, max_uid, student_repo, tg_bot=None, client_repo=None,
                    pending_repo=None, activity_repo=None):
    await context.clear()
    students = await require_parent(event, student_repo, max_uid)
    if not students:
        return
    if len(students) > 1:
        await edit_screen(event, *family_pick_screen(students))
        return
    view = await _view(students[0], max_uid, tg_bot, client_repo, pending_repo, activity_repo)
    await edit_screen(event, *family_screen(view, many=False))


@router.message_callback(F.callback.payload.startswith("client_family:"))
async def on_family_child(event: MessageCallback, context, max_uid, student_repo, tg_bot=None, client_repo=None,
                          pending_repo=None, activity_repo=None):
    await context.clear()
    student, students = await _child(event, max_uid, student_repo, event.callback.payload.split(":", 1)[1])
    if student is None:
        return
    view = await _view(student, max_uid, tg_bot, client_repo, pending_repo, activity_repo)
    await edit_screen(event, *family_screen(view, many=len(students) > 1))


@router.message_callback(F.callback.payload.startswith("famunl:"))
async def on_unlink_other(event: MessageCallback, max_uid, student_repo, tg_bot=None, client_repo=None,
                          pending_repo=None, activity_repo=None):
    _, sid, key = event.callback.payload.split(":", 2)
    student, _ = await _child(event, max_uid, student_repo, sid)
    if student is None:
        return
    view = await _view(student, max_uid, tg_bot, client_repo, pending_repo, activity_repo)
    p = next((x for x in view["parents"] if x["key"] == key), None)
    if p is None or p["me"]:
        await alert(event, _FAIL["self" if p else "not_found"])
        return
    if p["pending"]:
        await alert(event, "Заявка уже у администратора")
        return
    await edit_screen(event, *unlink_other_confirm_screen(view, p))


@router.message_callback(F.callback.payload.startswith("famunl_why:"))
async def on_unlink_reason_ask(event: MessageCallback, context, max_uid, student_repo):
    _, sid, key = event.callback.payload.split(":", 2)
    student, _ = await _child(event, max_uid, student_repo, sid)
    if student is None:
        return
    await context.set_state(MaxParentStates.unlink_reason)
    await context.update_data(famunl_sid=sid, famunl_key=key)
    await edit_screen(event, *unlink_reason_prompt(sid))


async def _submit(student, max_uid, key: str, reason: str, user, tg_bot, user_repo, client_repo,
                  pending_repo, activity_repo) -> str:
    status, _action = await parent_unlink.submit_unlink(
        student, max_addr(max_uid), key, reason, tg_bot=tg_bot, user_repo=user_repo, client_repo=client_repo,
        pending_repo=pending_repo, activity_repo=activity_repo, secret=settings.bot_token,
        requester_info=_user_info(user))
    logger.info("MAX: %s просит отвязать родителя от %s — %s", max_uid, student.student_id, status)
    return status


@router.message_callback(F.callback.payload.startswith("famunl_do:"))
async def on_unlink_send(event: MessageCallback, context, max_uid, student_repo, user_repo, tg_bot=None,
                         client_repo=None, pending_repo=None, activity_repo=None):
    await context.clear()
    _, sid, key = event.callback.payload.split(":", 2)
    student, _ = await _child(event, max_uid, student_repo, sid)
    if student is None:
        return
    status = await _submit(student, max_uid, key, "", event.callback.user, tg_bot, user_repo, client_repo,
                           pending_repo, activity_repo)
    if status in ("ok", "already"):
        await edit_screen(event, *unlink_sent_screen(sid, already=status == "already"))
    else:
        await alert(event, _FAIL.get(status, _FAIL["unavailable"]))


@router.message_created(F.message.body.text, MaxParentStates.unlink_reason)
async def on_unlink_reason(event: MessageCreated, context, max_uid, student_repo, user_repo, tg_bot=None,
                           client_repo=None, pending_repo=None, activity_repo=None):
    data = await context.get_data()
    await context.clear()
    sid, key = data.get("famunl_sid", ""), data.get("famunl_key", "")
    student = next((s for s in await student_repo.get_by_parent_max_id(max_uid) if s.student_id == sid), None)
    if student is None or not key:
        await send_screen(event.bot, max_uid, "Ученик не найден. Откройте «👥 Кто привязан» в меню ещё раз.", [])
        return
    status = await _submit(student, max_uid, key, event.message.body.text or "", event.message.sender, tg_bot,
                           user_repo, client_repo, pending_repo, activity_repo)
    if status in ("ok", "already"):
        await send_screen(event.bot, max_uid, *unlink_sent_screen(sid, already=status == "already"))
    else:
        await send_screen(event.bot, max_uid, _FAIL.get(status, _FAIL["unavailable"]),
                          [[cb("« Кто привязан", f"client_family:{sid}")]])
