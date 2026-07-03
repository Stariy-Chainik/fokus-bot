from __future__ import annotations
import re
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User, Student, StudentRequest, GroupBillingMode, StudentGroupTier
from bot.repositories import (
    StudentRepository,
    GroupRepository, BranchRepository, StudentGroupRepository,
    StudentRequestRepository, ClientRepository,
)
from bot.services import (
    StudentService, TierToggleError,
    StudentRequestService, LinkExistingOutcome,
)
from bot.models.enums import RequestStatus
from bot.states import AddStudentStates, StudentListStates, PartnerAssignStates, ClientCreateStates
from bot.handlers.common import show_card
from bot.keyboards.admin import (
    kb_students_menu,
    kb_student_paged, kb_student_card, kb_partner_candidates,
    kb_confirm, kb_back, _STUDENT_PAGE_SIZE,
)
from bot.handlers.access import is_admin as _is_admin

from ._base import router

logger = logging.getLogger(__name__)
from ._base import _render_student_card


# ─── Список учеников с поиском ───────────────────────────────────────────────

def _filter_and_page(students: list, query: str, page: int):
    if query:
        filtered = [s for s in students if query.lower() in s.name.lower()]
    else:
        filtered = students
    start = page * _STUDENT_PAGE_SIZE
    return filtered[start:start + _STUDENT_PAGE_SIZE], len(filtered)


@router.callback_query(F.data == "students:list")
async def cb_students_list(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(StudentListStates.searching)
    await state.update_data(student_query="")
    await callback.message.edit_text(
        "<b>Поиск ученика</b>\n"
        "Введите имя или часть имени ученика для поиска.\n"
        "Чтобы показать всех — отправьте <b>*</b>",
        reply_markup=kb_back("admin:students"),
    )
    await callback.answer()


@router.message(StudentListStates.searching)
async def handle_student_search(
    message: Message, state: FSMContext, student_repo: StudentRepository,
) -> None:
    query = message.text.strip() if message.text else ""
    if query == "*":
        query = ""
    await state.update_data(student_query=query)
    all_students = sorted(await student_repo.get_all(), key=lambda s: s.name)
    page_students, total = _filter_and_page(all_students, query, 0)
    if not page_students:
        await message.answer("Ничего не найдено. Попробуйте другой запрос.")
        return
    label = f"Найдено: {total}" if query else f"Всего учеников: {total}"
    await message.answer(
        f"<b>{label}. Страница 1:</b>",
        reply_markup=kb_student_paged(page_students, 0, total),
    )


@router.callback_query(F.data.startswith("spage:"))
async def cb_student_page(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        page = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    data = await state.get_data()
    query = data.get("student_query", "")
    all_students = sorted(await student_repo.get_all(), key=lambda s: s.name)
    page_students, total = _filter_and_page(all_students, query, page)
    await callback.message.edit_text(
        f"<b>Страница {page + 1}:</b>",
        reply_markup=kb_student_paged(page_students, page, total),
    )
    await callback.answer()



_TIER_TOGGLE_ALERTS = {
    TierToggleError.STUDENT_NOT_FOUND: "Ученик не найден",
    TierToggleError.NO_GROUPS: "У ученика не задана группа",
    TierToggleError.NO_PER_VISIT_GROUP: "Ни у одной группы ученика не включён биллинг per-visit",
}


@router.callback_query(F.data.startswith("student_tier_toggle:"))
async def cb_student_tier_toggle(
    callback: CallbackQuery, user: User | None,
    student_service: StudentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    error = await student_service.toggle_tier(student_id)
    if error is not None:
        await callback.answer(_TIER_TOGGLE_ALERTS[error], show_alert=True)
        return
    await _render_student_card(callback, student_id, "students:list", student_service)


@router.callback_query(F.data.startswith("student_card:"))
async def cb_student_card(
    callback: CallbackQuery, user: User | None,
    student_service: StudentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    await _render_student_card(callback, student_id, "students:list", student_service)


@router.callback_query(F.data.startswith("student_card_sp:"))
async def cb_student_card_from_sp(
    callback: CallbackQuery, user: User | None,
    student_service: StudentService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, mode, group_id, student_id = callback.data.split(":", 3)
    back_cb = f"sp_grp:{mode}:{group_id}"
    await _render_student_card(callback, student_id, back_cb, student_service)


