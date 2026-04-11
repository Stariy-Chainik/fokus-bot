from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.models import User
from bot.repositories import StudentRepository, TeacherRepository, TeacherStudentRepository
from bot.states import AddStudentStates, LinkTeacherStudentStates, StudentListStates
from bot.keyboards.admin import (
    kb_students_menu, kb_teacher_list, kb_student_list,
    kb_student_paged, kb_student_card, kb_confirm, kb_back, _STUDENT_PAGE_SIZE,
)

logger = logging.getLogger(__name__)
router = Router(name="admin_students")


def _is_admin(user: User | None) -> bool:
    return user is not None and user.is_admin


@router.callback_query(F.data == "admin:students")
async def cb_students_menu(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("Управление учениками:", reply_markup=kb_students_menu())
    await callback.answer()


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
        "Введите имя или часть имени ученика для поиска.\n"
        "Чтобы показать всех — отправьте <b>*</b>"
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
        f"{label}. Страница 1:",
        reply_markup=kb_student_paged(page_students, 0, total, query),
    )


@router.callback_query(F.data.startswith("spage:"))
async def cb_student_page(
    callback: CallbackQuery, user: User | None, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = callback.data.split(":")
    query = parts[1].replace("_", ":") if parts[1] != "" else ""
    page = int(parts[2])
    all_students = sorted(await student_repo.get_all(), key=lambda s: s.name)
    page_students, total = _filter_and_page(all_students, query, page)
    await callback.message.edit_text(
        f"Страница {page + 1}:",
        reply_markup=kb_student_paged(page_students, page, total, query),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("student_card:"))
async def cb_student_card(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository, teacher_repo: TeacherRepository, ts_repo: TeacherStudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    teacher_ids = await ts_repo.get_teachers_for_student(student_id)
    if teacher_ids:
        all_teachers = await teacher_repo.get_all()
        teachers_map = {t.teacher_id: t.name for t in all_teachers}
        teachers_text = "\n".join(f"  • {teachers_map.get(tid, tid)}" for tid in teacher_ids)
    else:
        teachers_text = "  не привязан"
    text = (
        f"👩‍🎓 <b>{student.name}</b>\n"
        f"ID: {student.student_id}\n\n"
        f"Педагоги:\n{teachers_text}"
    )
    await callback.message.edit_text(text, reply_markup=kb_student_card(student_id))
    await callback.answer()


# ─── Добавление ученика ───────────────────────────────────────────────────────

@router.callback_query(F.data == "students:add")
async def cb_add_student_start(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AddStudentStates.entering_name)
    await callback.message.edit_text("Введите имя ученика:")
    await callback.answer()


@router.message(AddStudentStates.entering_name)
async def add_student_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip() if message.text else ""
    if not name:
        await message.answer("Имя не может быть пустым:")
        return
    await state.update_data(name=name)
    await state.set_state(AddStudentStates.confirming)
    await message.answer(f"Добавить ученика «{name}»?", reply_markup=kb_confirm("confirm_add_student", "admin:students"))


@router.callback_query(F.data == "confirm_add_student")
async def cb_confirm_add_student(
    callback: CallbackQuery, state: FSMContext, user: User | None, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    await state.clear()
    try:
        student = await student_repo.add(data["name"])
        await callback.message.edit_text(
            f"Ученик добавлен!\nID: {student.student_id}\nИмя: {student.name}",
            reply_markup=kb_back("admin:students"),
        )
    except Exception as exc:
        logger.error("Ошибка добавления ученика: %s", exc)
        await callback.message.edit_text("Ошибка при добавлении ученика.", reply_markup=kb_back("admin:students"))
    await callback.answer()


# ─── Удаление ученика ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "students:delete")
async def cb_delete_student_start(
    callback: CallbackQuery, user: User | None, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    students = await student_repo.get_all()
    if not students:
        await callback.message.edit_text("Учеников нет.", reply_markup=kb_back("admin:students"))
        await callback.answer()
        return
    await callback.message.edit_text(
        "Выберите ученика для удаления:", reply_markup=kb_student_list(students, "del_student")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("del_student:"))
async def cb_delete_student_confirm(
    callback: CallbackQuery, user: User | None, student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"Удалить ученика «{student.name}» ({student_id})?",
        reply_markup=kb_confirm(f"confirm_del_student:{student_id}", "admin:students"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_del_student:"))
async def cb_delete_student_do(
    callback: CallbackQuery,
    user: User | None,
    student_repo: StudentRepository,
    ts_repo: TeacherStudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    for ts in await ts_repo.get_all():
        if ts.student_id == student_id:
            await ts_repo.remove(ts.teacher_id, student_id)
    ok = await student_repo.delete(student_id)
    text = f"Ученик {student_id} удалён (и все его связи с педагогами)." if ok else "Ученик не найден."
    await callback.message.edit_text(text, reply_markup=kb_back("admin:students"))
    await callback.answer()


# ─── Привязка ученика к педагогу ──────────────────────────────────────────────

@router.callback_query(F.data == "students:link")
async def cb_link_start(
    callback: CallbackQuery, user: User | None, state: FSMContext, teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teachers = await teacher_repo.get_all()
    if not teachers:
        await callback.message.edit_text("Педагогов нет.", reply_markup=kb_back("admin:students"))
        await callback.answer()
        return
    await state.set_state(LinkTeacherStudentStates.choosing_teacher)
    await callback.message.edit_text(
        "Выберите педагога:", reply_markup=kb_teacher_list(teachers, "link_teacher")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("link_teacher:"), LinkTeacherStudentStates.choosing_teacher)
async def cb_link_teacher_chosen(
    callback: CallbackQuery, state: FSMContext, student_repo: StudentRepository,
) -> None:
    await state.update_data(teacher_id=callback.data.split(":", 1)[1])
    await state.set_state(LinkTeacherStudentStates.choosing_student)
    students = await student_repo.get_all()
    if not students:
        await callback.message.edit_text("Учеников нет.", reply_markup=kb_back("admin:students"))
        await callback.answer()
        return
    await callback.message.edit_text(
        "Выберите ученика для привязки:", reply_markup=kb_student_list(students, "link_student")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("link_student:"), LinkTeacherStudentStates.choosing_student)
async def cb_link_student_chosen(
    callback: CallbackQuery,
    state: FSMContext,
    teacher_repo: TeacherRepository,
    student_repo: StudentRepository,
) -> None:
    student_id = callback.data.split(":", 1)[1]
    data = await state.get_data()
    teacher = await teacher_repo.get_by_id(data["teacher_id"])
    student = await student_repo.get_by_id(student_id)
    await state.update_data(student_id=student_id)
    await state.set_state(LinkTeacherStudentStates.confirming)
    await callback.message.edit_text(
        f"Привязать «{student.name if student else student_id}» "
        f"к педагогу «{teacher.name if teacher else data['teacher_id']}»?",
        reply_markup=kb_confirm("confirm_link_ts", "admin:students"),
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_link_ts")
async def cb_confirm_link(
    callback: CallbackQuery, state: FSMContext, user: User | None, ts_repo: TeacherStudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    await state.clear()
    teacher_id, student_id = data["teacher_id"], data["student_id"]
    if await ts_repo.exists(teacher_id, student_id):
        await callback.message.edit_text("Такая связь уже существует.", reply_markup=kb_back("admin:students"))
    else:
        await ts_repo.add(teacher_id, student_id)
        await callback.message.edit_text(
            f"Связь создана: {teacher_id} ↔ {student_id}.", reply_markup=kb_back("admin:students")
        )
    await callback.answer()


# ─── Удаление связи teacher_students ─────────────────────────────────────────

@router.callback_query(F.data == "students:unlink")
async def cb_unlink_start(
    callback: CallbackQuery, user: User | None, state: FSMContext, teacher_repo: TeacherRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    teachers = await teacher_repo.get_all()
    if not teachers:
        await callback.message.edit_text("Педагогов нет.", reply_markup=kb_back("admin:students"))
        await callback.answer()
        return
    await state.set_state(LinkTeacherStudentStates.choosing_teacher)
    await state.update_data(action="unlink")
    await callback.message.edit_text(
        "Выберите педагога для удаления связи:", reply_markup=kb_teacher_list(teachers, "unlink_teacher")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("unlink_teacher:"))
async def cb_unlink_teacher_chosen(
    callback: CallbackQuery,
    state: FSMContext,
    ts_repo: TeacherStudentRepository,
    student_repo: StudentRepository,
) -> None:
    teacher_id = callback.data.split(":", 1)[1]
    await state.update_data(teacher_id=teacher_id)
    student_ids = await ts_repo.get_students_for_teacher(teacher_id)
    if not student_ids:
        await callback.message.edit_text(
            "У педагога нет привязанных учеников.", reply_markup=kb_back("admin:students")
        )
        await callback.answer()
        return
    all_students = await student_repo.get_all()
    students = [s for s in all_students if s.student_id in student_ids]
    await state.set_state(LinkTeacherStudentStates.choosing_student)
    await callback.message.edit_text(
        "Выберите ученика для удаления связи:", reply_markup=kb_student_list(students, "unlink_student")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("unlink_student:"))
async def cb_unlink_student_do(
    callback: CallbackQuery, user: User | None, state: FSMContext, ts_repo: TeacherStudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 1)[1]
    data = await state.get_data()
    await state.clear()
    ok = await ts_repo.remove(data.get("teacher_id", ""), student_id)
    text = f"Связь удалена." if ok else "Связь не найдена."
    await callback.message.edit_text(text, reply_markup=kb_back("admin:students"))
    await callback.answer()
