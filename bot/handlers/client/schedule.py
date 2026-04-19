from __future__ import annotations
import logging
from datetime import date

from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot.models import User
from bot.models.enums import LessonType
from bot.repositories import LessonRepository, StudentRepository
from bot.keyboards import kb_client_periods, kb_client_schedule_back
from bot.utils.dates import display_period, format_date_display

logger = logging.getLogger(__name__)
router = Router(name="client_schedule")


def _is_client(user: User | None) -> bool:
    return user is not None and bool(user.student_id)


def _recent_periods(n: int = 6) -> list[tuple[str, str]]:
    from dateutil.relativedelta import relativedelta  # type: ignore
    today = date.today()
    periods = [(today - relativedelta(months=i)).strftime("%Y-%m") for i in range(n)]
    return [(p, display_period(p)) for p in periods]


@router.callback_query(F.data == "client:schedule")
async def cb_client_schedule_periods(callback: CallbackQuery, user: User | None) -> None:
    if not _is_client(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "📅 <b>Моё расписание</b>\n\nВыберите период:",
        reply_markup=kb_client_periods(_recent_periods()),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("client_period:"))
async def cb_client_schedule_view(
    callback: CallbackQuery,
    user: User | None,
    lesson_repo: LessonRepository,
    student_repo: StudentRepository,
) -> None:
    if not _is_client(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    period = callback.data.split(":", 1)[1]
    student_id = user.student_id

    lessons = await lesson_repo.get_by_student_and_period_all(student_id, period)
    lessons.sort(key=lambda ls: (ls.date, ls.lesson_id))

    if not lessons:
        await callback.message.edit_text(
            f"За {display_period(period)} занятий нет.",
            reply_markup=kb_client_schedule_back(),
        )
        await callback.answer()
        return

    # Подгружаем имена партнёров для парных занятий, если их нет в denorm-snapshot.
    students_by_id: dict[str, str] = {}

    async def _name(sid: str | None) -> str:
        if not sid:
            return "—"
        if sid in students_by_id:
            return students_by_id[sid]
        s = await student_repo.get_by_id(sid)
        nm = s.name if s else sid
        students_by_id[sid] = nm
        return nm

    lines = [f"📅 <b>Расписание — {display_period(period)}</b>", ""]
    for ls in lessons:
        if ls.type == LessonType.GROUP:
            kind = "👥 Группа"
        elif ls.student_2_id and ls.student_2_id != student_id and ls.student_1_id != student_id:
            # Редкий случай: ученик попал через attendees, а не через пару.
            kind = "👤 Индивидуальное"
        elif ls.student_2_id:
            partner_id = (
                ls.student_2_id if ls.student_1_id == student_id else ls.student_1_id
            )
            partner_name = (
                ls.student_2_name if ls.student_1_id == student_id else ls.student_1_name
            )
            if not partner_name and partner_id:
                partner_name = await _name(partner_id)
            kind = f"💃 Пара с {partner_name or '—'}"
        else:
            kind = "👤 Индивидуальное"
        lines.append(
            f"{format_date_display(ls.date)} · {ls.duration_min} мин · {kind}\n"
            f"  Педагог: {ls.teacher_name}"
        )
    lines.append("")
    lines.append(f"Всего занятий: {len(lessons)}")

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=kb_client_schedule_back(),
    )
    await callback.answer()
