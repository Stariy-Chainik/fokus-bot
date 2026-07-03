from __future__ import annotations
import logging
from datetime import date, timedelta

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User, GroupBillingMode, StudentGroupTier
from bot.models.enums import LessonType
from bot.repositories import (
    TeacherRepository, StudentRepository,
    GroupRepository, BranchRepository, TeacherGroupRepository,
    StudentGroupRepository, UserRepository,
)
from bot.services import LessonService, TeacherVisibilityService
from bot.states import RecordLessonStates
from bot.keyboards.teacher import (
    kb_lesson_type, kb_lesson_type_after_save, kb_duration, kb_teacher_menu,
    kb_attendance_yes_no, kb_pair_multi_select, kb_pair_from_soloists,
    kb_multi_select, kb_group_roster_per_visit,
    kb_other_groups_picker, kb_other_group_students,
    kb_group_branch_picker, kb_group_picker, kb_shared_group_picker,
    _PROXY_BUTTONS,
)
from bot.keyboards.admin import kb_admin_menu
from bot.utils import build_group_attendees_csv
from bot.keyboards.calendar import kb_calendar
from bot.utils.dates import format_date_display
from bot.utils.locks import InProgressGuard

from ._base import router
from ._base import _date_picker_kb, _header
from .flows import _start_group_flow, _show_pair_list
from bot.handlers.access import is_teacher_or_admin as _is_teacher

logger = logging.getLogger(__name__)


@router.callback_query(F.data == "teacher:record_lesson")
async def cb_record_lesson_start(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await state.set_state(RecordLessonStates.choosing_date)
    await callback.message.edit_text(
        "<b>Отметить занятие</b>\nВыберите дату:", reply_markup=_date_picker_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("proxy_record:"))
async def cb_proxy_record_start(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
    teacher_repo: TeacherRepository,
    user_repo: UserRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    allowed = user.is_admin or (user.teacher_id in _PROXY_BUTTONS)  # type: ignore[union-attr]
    if not allowed:
        await callback.answer("Нет доступа", show_alert=True)
        return

    # Администратор может записывать без подтверждения
    if user.is_admin:
        await state.clear()
        await state.update_data(proxy_teacher_id=teacher_id)
        await state.set_state(RecordLessonStates.choosing_date)
        await callback.message.edit_text(
            "<b>Отметить занятие</b>\nВыберите дату:", reply_markup=_date_picker_kb(),
        )
        await callback.answer()
        return

    # Ассистент (Клецова) — запрашивает разрешение у администратора
    teacher = await teacher_repo.get_by_id(teacher_id)
    teacher_name = teacher.name if teacher else teacher_id

    approve_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Разрешить", callback_data=f"proxy_approve:{callback.from_user.id}:{teacher_id}"),
        InlineKeyboardButton(text="❌ Отказать", callback_data=f"proxy_deny:{callback.from_user.id}:{teacher_id}"),
    ]])
    msg = (
        f"👤 <b>Запрос на запись занятия</b>\n\n"
        f"Клецова хочет отметить занятие за <b>{teacher_name}</b>.\n"
        f"Разрешить?"
    )
    admins = await user_repo.get_admins()
    for admin in admins:
        try:
            await callback.bot.send_message(admin.tg_id, msg, reply_markup=approve_kb)
        except Exception:
            pass

    can_switch = bool(user.is_admin and user.teacher_id)
    await callback.message.edit_text(
        "⏳ Запрос отправлен администратору. Ожидайте разрешения.",
        reply_markup=kb_teacher_menu(can_switch_role=can_switch, teacher_id=user.teacher_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("proxy_approve:"))
async def cb_proxy_approve(callback: CallbackQuery, user: User | None) -> None:
    if not user or not user.is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, requester_tg_id_str, teacher_id = callback.data.split(":", 2)
    requester_tg_id = int(requester_tg_id_str)

    start_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="▶ Начать запись", callback_data=f"proxy_record_go:{teacher_id}"),
    ]])
    try:
        await callback.bot.send_message(
            requester_tg_id,
            "✅ Администратор разрешил запись. Нажмите кнопку для начала:",
            reply_markup=start_kb,
        )
    except Exception:
        pass

    try:
        await callback.message.edit_text(
            (callback.message.text or "") + "\n\n✅ Разрешено",
            reply_markup=None,
        )
    except Exception:
        pass
    await callback.answer("Разрешено")


@router.callback_query(F.data.startswith("proxy_deny:"))
async def cb_proxy_deny(callback: CallbackQuery, user: User | None) -> None:
    if not user or not user.is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, requester_tg_id_str, _teacher_id = callback.data.split(":", 2)
    requester_tg_id = int(requester_tg_id_str)

    try:
        await callback.bot.send_message(
            requester_tg_id,
            "❌ Администратор отклонил запрос на запись занятия.",
        )
    except Exception:
        pass

    try:
        await callback.message.edit_text(
            (callback.message.text or "") + "\n\n❌ Отклонено",
            reply_markup=None,
        )
    except Exception:
        pass
    await callback.answer("Отклонено")


