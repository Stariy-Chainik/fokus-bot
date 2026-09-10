"""«💸 Выплатить зарплату»: фиксация выплат педагогам по месяцам.

Начислено — SalaryService (занятия + смены SHIFT_GROUPS + корректировки дней,
с историей ставок); выплачено — сумма строк листа teacher_payouts.
Поддерживаются частичные выплаты (аванс + остаток) и «нестандартные дни»
(лист salary_day_overrides): админ задаёт минуты смены за дату.
"""
from __future__ import annotations
import logging
import re
from datetime import date

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.models.enums import LessonType
from bot.repositories import TeacherRepository, LessonRepository, GroupRepository, BranchRepository
from bot.repositories.teacher_payout_repo import TeacherPayoutRepository
from bot.repositories.salary_override_repo import SalaryOverrideRepository
from bot.services.salary_service import SalaryService
from bot.states.admin_states import PayoutStates
from bot.utils.dates import display_period, last_periods
from bot.handlers.access import is_admin as _is_admin

logger = logging.getLogger(__name__)
router = Router(name="admin_payouts")

_PAGE_LINES = 45


def _kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─── Месяц → педагоги ────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin:payouts")
async def cb_payouts_periods(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    rows = [[InlineKeyboardButton(text=display_period(p), callback_data=f"payout_p:{p}")]
            for p in last_periods(4)]
    rows.append([InlineKeyboardButton(text="« Меню", callback_data="admin:menu")])
    await callback.message.edit_text("<b>💸 Выплата зарплаты</b>\nВыберите месяц:", reply_markup=_kb(rows))
    await callback.answer()


async def _render_period(
    callback: CallbackQuery, period: str,
    teacher_repo: TeacherRepository, salary_service: SalaryService, payout_repo: TeacherPayoutRepository,
) -> None:
    teachers = sorted(await teacher_repo.get_all(), key=lambda t: t.name)
    paid_by_teacher: dict[str, int] = {}
    for p in await payout_repo.get_by_period(period):
        paid_by_teacher[p.teacher_id] = paid_by_teacher.get(p.teacher_id, 0) + p.amount
    rows, total_acc, total_paid = [], 0, 0
    for t in teachers:
        acc = await salary_service.total_for(t, period)
        if acc == 0 and t.teacher_id not in paid_by_teacher:
            continue
        paid = paid_by_teacher.get(t.teacher_id, 0)
        total_acc += acc
        total_paid += paid
        icon = "🟢" if paid >= acc else ("🟡" if paid > 0 else "🔴")
        rows.append([InlineKeyboardButton(
            text=f"{icon} {t.name} — {paid}/{acc} ₽", callback_data=f"payout_t:{t.teacher_id}:{period}",
        )])
    rows.append([InlineKeyboardButton(text="« Месяцы", callback_data="admin:payouts")])
    await callback.message.edit_text(
        f"<b>💸 Зарплата за {display_period(period)}</b>\n"
        f"Начислено: {total_acc} ₽ · выплачено: {total_paid} ₽ · остаток: {total_acc - total_paid} ₽\n\n"
        f"🟢 выплачено · 🟡 частично · 🔴 нет. Формат: выплачено/начислено.",
        reply_markup=_kb(rows),
    )


@router.callback_query(F.data.startswith("payout_p:"))
async def cb_payout_period(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository, salary_service: SalaryService, payout_repo: TeacherPayoutRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _render_period(callback, callback.data.split(":", 1)[1], teacher_repo, salary_service, payout_repo)
    await callback.answer()


# ─── Карточка педагога за месяц ──────────────────────────────────────────────

async def _render_teacher(
    target, teacher_id: str, period: str,
    teacher_repo: TeacherRepository, salary_service: SalaryService,
    payout_repo: TeacherPayoutRepository, override_repo: SalaryOverrideRepository,
) -> None:
    teacher = await teacher_repo.get_by_id(teacher_id)
    if not teacher:
        await target.edit_text("Педагог не найден.")
        return
    acc = await salary_service.total_for(teacher, period)
    payouts = await payout_repo.get_by_teacher_period(teacher_id, period)
    paid = sum(p.amount for p in payouts)
    rest = acc - paid
    lines = [
        f"<b>{teacher.name} — {display_period(period)}</b>",
        f"Начислено: <b>{acc} ₽</b>",
        f"Выплачено: <b>{paid} ₽</b>",
        f"Остаток: <b>{rest} ₽</b>",
    ]
    if payouts:
        lines.append("")
        for p in payouts:
            note = f" — {p.comment}" if p.comment else ""
            lines.append(f"  • {p.paid_at[:10]}: {p.amount} ₽{note}")
    overrides = await override_repo.get_for_teacher_period(teacher_id, period)
    rows = []
    if overrides:
        lines.append("\n<b>Нестандартные дни:</b>")
        for o in sorted(overrides, key=lambda x: x.date):
            lines.append(f"  • {o.date[8:10]}.{o.date[5:7]} — {o.minutes} мин{(' — ' + o.comment) if o.comment else ''}")
            rows.append([InlineKeyboardButton(
                text=f"🗑 {o.date[8:10]}.{o.date[5:7]} ({o.minutes} мин)",
                callback_data=f"payout_ovr_del:{o.override_id}:{teacher_id}:{period}",
            )])
    if rest > 0:
        rows.append([InlineKeyboardButton(text=f"✅ Выплатить остаток {rest} ₽",
                                          callback_data=f"payout_all:{teacher_id}:{period}")])
    rows.append([InlineKeyboardButton(text="✏️ Другая сумма (аванс/часть)",
                                      callback_data=f"payout_custom:{teacher_id}:{period}")])
    rows.append([InlineKeyboardButton(text="📋 Расшифровка начисления",
                                      callback_data=f"payout_detail:{teacher_id}:{period}:0")])
    rows.append([InlineKeyboardButton(text="🕒 Нестандартный день",
                                      callback_data=f"payout_ovr:{teacher_id}:{period}")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"payout_p:{period}")])
    await target.edit_text("\n".join(lines), reply_markup=_kb(rows))


@router.callback_query(F.data.startswith("payout_t:"))
async def cb_payout_teacher(
    callback: CallbackQuery, user: User | None, state: FSMContext,
    teacher_repo: TeacherRepository, salary_service: SalaryService,
    payout_repo: TeacherPayoutRepository, salary_override_repo: SalaryOverrideRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    _, teacher_id, period = callback.data.split(":", 2)
    await _render_teacher(callback.message, teacher_id, period, teacher_repo, salary_service, payout_repo, salary_override_repo)
    await callback.answer()


@router.callback_query(F.data.startswith("payout_all:"))
async def cb_payout_all(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository, salary_service: SalaryService,
    payout_repo: TeacherPayoutRepository, salary_override_repo: SalaryOverrideRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, teacher_id, period = callback.data.split(":", 2)
    teacher = await teacher_repo.get_by_id(teacher_id)
    if not teacher:
        await callback.answer("Педагог не найден", show_alert=True)
        return
    acc = await salary_service.total_for(teacher, period)
    paid = sum(p.amount for p in await payout_repo.get_by_teacher_period(teacher_id, period))
    rest = acc - paid
    if rest <= 0:
        await callback.answer("Остатка нет — всё выплачено", show_alert=True)
        return
    await payout_repo.add(teacher_id, period, rest, callback.from_user.id, comment="остаток")
    await _render_teacher(callback.message, teacher_id, period, teacher_repo, salary_service, payout_repo, salary_override_repo)
    await callback.answer(f"Выплата {rest} ₽ записана")


@router.callback_query(F.data.startswith("payout_custom:"))
async def cb_payout_custom(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, teacher_id, period = callback.data.split(":", 2)
    await state.set_state(PayoutStates.waiting_amount)
    await state.update_data(payout_teacher_id=teacher_id, payout_period=period)
    await callback.message.edit_text(
        "Введите сумму выплаты в рублях (целое число):",
        reply_markup=_kb([[InlineKeyboardButton(text="« Отмена", callback_data=f"payout_t:{teacher_id}:{period}")]]),
    )
    await callback.answer()


@router.message(PayoutStates.waiting_amount, F.text)
async def on_payout_amount(
    message: Message, user: User | None, state: FSMContext,
    teacher_repo: TeacherRepository, salary_service: SalaryService,
    payout_repo: TeacherPayoutRepository, salary_override_repo: SalaryOverrideRepository,
) -> None:
    if not _is_admin(user):
        return
    raw = (message.text or "").replace(" ", "")
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("Нужно целое положительное число, например 15000.")
        return
    data = await state.get_data()
    teacher_id, period = data.get("payout_teacher_id"), data.get("payout_period")
    await state.clear()
    if not teacher_id or not period:
        await message.answer("Сессия устарела, откройте выплаты заново.")
        return
    await payout_repo.add(teacher_id, period, int(raw), message.from_user.id)
    sent = await message.answer("✅ Выплата записана.")
    await _render_teacher(sent, teacher_id, period, teacher_repo, salary_service, payout_repo, salary_override_repo)


# ─── Нестандартный день (корректировка минут смены) ─────────────────────────

@router.callback_query(F.data.startswith("payout_ovr:"))
async def cb_payout_override_start(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, teacher_id, period = callback.data.split(":", 2)
    await state.set_state(PayoutStates.waiting_ovr_date)
    await state.update_data(ovr_teacher_id=teacher_id, ovr_period=period)
    await callback.message.edit_text(
        f"🕒 <b>Нестандартный день</b> — {display_period(period)}\n\n"
        "Введите дату (число месяца или ДД.ММ), за которую задать отработанное время.\n"
        "Оно заменит расчёт смены по группам за этот день.",
        reply_markup=_kb([[InlineKeyboardButton(text="« Отмена", callback_data=f"payout_t:{teacher_id}:{period}")]]),
    )
    await callback.answer()


@router.message(PayoutStates.waiting_ovr_date, F.text)
async def on_override_date(message: Message, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user):
        return
    data = await state.get_data()
    period = data.get("ovr_period", "")
    raw = (message.text or "").strip()
    m = re.match(r"^(\d{1,2})(?:\.(\d{1,2}))?$", raw)
    if not m or not period:
        await message.answer("Формат: 15 или 15.09")
        return
    day = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else int(period[5:7])
    try:
        d = date(int(period[:4]), month, day)
    except ValueError:
        await message.answer("Такой даты нет, попробуйте ещё раз.")
        return
    await state.update_data(ovr_date=d.isoformat())
    await state.set_state(PayoutStates.waiting_ovr_minutes)
    await message.answer(
        f"Дата {d.strftime('%d.%m.%Y')}. Сколько минут отработано? (0 — если день не оплачивается; 60, 120, 180…)",
    )


@router.message(PayoutStates.waiting_ovr_minutes, F.text)
async def on_override_minutes(message: Message, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) > 720:
        await message.answer("Введите минуты числом, например 120.")
        return
    await state.update_data(ovr_minutes=int(raw))
    await state.set_state(PayoutStates.waiting_ovr_comment)
    await message.answer("Комментарий (причина) — или «-», чтобы пропустить:")


@router.message(PayoutStates.waiting_ovr_comment, F.text)
async def on_override_comment(
    message: Message, user: User | None, state: FSMContext,
    teacher_repo: TeacherRepository, salary_service: SalaryService,
    payout_repo: TeacherPayoutRepository, salary_override_repo: SalaryOverrideRepository,
) -> None:
    if not _is_admin(user):
        return
    data = await state.get_data()
    await state.clear()
    teacher_id, period = data.get("ovr_teacher_id"), data.get("ovr_period")
    d, minutes = data.get("ovr_date"), data.get("ovr_minutes")
    if not (teacher_id and period and d) or minutes is None:
        await message.answer("Сессия устарела, откройте выплаты заново.")
        return
    comment = "" if (message.text or "").strip() == "-" else (message.text or "").strip()
    await salary_override_repo.add(teacher_id, d, int(minutes), comment, message.from_user.id)
    logger.info("Корректировка дня: %s %s %d мин (%s)", teacher_id, d, minutes, comment)
    sent = await message.answer(f"✅ Записано: {d[8:10]}.{d[5:7]} — {minutes} мин.")
    await _render_teacher(sent, teacher_id, period, teacher_repo, salary_service, payout_repo, salary_override_repo)


@router.callback_query(F.data.startswith("payout_ovr_del:"))
async def cb_override_delete(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository, salary_service: SalaryService,
    payout_repo: TeacherPayoutRepository, salary_override_repo: SalaryOverrideRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, override_id, teacher_id, period = callback.data.split(":", 3)
    await salary_override_repo.delete(override_id)
    await _render_teacher(callback.message, teacher_id, period, teacher_repo, salary_service, payout_repo, salary_override_repo)
    await callback.answer("Корректировка удалена")


# ─── Расшифровка начисления ──────────────────────────────────────────────────

async def _detail_lines(
    teacher, period: str, salary_service: SalaryService,
    lesson_repo: LessonRepository, group_repo: GroupRepository, branch_repo: BranchRepository,
) -> list[str]:
    lines_all = await salary_service.lines_for(teacher, period)
    lessons = {ls.lesson_id: ls for ls in await lesson_repo.get_by_teacher_and_period(teacher.teacher_id, period)}
    groups = {g.group_id: g for g in await group_repo.get_all()}
    branches = {b.branch_id: b.name for b in await branch_repo.get_all()}

    by_branch: dict[str, dict[str, list[tuple[str, int, bool]]]] = {}
    individual: list[tuple[str, int]] = []
    day_lines: list[tuple[str, str, int]] = []
    for ln in lines_all:
        dd = f"{ln.date[8:10]}.{ln.date[5:7]}"
        if ln.kind in ("shift", "override"):
            day_lines.append((dd, ln.label, ln.amount))
            continue
        ls = lessons.get(ln.lesson_id)
        if ls is None:
            continue
        if ls.type == LessonType.GROUP:
            g = groups.get(ls.group_id)
            bname = branches.get(g.branch_id, "—") if g else "—"
            gname = g.name if g else (ls.group_id or "без группы")
            by_branch.setdefault(bname, {}).setdefault(gname, []).append(
                (f"{dd} {ls.duration_min}м", ln.amount, ln.kind == "in_shift"))
        else:
            who = " + ".join(n for n in (ls.student_1_name, ls.student_2_name, ls.student_3_name, ls.student_4_name) if n)
            individual.append((f"{dd} {ls.duration_min}м {who}", ln.amount))

    out: list[str] = []
    for bname in sorted(by_branch):
        out.append(f"🏢 <b>{bname}</b>")
        for gname in sorted(by_branch[bname]):
            items = by_branch[bname][gname]
            gsum = sum(a for _, a, _ in items)
            in_shift = all(s for _, _, s in items)
            out.append(f"  💃 {gname} — {len(items)} зан." + (" (в смене)" if in_shift else f", {gsum} ₽"))
            out.extend(f"     • {t}" + ("" if s else f" — {a} ₽") for t, a, s in items)
    if day_lines:
        out.append("🕒 <b>Смены и корректировки</b>")
        out.extend(f"  • {dd} {label} — {a} ₽" for dd, label, a in day_lines)
    if individual:
        isum = sum(a for _, a in individual)
        out.append(f"👤 <b>Индивидуальные</b> — {len(individual)} зан., {isum} ₽")
        out.extend(f"  • {t} — {a} ₽" for t, a in individual)
    if not out:
        out.append("Занятий нет.")
    out.append(f"\n<b>Итого начислено: {sum(ln.amount for ln in lines_all)} ₽</b>")
    return out


@router.callback_query(F.data.startswith("payout_detail:"))
async def cb_payout_detail(
    callback: CallbackQuery, user: User | None,
    teacher_repo: TeacherRepository, salary_service: SalaryService, lesson_repo: LessonRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, teacher_id, period, page_raw = callback.data.split(":", 3)
    page = int(page_raw) if page_raw.isdigit() else 0
    teacher = await teacher_repo.get_by_id(teacher_id)
    if not teacher:
        await callback.answer("Педагог не найден", show_alert=True)
        return
    lines = await _detail_lines(teacher, period, salary_service, lesson_repo, group_repo, branch_repo)
    pages = [lines[i:i + _PAGE_LINES] for i in range(0, len(lines), _PAGE_LINES)] or [[]]
    page = max(0, min(page, len(pages) - 1))
    head = f"<b>{teacher.name} — {display_period(period)}</b>"
    if len(pages) > 1:
        head += f" (стр. {page + 1}/{len(pages)})"
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="« Пред.", callback_data=f"payout_detail:{teacher_id}:{period}:{page - 1}"))
    if page < len(pages) - 1:
        nav.append(InlineKeyboardButton(text="След. »", callback_data=f"payout_detail:{teacher_id}:{period}:{page + 1}"))
    rows = [nav] if nav else []
    rows.append([InlineKeyboardButton(text="« К выплате", callback_data=f"payout_t:{teacher_id}:{period}")])
    await callback.message.edit_text(head + "\n\n" + "\n".join(pages[page]), reply_markup=_kb(rows))
    await callback.answer()
