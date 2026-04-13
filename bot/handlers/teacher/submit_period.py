from __future__ import annotations
import logging
from collections import defaultdict

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User, TeacherPeriodSubmission
from bot.models.enums import LessonType
from bot.repositories import LessonRepository, TeacherPeriodSubmissionRepository
from bot.states import SubmitPeriodStates
from bot.keyboards.teacher import kb_teacher_menu
from bot.utils import generate_submission_id, now_str
from bot.utils.dates import display_period

logger = logging.getLogger(__name__)
router = Router(name="teacher_submit_period")

_submitting: set[str] = set()


def _is_teacher(user: User | None) -> bool:
    return user is not None and user.teacher_id is not None


async def _period_summary(
    teacher_id: str, period_month: str, lesson_repo: LessonRepository,
) -> tuple[int, int, int, int]:
    """→ (total, group_count, ind_count, total_earned)."""
    lessons = await lesson_repo.get_by_teacher_and_period(teacher_id, period_month)
    total = len(lessons)
    group = sum(1 for ls in lessons if ls.type == LessonType.GROUP)
    ind = total - group
    earned = sum(ls.earned for ls in lessons)
    return total, group, ind, earned


@router.callback_query(F.data == "teacher:submit_period")
async def cb_submit_period_start(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()

    lessons = await lesson_repo.get_by_teacher(user.teacher_id)
    counts: dict[str, int] = defaultdict(int)
    earned: dict[str, int] = defaultdict(int)
    for ls in lessons:
        pm = ls.date[:7]
        counts[pm] += 1
        earned[pm] += ls.earned

    submitted = {s.period_month for s in await submission_repo.get_by_teacher(user.teacher_id)}
    open_periods = sorted((p for p in counts if p not in submitted), reverse=True)

    if not open_periods:
        await callback.message.edit_text(
            "Нет периодов, доступных для сдачи.", reply_markup=kb_teacher_menu(),
        )
        await callback.answer()
        return

    rows = []
    for p in open_periods:
        label = f"{display_period(p)} — {counts[p]} зан., {earned[p]} ₽"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"submit_pick:{p}")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:menu")])

    await state.set_state(SubmitPeriodStates.choosing_month)
    await callback.message.edit_text(
        "Выберите период для сдачи:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("submit_pick:"), SubmitPeriodStates.choosing_month)
async def cb_submit_pick(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    period_month = callback.data.split(":", 1)[1]

    if await submission_repo.get_by_teacher_and_period(user.teacher_id, period_month):
        await callback.answer("Период уже сдан.", show_alert=True)
        return

    total, group, ind, earned = await _period_summary(user.teacher_id, period_month, lesson_repo)
    if total == 0:
        await callback.answer("В этом периоде нет занятий.", show_alert=True)
        return

    await state.update_data(period_month=period_month, total=total, earned=earned)
    await state.set_state(SubmitPeriodStates.confirming)

    text = (
        f"Сдать период {display_period(period_month)}?\n\n"
        f"Занятий: {total} (груп.: {group}, инд.: {ind})\n"
        f"Итого начислено: {earned} ₽\n\n"
        "После сдачи редактирование занятий этого месяца станет недоступно."
    )
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить сдачу", callback_data="submit_confirm")],
            [InlineKeyboardButton(text="« Отмена", callback_data="teacher:submit_period")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "submit_confirm", SubmitPeriodStates.confirming)
async def cb_submit_confirm(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    lock_key = f"{user.teacher_id}"
    if lock_key in _submitting:
        await callback.answer("Сдача уже выполняется, подождите.", show_alert=True)
        return
    _submitting.add(lock_key)

    try:
        data = await state.get_data()
        period_month = data.get("period_month")
        total = int(data.get("total") or 0)
        earned = int(data.get("earned") or 0)
        if not period_month:
            await callback.answer("Период не выбран", show_alert=True)
            return

        # защита от двойной сдачи
        if await submission_repo.get_by_teacher_and_period(user.teacher_id, period_month):
            await state.clear()
            await callback.message.edit_text(
                f"Период {display_period(period_month)} уже сдан.", reply_markup=kb_teacher_menu(),
            )
            await callback.answer()
            return

        existing_ids = await submission_repo.get_existing_ids()
        sub = TeacherPeriodSubmission(
            submission_id=generate_submission_id(existing_ids),
            teacher_id=user.teacher_id,
            period_month=period_month,
            submitted_at=now_str(),
            lessons_count=total,
            total_earned=earned,
        )
        await submission_repo.add(sub)
        logger.info("Сдан период %s teacher=%s lessons=%d earned=%d",
                    period_month, user.teacher_id, total, earned)

        await state.clear()
        await callback.message.edit_text(
            f"✅ Период {display_period(period_month)} сдан.\n"
            f"Занятия этого месяца больше редактировать нельзя.",
            reply_markup=kb_teacher_menu(),
        )
        await callback.answer()
    finally:
        _submitting.discard(lock_key)
