"""MAX: «🏫 Группа в саду» — родитель указывает группу ребёнка в детском саду (решение владельца 10.10.2026).

Кнопка в меню только у родителей детей садовых групп школы (`_common.kindergarten_children`).
Значение — одно сообщение в состоянии `kgroup_value`; «Меню» сбрасывает состояние.
"""
from __future__ import annotations
import logging

from maxapi import F
from maxapi.types import MessageCallback, MessageCreated

from bot.screens.parent_kindergarten import kgroup_pick_screen, kgroup_prompt_screen, kgroup_saved_screen
from bot.services.kindergarten import clean_value
from ..render import edit_screen, send_screen, alert
from ..states import MaxParentStates
from . import router
from ._common import kindergarten_children, require_parent

logger = logging.getLogger(__name__)


async def _kid(event, max_uid, student_repo, sid: str):
    students = await require_parent(event, student_repo, max_uid)
    if not students:
        return None
    kid = next((s for s in await kindergarten_children(students) if s.student_id == sid), None)
    if kid is None:
        await alert(event, "Ребёнок не в садовой группе")
    return kid


async def _ask(event, context, kid) -> None:
    await context.set_state(MaxParentStates.kgroup_value)
    await context.update_data(kg_sid=kid.student_id)
    await edit_screen(event, *kgroup_prompt_screen(kid))


@router.message_callback(F.callback.payload == "client:kgroup")
async def on_kgroup(event: MessageCallback, context, max_uid, student_repo):
    await context.clear()
    students = await require_parent(event, student_repo, max_uid)
    if not students:
        return
    kids = await kindergarten_children(students)
    if not kids:
        await alert(event, "Нет детей в садовых группах")
    elif len(kids) == 1:
        await _ask(event, context, kids[0])
    else:
        await edit_screen(event, *kgroup_pick_screen(kids))


@router.message_callback(F.callback.payload.startswith("client_kgroup:"))
async def on_kgroup_pick(event: MessageCallback, context, max_uid, student_repo):
    kid = await _kid(event, max_uid, student_repo, event.callback.payload.split(":", 1)[1])
    if kid is not None:
        await _ask(event, context, kid)


@router.message_callback(F.callback.payload.startswith("kgroup_clear:"))
async def on_kgroup_clear(event: MessageCallback, context, max_uid, student_repo):
    await context.clear()
    kid = await _kid(event, max_uid, student_repo, event.callback.payload.split(":", 1)[1])
    if kid is None:
        return
    await student_repo.update_kindergarten_group(kid.student_id, "", actor=max_uid, who="родитель (MAX)")
    await edit_screen(event, *kgroup_saved_screen(kid, ""))


@router.message_created(F.message.body.text, MaxParentStates.kgroup_value)
async def on_kgroup_value(event: MessageCreated, context, max_uid, student_repo):
    if (event.message.body.text or "").strip().startswith("/"):        # команда — выход из ввода
        await context.clear()
        return
    data = await context.get_data()
    sid = data.get("kg_sid", "")
    students = await student_repo.get_by_parent_max_id(max_uid)
    kid = next((s for s in await kindergarten_children(students) if s.student_id == sid), None)
    if kid is None:
        await context.clear()
        await send_screen(event.bot, max_uid, "Ребёнок не найден. Откройте «🏫 Группа в саду» в меню ещё раз.", [])
        return
    value = clean_value(event.message.body.text)
    if not value:
        await send_screen(event.bot, max_uid, "Напишите номер или название группы — не длиннее 40 символов.", [])
        return
    await context.clear()
    await student_repo.update_kindergarten_group(kid.student_id, value, actor=max_uid, who="родитель (MAX)")
    logger.info("MAX: %s указал группу в саду %s для %s", max_uid, value, kid.student_id)
    await send_screen(event.bot, max_uid, *kgroup_saved_screen(kid, value))
