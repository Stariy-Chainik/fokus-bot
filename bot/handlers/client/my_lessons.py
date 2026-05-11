from __future__ import annotations
import logging
from datetime import date

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.models.enums import LessonType, PaymentStatus
from bot.repositories import StudentRepository, LessonRepository, TeacherRepository
from bot.repositories.payment_repo import PaymentRepository
from bot.keyboards.client import (
    kb_client_student_select, kb_lessons_period_select,
    kb_lessons_month_list, kb_lessons_back,
)
from bot.keyboards.calendar import kb_calendar
from bot.services.billing_service import build_billing_rows
from bot.utils.dates import format_date_short_with_wd, display_period

logger = logging.getLogger(__name__)
router = Router(name="client_lessons")


def _short_name(full_name: str) -> str:
    parts = full_name.split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1][0]}."
    return full_name


async def _show_lessons(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    lesson_repo: LessonRepository,
    teacher_repo: TeacherRepository,
    payment_repo: PaymentRepository,
    period_str: str,
    student_id: str = "all",
) -> None:
    all_students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not all_students:
        await callback.answer("Нет доступа", show_alert=True)
        return

    if student_id != "all":
        students = [s for s in all_students if s.student_id == student_id]
        if not students:
            await callback.answer("Ученик не найден", show_alert=True)
            return
    else:
        students = all_students

    is_month = len(period_str) == 7
    title = (
        f"📅 Занятия за {display_period(period_str)}"
        if is_month
        else f"📅 Занятия за {format_date_short_with_wd(period_str)}"
    )
    lines: list[str] = [f"<b>{title}</b>"]
    total_lessons = 0

    for student in students:
        lessons = sorted(
            await lesson_repo.get_by_student_and_period(student.student_id, period_str),
            key=lambda ls: ls.date,
        )
        if not lessons:
            continue

        # Загружаем статусы оплаты по уникальным периодам из занятий
        periods_in_view = {ls.date[:7] for ls in lessons}
        paid_keys: set[tuple[str, str]] = set()
        for pm in periods_in_view:
            for pay in await payment_repo.get_by_student_and_period(student.student_id, pm):
                if pay.status == PaymentStatus.PAID:
                    paid_keys.add((pm, pay.teacher_id))

        lines.append(f"\n<b>{student.name}:</b>")

        for ls in lessons:
            teacher = await teacher_repo.get_by_id(ls.teacher_id)
            teacher_short = _short_name(ls.teacher_name)

            amount = 0
            if teacher:
                for row in build_billing_rows(ls, teacher):
                    if row.student_id == student.student_id:
                        amount += row.amount

            # Групповые занятия без тарификации (абонемент/NONE) не показываем
            if ls.type == LessonType.GROUP and amount == 0:
                continue

            total_lessons += 1
            date_prefix = f"{format_date_short_with_wd(ls.date)} — " if is_month else ""
            type_tag = " (группа)" if ls.type == LessonType.GROUP else ""
            amount_part = f" · {amount} ₽" if amount > 0 else ""
            paid_icon = " ✅" if (ls.date[:7], ls.teacher_id) in paid_keys else ""
            lines.append(
                f"  • {date_prefix}{teacher_short}{type_tag} — {ls.duration_min} мин{amount_part}{paid_icon}"
            )

    if total_lessons == 0:
        lines.append("\nЗанятий нет.")

    await callback.message.edit_text("\n".join(lines), reply_markup=kb_lessons_back(student_id))


@router.callback_query(F.data == "client:lessons")
async def cb_client_lessons(
    callback: CallbackQuery, student_repo: StudentRepository,
) -> None:
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not students:
        await callback.answer("Нет доступа", show_alert=True)
        return
    if len(students) > 1:
        await callback.message.edit_text(
            "Выберите ученика:",
            reply_markup=kb_client_student_select(students, "lessons"),
        )
    else:
        await callback.message.edit_text(
            "Выберите период:",
            reply_markup=kb_lessons_period_select(students[0].student_id),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("cl_stu:"))
async def cb_cl_stu(
    callback: CallbackQuery, student_repo: StudentRepository,
) -> None:
    student_id = callback.data.split(":", 1)[1]
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not students:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "Выберите период:",
        reply_markup=kb_lessons_period_select(student_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cl_date:"))
async def cb_cl_date(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    lesson_repo: LessonRepository,
    teacher_repo: TeacherRepository,
    payment_repo: PaymentRepository,
) -> None:
    # cl_date:{student_id}:{date}
    _, student_id, period_str = callback.data.split(":", 2)
    await _show_lessons(callback, student_repo, lesson_repo, teacher_repo, payment_repo, period_str, student_id)
    await callback.answer()


@router.callback_query(F.data.startswith("cl_calendar_s:"))
async def cb_cl_calendar_s(
    callback: CallbackQuery, student_repo: StudentRepository, state: FSMContext,
) -> None:
    student_id = callback.data.split(":", 1)[1]
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not students:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.update_data(cl_student_id=student_id)
    today = date.today()
    await callback.message.edit_text(
        "Выберите дату:",
        reply_markup=kb_calendar(today.year, today.month, prefix="cl", cancel_cb=f"cl_stu:{student_id}"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cl_nav:"))
async def cb_cl_nav(
    callback: CallbackQuery, student_repo: StudentRepository, state: FSMContext,
) -> None:
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not students:
        await callback.answer("Нет доступа", show_alert=True)
        return
    ym = callback.data.split(":", 1)[1]
    year, month = (int(x) for x in ym.split("-"))
    data = await state.get_data()
    student_id = data.get("cl_student_id", "all")
    await callback.message.edit_reply_markup(
        reply_markup=kb_calendar(year, month, prefix="cl", cancel_cb=f"cl_stu:{student_id}"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cl_pick:"))
async def cb_cl_pick(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    lesson_repo: LessonRepository,
    teacher_repo: TeacherRepository,
    payment_repo: PaymentRepository,
    state: FSMContext,
) -> None:
    period_str = callback.data.split(":", 1)[1]
    data = await state.get_data()
    student_id = data.get("cl_student_id", "all")
    await _show_lessons(callback, student_repo, lesson_repo, teacher_repo, payment_repo, period_str, student_id)
    await callback.answer()


@router.callback_query(F.data.startswith("cl_month_list_s:"))
async def cb_cl_month_list_s(
    callback: CallbackQuery, student_repo: StudentRepository,
) -> None:
    student_id = callback.data.split(":", 1)[1]
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not students:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "Выберите месяц:",
        reply_markup=kb_lessons_month_list(student_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cl_month:"))
async def cb_cl_month(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    lesson_repo: LessonRepository,
    teacher_repo: TeacherRepository,
    payment_repo: PaymentRepository,
) -> None:
    # cl_month:{student_id}:{ym}
    _, student_id, period_str = callback.data.split(":", 2)
    await _show_lessons(callback, student_repo, lesson_repo, teacher_repo, payment_repo, period_str, student_id)
    await callback.answer()
