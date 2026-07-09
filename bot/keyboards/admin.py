from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def kb_admin_menu(can_switch_role: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="👨‍🏫 Педагоги", callback_data="teachers:list")],
        [InlineKeyboardButton(text="👩‍🎓 Ученики", callback_data="admin:students")],
        [InlineKeyboardButton(text="📝 Заявки", callback_data="admin:requests")],
        [InlineKeyboardButton(text="🏢 Филиалы и группы", callback_data="admin:branches")],
        [InlineKeyboardButton(text="🧾 Счёт ученика за период", callback_data="bills:view")],
        [InlineKeyboardButton(text="⚠️ Должники", callback_data="admin:debtors")],
        [InlineKeyboardButton(text="💾 Подтвердить оплату", callback_data="bills:confirm_payment")],
        [InlineKeyboardButton(text="📝 Отметить занятие", callback_data="admin:record_lesson")],
        [InlineKeyboardButton(text="✏️ Редактировать занятие", callback_data="admin:edit_lesson")],
        [InlineKeyboardButton(text="📊 Прибыль", callback_data="profit:view")],
        [InlineKeyboardButton(text="🔧 Диагностика", callback_data="admin:diagnostics")],
    ]
    if can_switch_role:
        rows.append([InlineKeyboardButton(text="🔄 Режим педагога", callback_data="mode:teacher")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_students_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Поиск ученика", callback_data="students:list")],
        [InlineKeyboardButton(text="💃 Пары", callback_data="students:pairs")],
        [InlineKeyboardButton(text="🎯 Солисты", callback_data="students:soloists")],
        [InlineKeyboardButton(text="➕ Добавить ученика", callback_data="students:add")],
        [InlineKeyboardButton(text="🗑 Удалить ученика", callback_data="students:delete")],
        [InlineKeyboardButton(text="« Назад", callback_data="admin:menu")],
    ])


_STUDENT_PAGE_SIZE = 20


def kb_student_paged(students: list, page: int, total: int) -> InlineKeyboardMarkup:
    """Пагинация поискового списка учеников.
    Сам запрос хранится в FSM-state, а не в callback_data — иначе символы
    вроде ':' / '_' ломают декодирование.
    """
    buttons = [
        [InlineKeyboardButton(text=s.name, callback_data=f"student_card:{s.student_id}")]
        for s in students
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="← Пред.", callback_data=f"spage:{page - 1}"))
    if (page + 1) * _STUDENT_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="След. →", callback_data=f"spage:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="🔍 Новый поиск", callback_data="students:list")])
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="admin:students")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_student_card(
    student_id: str, has_partner: bool, back_cb: str = "students:list",
    tier_toggle: tuple[str, str] | None = None,
    has_groups: bool = False,
    client_rows: list | None = None,
) -> InlineKeyboardMarkup:
    """
    tier_toggle: (current_tier, label) — если задан, добавит кнопку смены тарифа.
    has_groups: если у ученика есть хоть одна группа, показываем кнопку «Убрать из группы».
    client_rows: список строк кнопок управления клиентом [(label, cb), ...] на строку.
    """
    group_row = [InlineKeyboardButton(
        text="➕ Добавить в группу", callback_data=f"student_groups_add:{student_id}",
    )]
    if has_groups:
        group_row.append(InlineKeyboardButton(
            text="➖ Убрать из группы", callback_data=f"student_groups_remove:{student_id}",
        ))
    rows = [group_row]

    partner_label = "🔄 Изменить партнёра" if has_partner else "💃 Назначить партнёра"
    rows.append([InlineKeyboardButton(text=partner_label, callback_data=f"partner_assign:{student_id}")])
    if has_partner:
        rows.append([InlineKeyboardButton(text="❌ Убрать партнёра", callback_data=f"partner_clear:{student_id}")])
    if tier_toggle is not None:
        _, label = tier_toggle
        rows.append([InlineKeyboardButton(text=label, callback_data=f"student_tier_toggle:{student_id}")])
    if client_rows:
        for row_items in client_rows:
            rows.append([InlineKeyboardButton(text=lbl, callback_data=cb) for lbl, cb in row_items])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="go:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_partner_candidates(
    candidates: list, student_id: str, cancel_cb: str | None = None,
) -> InlineKeyboardMarkup:
    """
    candidates: list[tuple[Student, bool]] — ученик и флаг «у него уже есть партнёр».
    cancel_cb — куда ведёт «Отмена» (по умолчанию — карточка ученика).
    """
    buttons = []
    for s, has_partner in candidates:
        label = f"⚠️ {s.name}" if has_partner else s.name
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"partner_pick:{s.student_id}")])
    buttons.append([InlineKeyboardButton(
        text="« Отмена", callback_data=cancel_cb or f"student_card:{student_id}",
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_teacher_list(teachers: list, action_prefix: str, back_cb: str = "admin:menu") -> InlineKeyboardMarkup:
    """Список педагогов для выбора. action_prefix например 'del_teacher' → callback_data='del_teacher:TCH-0001'"""
    buttons = [
        [InlineKeyboardButton(text=t.name, callback_data=f"{action_prefix}:{t.teacher_id}")]
        for t in teachers
    ]
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_teacher_card(teacher_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Занятия педагога", callback_data=f"tc_lessons:{teacher_id}")],
        [InlineKeyboardButton(text="💰 Зарплата за период", callback_data=f"tc_salary:{teacher_id}")],
        [InlineKeyboardButton(text="💵 Выплата за день", callback_data=f"salary_day:{teacher_id}")],
        [InlineKeyboardButton(text="📊 Изменить ставки", callback_data=f"card_edit_rates:{teacher_id}")],
        [InlineKeyboardButton(text="🏢 Изменить группы", callback_data=f"t_edit_groups:{teacher_id}")],
        [InlineKeyboardButton(text="🔓 Открыть период", callback_data=f"open_period_list:{teacher_id}")],
        [InlineKeyboardButton(text="🗑 Удалить педагога", callback_data=f"del_teacher:{teacher_id}")],
        [InlineKeyboardButton(text="« Назад", callback_data="teachers:list")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="go:home")],
    ])


def kb_rate_select(teacher_id: str, rate_group: int, rate_teacher: int, rate_student: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Групповое: {rate_group} руб.", callback_data=f"edit_rate:group:{teacher_id}")],
        [InlineKeyboardButton(text=f"Инд. педагогу: {rate_teacher} руб.", callback_data=f"edit_rate:teacher:{teacher_id}")],
        [InlineKeyboardButton(text=f"Инд. ученику: {rate_student} руб.", callback_data=f"edit_rate:student:{teacher_id}")],
        [InlineKeyboardButton(text="« Отмена", callback_data=f"teacher_card:{teacher_id}")],
    ])


def kb_confirm(
    confirm_cb: str, cancel_cb: str, confirm_text: str = "✅ Подтвердить",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=confirm_text, callback_data=confirm_cb),
            InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_cb),
        ]
    ])


def kb_back(cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад", callback_data=cb)],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="go:home")],
    ])
