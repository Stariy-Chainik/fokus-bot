from __future__ import annotations
import logging
from datetime import date

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.models.enums import LessonType
from bot.repositories import StudentRepository, LessonRepository
from config.settings import settings
from bot.keyboards.client import (
    kb_client_student_select, kb_lessons_period_select,
    kb_lessons_month_list, kb_lessons_back, kb_lessons_month_filter,
)
from bot.keyboards.calendar import kb_calendar
from bot.services import PaymentService
from bot.services.parent_views import history_hidden
from bot.utils.dates import format_date_short_with_wd, display_period

from bot.utils.callbacks import (
    ClientCalNavCb,
    ClientCalPickCb,
    ClientCalendarCb,
    ClientDateCb,
    ClientMonthCb,
    ClientMonthListCb,
    ClientMonthTeacherCb,
    ClientStudentCb,
)
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
    payment_service: PaymentService,
    period_str: str,
    student_id: str = "all",
    teacher_filter: str = "all",
) -> None:
    if history_hidden(period_str):                 # история родителя — с сентября 2026
        await callback.answer("Занятия показываются с сентября 2026", show_alert=True)
        return
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
    unpaid_total = 0
    seen_teachers: dict[str, str] = {}  # педагоги платных занятий — для фильтра

    for student in students:
        # Отметки оплаты считаются по всему месяцу (оплаты накопительные), даже если показываем день
        month = await payment_service.student_lesson_marks(student.student_id, period_str[:7])
        lessons = month.lessons if is_month else [ls for ls in month.lessons if ls.date == period_str]
        if not lessons:
            continue

        lines.append(f"\n<b>{student.name}:</b>")

        for ls in lessons:
            teacher_short = _short_name(ls.teacher_name)
            amount = month.mark(ls.lesson_id).amount

            # Групповые занятия без тарификации (абонемент/NONE) не показываем
            if ls.type == LessonType.GROUP and amount == 0:
                continue

            seen_teachers.setdefault(ls.teacher_id, teacher_short)
            if teacher_filter != "all" and ls.teacher_id != teacher_filter:
                continue

            total_lessons += 1
            date_prefix = f"{format_date_short_with_wd(ls.date)} — " if is_month else ""
            if ls.type == LessonType.GROUP and ls.group_id in settings.revenue_share_group_map:
                type_tag = " (индивид.)"  # индивидуальные Яковлевой хранятся как GROUP
            elif ls.type == LessonType.GROUP:
                type_tag = " (группа)"
            else:
                type_tag = ""
            direct = (ls.type == LessonType.INDIVIDUAL
                      and ls.teacher_id in settings.direct_pay_teacher_id_set)
            if direct:
                amount_part = " · оплата педагогу напрямую"
                paid_icon = ""
            else:
                amount_part = f" · {amount} ₽" if amount > 0 else ""
                if amount > 0:
                    if month.mark(ls.lesson_id).paid:
                        paid_icon = " ✅"
                    else:
                        paid_icon = " ⬜"
                        unpaid_total += amount
                else:
                    paid_icon = ""
            if ls.type == LessonType.GROUP and ls.group_id in settings.revenue_share_group_map:
                dur_part = ""  # длительность индивидуальных на цену не влияет
            else:
                dur_part = f" — {ls.duration_min} мин"
            lines.append(
                f"  • {date_prefix}{teacher_short}{type_tag}{dur_part}{amount_part}{paid_icon}"
            )

    if total_lessons == 0:
        lines.append("\nЗанятий нет.")
    elif unpaid_total > 0:
        lines.append(f"\n⬜ Не оплачено: <b>{unpaid_total} ₽</b>")
        if student_id == "all":
            lines.append("<i>Оплата — в разделе «💳 Оплата занятий».</i>")
    elif is_month:
        lines.append("\n✅ Все занятия оплачены")

    if is_month:
        teachers = sorted(seen_teachers.items(), key=lambda x: x[1])
        kb = kb_lessons_month_filter(
            student_id, period_str, teachers, active=teacher_filter, pay_amount=unpaid_total,
        )
    else:
        kb = kb_lessons_back(student_id)
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)


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
    student_id = ClientStudentCb.unpack(callback.data).student_id
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
    payment_service: PaymentService,
) -> None:
    # cl_date:{student_id}:{date}
    cb = ClientDateCb.unpack(callback.data)
    student_id, period_str = cb.student_id, cb.date
    await _show_lessons(callback, student_repo, payment_service, period_str, student_id)
    await callback.answer()


