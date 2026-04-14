from __future__ import annotations
import logging
from collections import defaultdict
from datetime import date

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User, TeacherPeriodSubmission
from bot.models.enums import LessonType
from bot.repositories import LessonRepository, TeacherPeriodSubmissionRepository
from bot.services import LessonService
from bot.states import SubmitPeriodStates
from bot.keyboards.teacher import kb_teacher_menu
from bot.utils import generate_submission_id, now_str
from bot.utils.dates import display_period
from bot.utils.lesson_stats import format_lesson_breakdown

logger = logging.getLogger(__name__)
router = Router(name="teacher_submit_period")

_submitting: set[str] = set()


def _is_teacher(user: User | None) -> bool:
    return user is not None and user.teacher_id is not None


async def _period_breakdown(
    teacher_id: str, period_month: str, lesson_repo: LessonRepository,
) -> tuple[int, int, int, str, str]:
    """→ (total, group_count, ind_count, group_line, ind_line)."""
    lessons = await lesson_repo.get_by_teacher_and_period(teacher_id, period_month)
    group, ind, gline, iline = format_lesson_breakdown(lessons)
    return group + ind, group, ind, gline, iline


async def _open_periods(
    teacher_id: str, lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> tuple[list[str], dict[str, int]]:
    """→ (sorted desc list of open periods, counts by period)."""
    lessons = await lesson_repo.get_by_teacher(teacher_id)
    counts: dict[str, int] = defaultdict(int)
    for ls in lessons:
        counts[ls.date[:7]] += 1
    submitted = {s.period_month for s in await submission_repo.get_by_teacher(teacher_id)}
    open_periods = sorted((p for p in counts if p not in submitted), reverse=True)
    return open_periods, counts


async def _show_confirm(
    callback: CallbackQuery, state: FSMContext, user: User,
    period_month: str, lesson_repo: LessonRepository,
    open_periods: list[str],
) -> None:
    total, group, ind, gline, iline = await _period_breakdown(user.teacher_id, period_month, lesson_repo)
    if total == 0:
        # Текущий месяц пустой — предложим выбрать другой, если есть.
        rows: list[list[InlineKeyboardButton]] = []
        if open_periods:
            rows.append([InlineKeyboardButton(
                text="📅 Выбрать другой месяц", callback_data="submit_pick_other",
            )])
        rows.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:menu")])
        await callback.message.edit_text(
            f"<b>Сдать период {display_period(period_month)}</b>\n"
            "В этом месяце нет занятий.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return

    await state.update_data(period_month=period_month, total=total)
    await state.set_state(SubmitPeriodStates.confirming)

    rows = [[InlineKeyboardButton(text="✅ Подтвердить сдачу", callback_data="submit_confirm")]]
    # Если есть другие открытые периоды кроме текущего — дать возможность выбрать.
    others = [p for p in open_periods if p != period_month]
    if others:
        rows.append([InlineKeyboardButton(
            text="📅 Выбрать другой месяц", callback_data="submit_pick_other",
        )])
    rows.append([InlineKeyboardButton(text="« Отмена", callback_data="teacher:menu")])

    await callback.message.edit_text(
        f"<b>Сдать период {display_period(period_month)}?</b>\n\n"
        f"Всего занятий: {total}\n"
        f"👥 Групповые ({group}): {gline}\n"
        f"👤 Индивидуальные ({ind}): {iline}\n\n"
        "После сдачи редактирование занятий этого месяца станет недоступно.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


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

    open_periods, _counts = await _open_periods(user.teacher_id, lesson_repo, submission_repo)
    current = date.today().strftime("%Y-%m")
    current_submitted = await submission_repo.get_by_teacher_and_period(user.teacher_id, current)

    # По умолчанию — текущий месяц, если он ещё не сдан.
    if not current_submitted:
        await _show_confirm(callback, state, user, current, lesson_repo, open_periods)
        await callback.answer()
        return

    # Текущий уже сдан — покажем список остальных открытых.
    if not open_periods:
        await callback.message.edit_text(
            f"<b>Сдать период</b>\n"
            f"Текущий месяц ({display_period(current)}) уже сдан, других открытых периодов нет.",
            reply_markup=kb_teacher_menu(),
        )
        await callback.answer()
        return

    await _show_period_list(callback, state, open_periods, _counts)
    await callback.answer()


async def _show_period_list(
    callback: CallbackQuery, state: FSMContext,
    open_periods: list[str], counts: dict[str, int],
) -> None:
    rows = []
    for p in open_periods:
        label = f"{display_period(p)} — {counts[p]} зан."
        rows.append([InlineKeyboardButton(text=label, callback_data=f"submit_pick:{p}")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="teacher:menu")])
    await state.set_state(SubmitPeriodStates.choosing_month)
    await callback.message.edit_text(
        "<b>Сдать период</b>\nВыберите месяц для сдачи:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "submit_pick_other")
async def cb_submit_pick_other(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    open_periods, counts = await _open_periods(user.teacher_id, lesson_repo, submission_repo)
    if not open_periods:
        await callback.message.edit_text(
            "Нет открытых периодов.", reply_markup=kb_teacher_menu(),
        )
        await callback.answer()
        return
    await _show_period_list(callback, state, open_periods, counts)
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

    open_periods, _c = await _open_periods(user.teacher_id, lesson_repo, submission_repo)
    await _show_confirm(callback, state, user, period_month, lesson_repo, open_periods)
    await callback.answer()


@router.callback_query(F.data == "submit_confirm", SubmitPeriodStates.confirming)
async def cb_submit_confirm(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    lesson_repo: LessonRepository,
    submission_repo: TeacherPeriodSubmissionRepository,
    lesson_service: LessonService,
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

        # Расчёт: проставляем earned в lessons и создаём billing.
        lessons_count, total_earned = await lesson_service.finalize_period(
            user.teacher_id, period_month,
        )

        existing_ids = await submission_repo.get_existing_ids()
        sub = TeacherPeriodSubmission(
            submission_id=generate_submission_id(existing_ids),
            teacher_id=user.teacher_id,
            period_month=period_month,
            submitted_at=now_str(),
            lessons_count=lessons_count,
            total_earned=total_earned,
        )
        await submission_repo.add(sub)
        logger.info("Сдан период %s teacher=%s lessons=%d earned=%d",
                    period_month, user.teacher_id, lessons_count, total_earned)

        total, group, ind, gline, iline = await _period_breakdown(
            user.teacher_id, period_month, lesson_repo,
        )
        await state.clear()
        await callback.message.edit_text(
            f"<b>✅ Период {display_period(period_month)} сдан</b>\n\n"
            f"Всего занятий: {total}\n"
            f"👥 Групповые ({group}): {gline}\n"
            f"👤 Индивидуальные ({ind}): {iline}\n\n"
            f"Занятия этого месяца больше редактировать нельзя.",
            reply_markup=kb_teacher_menu(),
        )
        await callback.answer()
    finally:
        _submitting.discard(lock_key)
