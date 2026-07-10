from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from datetime import date
from dateutil.relativedelta import relativedelta

from bot.repositories import (
    TeacherRepository,
    GroupRepository, BranchRepository, TeacherGroupRepository,
    TeacherPeriodSubmissionRepository,
)
from bot.keyboards.admin import kb_teacher_card
from bot.handlers.common import show_card
from bot.utils.dates import display_period




from bot.handlers.access import is_admin as _is_admin

from ._base import router

logger = logging.getLogger(__name__)


# ─── Список педагогов ────────────────────────────────────────────────────────

def _kb_teachers_list_with_status(
    teachers: list, submitted_ids: set[str],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for t in teachers:
        mark = "🟢" if t.teacher_id in submitted_ids else "🔴"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {t.name}", callback_data=f"teacher_card:{t.teacher_id}",
        )])
    rows.append([InlineKeyboardButton(text="➕ Добавить педагога", callback_data="teachers:add")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "teachers:list")
async def cb_teachers_list(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    teacher_repo: TeacherRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    teachers = await teacher_repo.get_all()
    if not teachers:
        await callback.message.edit_text(
            "Педагогов нет.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить педагога", callback_data="teachers:add")],
                [InlineKeyboardButton(text="« Назад", callback_data="admin:menu")],
            ]),
        )
        await callback.answer()
        return
    prev = (date.today() - relativedelta(months=1)).strftime("%Y-%m")
    submitted_ids = {
        s.teacher_id for s in await submission_repo.get_all()
        if s.period_month == prev
    }
    await callback.message.edit_text(
        f"<b>Педагоги</b>\nСтатус сдачи периода: {display_period(prev)}\n"
        "🟢 — сдан, 🔴 — открыт",
        reply_markup=_kb_teachers_list_with_status(teachers, submitted_ids),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("teacher_card:"))
async def cb_teacher_card(
    callback: CallbackQuery, user: User | None, teacher_repo: TeacherRepository,
    teacher_group_repo: TeacherGroupRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    teacher = await teacher_repo.get_by_id(teacher_id)
    if not teacher:
        await callback.answer("Педагог не найден", show_alert=True)
        return
    tg_info = f"@tg_id: {teacher.tg_id}" if teacher.tg_id else "Telegram не привязан"

    gids = set(await teacher_group_repo.get_groups_for_teacher(teacher_id))
    groups = [g for g in await group_repo.get_all() if g.group_id in gids]
    branches = {b.branch_id: b.name for b in await branch_repo.get_all()}
    groups.sort(key=lambda g: (branches.get(g.branch_id, ""), g.name))
    if groups:
        groups_block = "\n".join(
            f"  • {branches.get(g.branch_id, '—')} / {g.name}" for g in groups
        )
    else:
        groups_block = "  —"

    text = (
        f"👨‍🏫 <b>{teacher.name}</b>\n"
        f"ID: {teacher.teacher_id}\n"
        f"{tg_info}\n\n"
        f"📊 Ставки (руб. за 45 мин):\n"
        f"  Групповое: <b>{teacher.rate_group}</b>\n"
        f"  Инд. педагогу: <b>{teacher.rate_for_teacher}</b>\n"
        f"  Инд. ученику: <b>{teacher.rate_for_student}</b>\n\n"
        f"🏢 Группы:\n{groups_block}"
    )
    await show_card(callback, text, reply_markup=kb_teacher_card(teacher_id))

