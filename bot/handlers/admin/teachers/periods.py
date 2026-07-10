from __future__ import annotations
import logging

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User

from bot.repositories import (
    TeacherRepository,
    TeacherPeriodSubmissionRepository,
)
from bot.keyboards.admin import kb_confirm
from bot.utils.dates import display_period




from bot.handlers.access import is_admin as _is_admin

from ._base import router

logger = logging.getLogger(__name__)


# ─── Открытие (сброс) сданного периода ───────────────────────────────────────

@router.callback_query(F.data.startswith("open_period_list:"))
async def cb_open_period_list(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    teacher = await teacher_repo.get_by_id(teacher_id)
    if not teacher:
        await callback.answer("Педагог не найден", show_alert=True)
        return
    submissions = sorted(
        await submission_repo.get_by_teacher(teacher_id),
        key=lambda s: s.period_month, reverse=True,
    )
    if not submissions:
        await callback.answer("Нет сданных периодов.", show_alert=True)
        return
    rows = [
        [InlineKeyboardButton(
            text=display_period(s.period_month),
            callback_data=f"open_period_confirm:{teacher_id}:{s.period_month}",
        )]
        for s in submissions
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"teacher_card:{teacher_id}")])
    await callback.message.edit_text(
        f"<b>Открыть период — {teacher.name}</b>\n"
        "Выберите период для открытия (сдача будет отменена):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("open_period_confirm:"))
async def cb_open_period_confirm(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, teacher_id, period_month = callback.data.split(":", 2)
    teacher = await teacher_repo.get_by_id(teacher_id)
    name = teacher.name if teacher else teacher_id
    await callback.message.edit_text(
        f"<b>Открыть период {display_period(period_month)} для «{name}»?</b>\n\n"
        "Педагог снова сможет редактировать занятия этого месяца.",
        reply_markup=kb_confirm(
            f"open_period_do:{teacher_id}:{period_month}",
            f"open_period_list:{teacher_id}",
            confirm_text="🔓 Открыть",
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("open_period_do:"))
async def cb_open_period_do(
    callback: CallbackQuery, user: User | None,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, teacher_id, period_month = callback.data.split(":", 2)
    ok = await submission_repo.delete_by_teacher_and_period(teacher_id, period_month)
    if ok:
        await callback.message.edit_text(
            f"✅ Период {display_period(period_month)} открыт. Педагог снова может редактировать занятия.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« К карточке педагога", callback_data=f"teacher_card:{teacher_id}")],
            ]),
        )
    else:
        await callback.answer("Запись не найдена — возможно, уже открыт.", show_alert=True)
    await callback.answer()

