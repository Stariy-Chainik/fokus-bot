import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import LessonRepository
from bot.services import LessonService
from bot.keyboards.teacher import kb_lesson_list, kb_lesson_detail, kb_teacher_menu
from bot.utils.dates import format_date_display

logger = logging.getLogger(__name__)
router = Router(name="teacher_my_lessons")
PAGE_SIZE = 5


def _is_teacher(user: User | None) -> bool:
    return user is not None and user.teacher_id is not None


@router.callback_query(F.data == "teacher:my_lessons")
async def cb_my_lessons(
    callback: CallbackQuery, user: User | None, lesson_repo: LessonRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lessons = await lesson_repo.get_by_teacher(user.teacher_id)
    lessons.sort(key=lambda ls: ls.date, reverse=True)
    if not lessons:
        await callback.message.edit_text("У вас ещё нет занятий.", reply_markup=kb_teacher_menu())
        await callback.answer()
        return
    await callback.message.edit_text(
        f"Ваши занятия ({len(lessons)}):",
        reply_markup=kb_lesson_list(lessons, page=0, page_size=PAGE_SIZE),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lessons_page:"))
async def cb_lessons_page(
    callback: CallbackQuery, user: User | None, lesson_repo: LessonRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    page = int(callback.data.split(":", 1)[1])
    lessons = await lesson_repo.get_by_teacher(user.teacher_id)
    lessons.sort(key=lambda ls: ls.date, reverse=True)
    await callback.message.edit_reply_markup(
        reply_markup=kb_lesson_list(lessons, page=page, page_size=PAGE_SIZE)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lesson_detail:"))
async def cb_lesson_detail(
    callback: CallbackQuery, user: User | None, lesson_repo: LessonRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lesson_id = callback.data.split(":", 1)[1]
    lesson = await lesson_repo.get_by_id(lesson_id)
    if not lesson or lesson.teacher_id != user.teacher_id:
        await callback.answer("Занятие не найдено", show_alert=True)
        return

    lines = [
        f"Занятие {lesson.lesson_id}",
        f"Дата: {format_date_display(lesson.date)}",
        f"Тип: {'Групповое' if lesson.type.value == 'group' else 'Индивидуальное'}",
        f"Длительность: {lesson.duration_min} мин",
        f"Начислено: {lesson.earned} руб.",
    ]
    if lesson.student_1_name:
        lines.append(f"Ученик 1: {lesson.student_1_name}")
    if lesson.student_2_name:
        lines.append(f"Ученик 2: {lesson.student_2_name}")

    await callback.message.edit_text("\n".join(lines), reply_markup=kb_lesson_detail(lesson_id))
    await callback.answer()


@router.callback_query(F.data.startswith("delete_lesson:"))
async def cb_delete_lesson_confirm(
    callback: CallbackQuery, user: User | None, lesson_repo: LessonRepository,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lesson_id = callback.data.split(":", 1)[1]
    lesson = await lesson_repo.get_by_id(lesson_id)
    if not lesson or lesson.teacher_id != user.teacher_id:
        await callback.answer("Занятие не найдено", show_alert=True)
        return
    await callback.message.edit_text(
        f"Удалить занятие {lesson_id} от {format_date_display(lesson.date)}?\n"
        "Связанные billing-записи тоже будут удалены.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"confirm_delete_lesson:{lesson_id}")],
            [InlineKeyboardButton(text="« Назад", callback_data=f"lesson_detail:{lesson_id}")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_delete_lesson:"))
async def cb_delete_lesson_do(
    callback: CallbackQuery, user: User | None,
    lesson_repo: LessonRepository, lesson_service: LessonService,
) -> None:
    if not _is_teacher(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    lesson_id = callback.data.split(":", 1)[1]
    lesson = await lesson_repo.get_by_id(lesson_id)
    if lesson and lesson.teacher_id != user.teacher_id:
        await callback.answer("Нет доступа к этому занятию", show_alert=True)
        return
    ok = await lesson_service.delete(lesson_id)
    text = f"Занятие {lesson_id} удалено." if ok else "Занятие не найдено."
    await callback.message.edit_text(text, reply_markup=kb_teacher_menu())
    await callback.answer()