@router.callback_query(F.data.startswith("proxy_record_go:"))
async def cb_proxy_record_go(
    callback: CallbackQuery, user: User | None, state: FSMContext,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teacher_id = callback.data.split(":", 1)[1]
    await state.clear()
    await state.update_data(proxy_teacher_id=teacher_id)
    await state.set_state(RecordLessonStates.choosing_date)
    await callback.message.edit_text(
        "<b>Отметить занятие</b>\nВыберите дату:", reply_markup=_date_picker_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "teacher:cancel_lesson")
async def cb_cancel_lesson(callback: CallbackQuery, state: FSMContext, user: User | None) -> None:
    data = await state.get_data()
    is_proxy = bool(data.get("proxy_teacher_id"))
    await state.clear()
    if user and user.is_admin and (is_proxy or not user.teacher_id):
        await callback.message.edit_text("Отменено.", reply_markup=kb_admin_menu())
    else:
        can_switch = bool(user and user.is_admin and user.teacher_id)
        await callback.message.edit_text(
            "Отменено.", reply_markup=kb_teacher_menu(can_switch_role=can_switch, teacher_id=user.teacher_id if user else None),
        )
    await callback.answer()


# ─── Назад на шаг ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("lesson_back:"))
async def cb_lesson_back(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    visibility: TeacherVisibilityService, student_repo: StudentRepository,
    teacher_group_repo: TeacherGroupRepository, group_repo: GroupRepository,
    branch_repo: BranchRepository,
    teacher_repo: TeacherRepository, lesson_service: LessonService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    target = callback.data.split(":", 1)[1]
    data = await state.get_data()

    if target == "date":
        await state.set_state(RecordLessonStates.choosing_date)
        await callback.message.edit_text(
            "<b>Отметить занятие</b>\nВыберите дату:", reply_markup=_date_picker_kb(),
        )

    elif target == "kind":
        # очистим данные ниже по воронке
        await state.update_data(kind=None, duration_min=None, selected_ids=[])
        await state.set_state(RecordLessonStates.choosing_kind)
        data = await state.get_data()
        await callback.message.edit_text(
            f"{_header(data)}Тип занятия:", reply_markup=kb_lesson_type(),
        )

    elif target == "duration":
        await state.update_data(selected_ids=[])
        await state.set_state(RecordLessonStates.choosing_duration)
        data = await state.get_data()
        await callback.message.edit_text(
            f"{_header(data)}Выберите длительность:",
            reply_markup=kb_duration(back_cb="lesson_back:kind"),
        )

    elif target == "attendance":
        # Возврат к вопросу «отметить присутствующих?» из roster.
        gid = data.get("selected_group_id") or ""
        group = await group_repo.get_by_id(gid) if gid else None
        gname = group.name if group else ""
        await state.set_state(RecordLessonStates.asking_attendance)
        await state.update_data(selected_ids=[])
        prefix = f"Группа: <b>{gname}</b>\n" if gname else ""
        await callback.message.edit_text(
            f"{_header(data)}{prefix}Отметить присутствующих?",
            reply_markup=kb_attendance_yes_no(),
        )

    elif target == "pair":
        await state.update_data(
            selected_group_id=None, selected_branch_id=None,
            selected_ids=[], group_auto=False, pair_from_soloists=False,
        )
        await _show_pair_list(callback, state, user, visibility)

    elif target == "group":
        # Возврат к пикеру группы (или филиала) из attendance yes/no / pso roster.
        if data.get("group_auto"):
            await state.update_data(
                selected_group_id=None, selected_ids=[],
                group_auto=False, pair_from_soloists=False,
            )
            await state.set_state(RecordLessonStates.choosing_duration)
            await callback.message.edit_text(
                f"{_header(data)}Выберите длительность:",
                reply_markup=kb_duration(back_cb="lesson_back:kind"),
            )
        else:
            await state.update_data(selected_group_id=None, selected_ids=[])
            await _start_group_flow(
                callback, state, user, teacher_group_repo, group_repo, branch_repo, student_repo, visibility,
                back_cb="lesson_back:duration",
                teacher_repo=teacher_repo, lesson_service=lesson_service,
            )

    elif target == "group_branch":
        # Возврат к выбору филиала из пикера группы.
        await state.update_data(selected_branch_id=None, selected_group_id=None)
        await _start_group_flow(
            callback, state, user, teacher_group_repo, group_repo, branch_repo, student_repo, visibility,
            back_cb="lesson_back:duration",
        )

    await callback.answer()

