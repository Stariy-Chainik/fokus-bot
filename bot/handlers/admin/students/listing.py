from __future__ import annotations
import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.models import User
from bot.repositories import (
    StudentRepository,
)
from bot.services import (
    StudentService, TierToggleError,
)
from bot.states import StudentListStates
from bot.keyboards.admin import (
    kb_student_paged,
    kb_back,
)
from bot.utils.constants import STUDENT_PAGE_SIZE
from bot.utils.paging import Page, paginate
from bot.handlers.filters import AdminOnly

from ._base import router
from ._base import _render_student_card

logger = logging.getLogger(__name__)


# ─── Список учеников с поиском ───────────────────────────────────────────────

def _filter_and_page(students: list, query: str, page: int) -> Page:
    if query:
        filtered = [s for s in students if query.lower() in s.name.lower()]
    else:
        filtered = students
    return paginate(filtered, page, STUDENT_PAGE_SIZE)


@router.callback_query(F.data == "students:list", AdminOnly())
async def cb_students_list(callback: CallbackQuery, user: User, state: FSMContext) -> None:
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
    pg = _filter_and_page(all_students, query, 0)
    if not pg.items:
        await message.answer("Ничего не найдено. Попробуйте другой запрос.")
        return
    label = f"Найдено: {pg.total}" if query else f"Всего учеников: {pg.total}"
    await message.answer(
        f"<b>{label}. Страница 1:</b>",
        reply_markup=kb_student_paged(pg),
    )


@router.callback_query(F.data.startswith("spage:"), AdminOnly())
async def cb_student_page(
    callback: CallbackQuery, state: FSMContext, user: User,
    student_repo: StudentRepository,
) -> None:
    try:
        page = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    data = await state.get_data()
    query = data.get("student_query", "")
    all_students = sorted(await student_repo.get_all(), key=lambda s: s.name)
    pg = _filter_and_page(all_students, query, page)
    await callback.message.edit_text(
        f"<b>Страница {page + 1}:</b>",
        reply_markup=kb_student_paged(pg),
    )
    await callback.answer()



_TIER_TOGGLE_ALERTS = {
    TierToggleError.STUDENT_NOT_FOUND: "Ученик не найден",
    TierToggleError.NO_GROUPS: "У ученика не задана группа",
    TierToggleError.NO_PER_VISIT_GROUP: "Ни у одной группы ученика не включён биллинг per-visit",
}


@router.callback_query(F.data.startswith("student_tier_toggle:"), AdminOnly())
async def cb_student_tier_toggle(
    callback: CallbackQuery, user: User,
    student_service: StudentService,
) -> None:
    student_id = callback.data.split(":", 1)[1]
    error = await student_service.toggle_tier(student_id)
    if error is not None:
        await callback.answer(_TIER_TOGGLE_ALERTS[error], show_alert=True)
        return
    await _render_student_card(callback, student_id, "students:list", student_service)


@router.callback_query(F.data.startswith("student_card:"), AdminOnly())
async def cb_student_card(
    callback: CallbackQuery, user: User,
    student_service: StudentService,
) -> None:
    student_id = callback.data.split(":", 1)[1]
    await _render_student_card(callback, student_id, "students:list", student_service)


@router.callback_query(F.data.startswith("student_card_sp:"), AdminOnly())
async def cb_student_card_from_sp(
    callback: CallbackQuery, user: User,
    student_service: StudentService,
) -> None:
    _, mode, group_id, student_id = callback.data.split(":", 3)
    back_cb = f"sp_grp:{mode}:{group_id}"
    await _render_student_card(callback, student_id, back_cb, student_service)


