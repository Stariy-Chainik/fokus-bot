from __future__ import annotations
import logging

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    StudentRepository,
)
from bot.services import TeacherVisibilityService
from bot.keyboards.teacher import (
    kb_my_student_card, kb_my_pair_card,
)
from bot.handlers.common import show_card
from bot.handlers.access import is_teacher as _is_teacher

from ._base import router

logger = logging.getLogger(__name__)


# ─── Карточка ученика / пары ─────────────────────────────────────────────────

async def _render_student_card(
    callback: CallbackQuery, student_id: str, user: User,
    student_repo: StudentRepository, visibility: TeacherVisibilityService,
    back_to_pairs: bool = False,
) -> None:
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    mine = await visibility.students_for_teacher(user.teacher_id)
    mine_by_id = {s.student_id: s for s in mine}
    mine_ids = set(mine_by_id)
    if student.student_id not in mine_ids:
        await callback.answer("Ученик не в вашей группе", show_alert=True)
        return
    # mine уже содержит hydrated group_ids — возьмём их оттуда.
    student.group_ids = mine_by_id[student.student_id].group_ids
    # Для back_cb выбираем группу, которая одновременно и у педагога, и у ученика.
    teacher_gids = await visibility.visible_group_ids(user.teacher_id)
    shared_gids = [gid for gid in student.group_ids if gid in teacher_gids]
    primary_gid = shared_gids[0] if shared_gids else ""

    if student.partner_id:
        partner = await student_repo.get_by_id(student.partner_id)
        partner_name = partner.name if partner else f"(удалён: {student.partner_id})"
        # Педагог может управлять парой только если партнёр тоже видим ему.
        can_manage = partner is not None and partner.student_id in mine_ids
        note = "" if can_manage else "\n\n⚠️ Партнёр у другого педагога — управляет админ."
    else:
        partner_name = "— (солист)"
        can_manage = True
        note = ""

    text = (
        f"👩‍🎓 <b>{student.name}</b>\n"
        f"ID: {student.student_id}\n\n"
        f"Партнёр: {partner_name}"
        f"{note}"
    )
    if back_to_pairs and student.partner_id:
        pairs_back_cb = (
            f"t_pairs_grp:{primary_gid}" if primary_gid
            else "teacher:my_pairs"
        )
        kb = kb_my_pair_card(
            student.student_id, student.partner_id,
            back_cb=pairs_back_cb,
        ) if can_manage else InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="« Назад", callback_data=pairs_back_cb)]]
        )
    else:
        back_cb = (
            f"t_solo_grp:{primary_gid}" if primary_gid
            else "teacher:my_soloists"
        )
        kb = kb_my_student_card(
            student.student_id,
            has_partner=bool(student.partner_id),
            can_manage=can_manage,
            back_cb=back_cb,
        )
    await show_card(callback, text, reply_markup=kb)


@router.callback_query(F.data.startswith("t_student_card:"))
async def cb_student_card(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, visibility: TeacherVisibilityService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    await _render_student_card(callback, student_id, user, student_repo, visibility, back_to_pairs=False)


@router.callback_query(F.data.startswith("t_pair_card:"))
async def cb_pair_card(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, visibility: TeacherVisibilityService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    await _render_student_card(callback, student_id, user, student_repo, visibility, back_to_pairs=True)


