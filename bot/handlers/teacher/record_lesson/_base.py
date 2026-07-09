from __future__ import annotations
"""
Педагог: FSM «Отметить занятие».
Порядок: Дата → Тип (группа/пара/соло) → Длительность → ветка → создание.
Группа: опциональная отметка присутствующих. Пара: выбор одной пары.
Соло: мульти-выбор учеников (включая тех, кто в паре — если пришли одни).
Защита от двойного нажатия — InProgressGuard _confirming_lesson_ids по tg_id.
"""
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
)
from bot.keyboards.admin import kb_admin_menu
from bot.utils import build_group_attendees_csv
from bot.keyboards.calendar import kb_calendar
from bot.utils.dates import format_date_display
from bot.utils.locks import InProgressGuard

logger = logging.getLogger(__name__)
router = Router(name="teacher_record_lesson")

_KIND_LABEL = {"group": "Группа", "pair": "Пара", "soloist": "Соло"}


from bot.handlers.access import is_teacher_or_admin as _is_teacher


def _tid(user: User, data: dict) -> str | None:
    """Эффективный teacher_id: proxy (запись от имени) или собственный."""
    return data.get("proxy_teacher_id") or user.teacher_id


def _menu_kb(user: User | None, data: dict) -> InlineKeyboardMarkup:
    """Клавиатура «назад» с учётом роли (педагог / прокси-админ)."""
    if user and user.is_admin and (not user.teacher_id or data.get("proxy_teacher_id")):
        return kb_admin_menu()
    can_switch = bool(user and user.is_admin and user.teacher_id)
    return kb_teacher_menu(can_switch_role=can_switch, teacher_id=user.teacher_id if user else None)


async def _all_students_in_group(
    group_id: str, student_repo, student_group_repo: StudentGroupRepository,
):
    """Все ученики группы (без фильтра по педагогу) — для отметки присутствующих."""
    member_ids = set(await student_group_repo.get_students_for_group(group_id))
    members = [s for s in await student_repo.get_all() if s.student_id in member_ids]
    members.sort(key=lambda s: s.name)
    return members


def _date_picker_kb() -> InlineKeyboardMarkup:
    today = date.today()
    yesterday = today - timedelta(days=1)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"Сегодня ({today.strftime('%d.%m')})",
                callback_data=f"lesson_date:{today.isoformat()}",
            ),
            InlineKeyboardButton(
                text=f"Вчера ({yesterday.strftime('%d.%m')})",
                callback_data=f"lesson_date:{yesterday.isoformat()}",
            ),
        ],
        [InlineKeyboardButton(text="📅 Другая дата", callback_data="lesson_date:manual")],
        [InlineKeyboardButton(text="« Отмена", callback_data="teacher:cancel_lesson")],
    ])


def _header(data: dict) -> str:
    parts = []
    if data.get("lesson_date"):
        parts.append(f"Дата: {format_date_display(data['lesson_date'])}")
    if data.get("kind"):
        parts.append(f"Тип: {_KIND_LABEL.get(data['kind'], data['kind'])}")
    if data.get("duration_min"):
        parts.append(f"{data['duration_min']} мин")
    header = " | ".join(parts)
    return (f"<b>{header}</b>\n\n" if parts else "")