async def _lesson_dates_for_student(
    lesson_repo: LessonRepository,
    students: list,
    student_id: str,
    year: int,
    month: int,
) -> set[date]:
    period = f"{year}-{month:02d}"
    target = students if student_id == "all" else [s for s in students if s.student_id == student_id]
    result: set[date] = set()
    for s in target:
        for ls in await lesson_repo.get_by_student_and_period(s.student_id, period):
            try:
                result.add(date.fromisoformat(ls.date))
            except ValueError:
                pass
    return result


@router.callback_query(F.data.startswith("cl_calendar_s:"))
async def cb_cl_calendar_s(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    lesson_repo: LessonRepository,
    state: FSMContext,
) -> None:
    student_id = ClientCalendarCb.unpack(callback.data).student_id
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not students:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.update_data(cl_student_id=student_id)
    today = date.today()
    highlights = await _lesson_dates_for_student(lesson_repo, students, student_id, today.year, today.month)
    await callback.message.edit_text(
        "Выберите дату:",
        reply_markup=kb_calendar(today.year, today.month, prefix="cl",
                                 cancel_cb=f"cl_stu:{student_id}", highlight_dates=highlights),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cl_nav:"))
async def cb_cl_nav(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    lesson_repo: LessonRepository,
    state: FSMContext,
) -> None:
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not students:
        await callback.answer("Нет доступа", show_alert=True)
        return
    ym = ClientCalNavCb.unpack(callback.data).ym
    if history_hidden(ym):
        await callback.answer("Раньше сентября 2026 занятий в кабинете нет")
        return
    year, month = (int(x) for x in ym.split("-"))
    data = await state.get_data()
    student_id = data.get("cl_student_id", "all")
    highlights = await _lesson_dates_for_student(lesson_repo, students, student_id, year, month)
    await callback.message.edit_reply_markup(
        reply_markup=kb_calendar(year, month, prefix="cl",
                                 cancel_cb=f"cl_stu:{student_id}", highlight_dates=highlights),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cl_pick:"))
async def cb_cl_pick(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    payment_service: PaymentService,
    state: FSMContext,
) -> None:
    period_str = ClientCalPickCb.unpack(callback.data).date
    data = await state.get_data()
    student_id = data.get("cl_student_id", "all")
    await _show_lessons(callback, student_repo, payment_service, period_str, student_id)
    await callback.answer()


@router.callback_query(F.data.startswith("cl_month_list_s:"))
async def cb_cl_month_list_s(
    callback: CallbackQuery, student_repo: StudentRepository,
) -> None:
    student_id = ClientMonthListCb.unpack(callback.data).student_id
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not students:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "Выберите месяц:",
        reply_markup=kb_lessons_month_list(student_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cl_month_t:"))
async def cb_cl_month_teacher(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    payment_service: PaymentService,
) -> None:
    # cl_month_t:{student_id}:{ym}:{teacher_id|all}
    cb = ClientMonthTeacherCb.unpack(callback.data)
    student_id, period_str, teacher_id = cb.student_id, cb.period, cb.teacher_id
    await _show_lessons(
        callback, student_repo, payment_service,
        period_str, student_id, teacher_filter=teacher_id,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cl_month:"))
async def cb_cl_month(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    payment_service: PaymentService,
) -> None:
    # cl_month:{student_id}:{ym}
    cb = ClientMonthCb.unpack(callback.data)
    student_id, period_str = cb.student_id, cb.period
    await _show_lessons(callback, student_repo, payment_service, period_str, student_id)
    await callback.answer()
