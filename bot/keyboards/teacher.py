from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

PAGE_SIZE = 8  # кол-во учеников на странице при листании


def kb_teacher_menu(can_switch_role: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✏️ Отметить занятие", callback_data="teacher:record_lesson")],
        [InlineKeyboardButton(text="💃 Мои пары", callback_data="teacher:my_pairs")],
        [InlineKeyboardButton(text="🎯 Мои солисты", callback_data="teacher:my_soloists")],
        [InlineKeyboardButton(text="🏢 Мои группы", callback_data="teacher:my_groups")],
        [InlineKeyboardButton(text="📋 Мои занятия", callback_data="teacher:my_lessons")],
        [InlineKeyboardButton(text="📊 Моя статистика", callback_data="teacher:my_stats")],
        [InlineKeyboardButton(text="📤 Сдать период", callback_data="teacher:submit_period")],
    ]
    if can_switch_role:
        rows.append([InlineKeyboardButton(text="🔄 Режим администратора", callback_data="mode:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_my_student_card(
    student_id: str, has_partner: bool, can_manage: bool,
    back_cb: str = "teacher:my_soloists",
) -> InlineKeyboardMarkup:
    """
    Карточка ученика в интерфейсе педагога.
    Создание/изменение/снятие пары — через экран «Мои пары».
    can_manage оставлен для совместимости сигнатуры.
    """
    _ = has_partner, can_manage
    rows = [
        [InlineKeyboardButton(text="✏️ Изменить имя", callback_data=f"t_rename_student:{student_id}")],
        [InlineKeyboardButton(text="« Назад", callback_data=back_cb)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_my_pair_card(student_id: str) -> InlineKeyboardMarkup:
    """Карточка пары (открыта из списка «Мои пары»)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Изменить партнёра", callback_data=f"t_partner_assign:{student_id}")],
        [InlineKeyboardButton(text="❌ Убрать партнёра", callback_data=f"t_partner_clear:{student_id}")],
        [InlineKeyboardButton(text="« Назад к парам", callback_data="teacher:my_pairs")],
    ])


def kb_t_partner_candidates(
    candidates: list, student_id: str, cancel_cb: str | None = None,
) -> InlineKeyboardMarkup:
    """candidates: list[tuple[Student, bool has_partner]].
    cancel_cb — куда ведёт «Отмена» (по умолчанию — карточка ученика)."""
    buttons = []
    for s, has_partner in candidates:
        label = f"⚠️ {s.name}" if has_partner else s.name
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"t_partner_pick:{s.student_id}")])
    buttons.append([InlineKeyboardButton(
        text="« Отмена", callback_data=cancel_cb or f"t_student_card:{student_id}",
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_t_confirm(
    confirm_cb: str, cancel_cb: str, confirm_text: str = "✅ Подтвердить",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=confirm_text, callback_data=confirm_cb),
            InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_cb),
        ]
    ])


def kb_lesson_type() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Групповое", callback_data="lesson_kind:group")],
        [InlineKeyboardButton(text="💃 Парное", callback_data="lesson_kind:pair")],
        [InlineKeyboardButton(text="👤 Индивидуальное (соло)", callback_data="lesson_kind:soloist")],
        [
            InlineKeyboardButton(text="« Назад", callback_data="lesson_back:date"),
            InlineKeyboardButton(text="« Отмена", callback_data="teacher:cancel_lesson"),
        ],
    ])


def kb_lesson_type_after_save() -> InlineKeyboardMarkup:
    """После успешного сохранения: продолжить с тем же днём или выйти в меню."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Групповое", callback_data="lesson_kind:group")],
        [InlineKeyboardButton(text="💃 Парное", callback_data="lesson_kind:pair")],
        [InlineKeyboardButton(text="👤 Индивидуальное (соло)", callback_data="lesson_kind:soloist")],
        [InlineKeyboardButton(text="« В меню", callback_data="teacher:menu")],
    ])


def kb_attendance_yes_no() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Отметить", callback_data="attendance:yes"),
            InlineKeyboardButton(text="⏭ Пропустить", callback_data="attendance:no"),
        ],
        [
            InlineKeyboardButton(text="« Назад", callback_data="lesson_back:group"),
            InlineKeyboardButton(text="« Отмена", callback_data="teacher:cancel_lesson"),
        ],
    ])


def kb_pair_multi_select(pairs: list, selected_keys: set, back_cb: str = "lesson_back:duration") -> InlineKeyboardMarkup:
    """pairs: list[tuple[Student a, Student b]]. Чекбоксы для мульти-выбора пар.
    Ключ — a.student_id (партнёр определяется по partner_id)."""
    rows = []
    for a, b in pairs:
        mark = "✅" if a.student_id in selected_keys else "⬜"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {a.name} ↔ {b.name}",
            callback_data=f"pair_toggle:{a.student_id}",
        )])
    rows.append([
        InlineKeyboardButton(text=f"💾 Подтвердить ({len(selected_keys)})", callback_data="pair_confirm"),
    ])
    rows.append([
        InlineKeyboardButton(text="« Назад", callback_data=back_cb),
        InlineKeyboardButton(text="❌ Отмена", callback_data="teacher:cancel_lesson"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_multi_select(
    students: list, selected_ids: set, back_cb: str = "lesson_back:kind",
    show_toggle_all: bool = False,
) -> InlineKeyboardMarkup:
    """Чекбоксы со списком учеников. selected_ids — set[str] выбранных."""
    rows = []
    for s in students:
        mark = "✅" if s.student_id in selected_ids else "⬜"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {s.name}", callback_data=f"ms_toggle:{s.student_id}"
        )])
    if show_toggle_all:
        all_selected = students and len(selected_ids) == len(students)
        toggle_all_text = "◻️ Снять всех" if all_selected else "☑️ Отметить всех"
        rows.append([InlineKeyboardButton(text=toggle_all_text, callback_data="ms_all")])
    rows.append([
        InlineKeyboardButton(text=f"💾 Подтвердить ({len(selected_ids)})", callback_data="ms_confirm"),
    ])
    rows.append([
        InlineKeyboardButton(text="« Назад", callback_data=back_cb),
        InlineKeyboardButton(text="❌ Отмена", callback_data="teacher:cancel_lesson"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_group_branch_picker(branches: list, back_cb: str = "lesson_back:duration") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🏢 {b.name}", callback_data=f"group_branch:{b.branch_id}")]
        for b in branches
    ]
    rows.append([
        InlineKeyboardButton(text="« Назад", callback_data=back_cb),
        InlineKeyboardButton(text="❌ Отмена", callback_data="teacher:cancel_lesson"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_group_picker(groups: list, back_cb: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"💃 {g.name}", callback_data=f"group_pick:{g.group_id}")]
        for g in groups
    ]
    rows.append([
        InlineKeyboardButton(text="« Назад", callback_data=back_cb),
        InlineKeyboardButton(text="❌ Отмена", callback_data="teacher:cancel_lesson"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_duration(back_cb: str = "lesson_back:kind") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="35 мин", callback_data="duration:35"),
            InlineKeyboardButton(text="45 мин", callback_data="duration:45"),
            InlineKeyboardButton(text="60 мин", callback_data="duration:60"),
            InlineKeyboardButton(text="90 мин", callback_data="duration:90"),
        ],
        [
            InlineKeyboardButton(text="« Назад", callback_data=back_cb),
            InlineKeyboardButton(text="« Отмена", callback_data="teacher:cancel_lesson"),
        ],
    ])


def kb_yes_no(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да", callback_data=yes_cb),
            InlineKeyboardButton(text="❌ Нет", callback_data=no_cb),
        ]
    ])


def kb_student_search_results(
    students: list,
    action_prefix: str,
    page: int = 0,
    total: int = 0,
) -> InlineKeyboardMarkup:
    """
    Клавиатура результатов поиска/просмотра учеников.
    Поддерживает пагинацию: page — текущая страница (0-based).
    """
    buttons = [
        [InlineKeyboardButton(text=s.name, callback_data=f"{action_prefix}:{s.student_id}")]
        for s in students
    ]

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="← Пред.", callback_data=f"page:{action_prefix}:{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav_row.append(InlineKeyboardButton(text="След. →", callback_data=f"page:{action_prefix}:{page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="« Отмена", callback_data="teacher:cancel_lesson")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_lesson_list(
    lessons: list, page: int = 0, page_size: int = 10,
    locked_ids: set | None = None,
    filter_date: str | None = None,
    filter_month: str | None = None,
    back_cb: str = "teacher:lesson_delete",
) -> InlineKeyboardMarkup:
    """Список занятий педагога с пагинацией. locked_ids — занятия из сданного периода.
    filter_date/filter_month сохраняются в callback пагинации, чтобы не терять фильтр при листании."""
    from bot.utils.dates import format_date_display
    locked_ids = locked_ids or set()
    start = page * page_size
    page_lessons = lessons[start: start + page_size]
    if filter_date:
        filter_tag = filter_date
    elif filter_month:
        filter_tag = f"m-{filter_month}"
    else:
        filter_tag = "all"

    from bot.models.enums import LessonType
    buttons = []
    for ls in page_lessons:
        date_display = format_date_display(ls.date)
        lock_icon = "🔒 " if ls.lesson_id in locked_ids else ""
        if ls.type == LessonType.GROUP:
            who = "группа"
        elif ls.student_2_name:
            who = f"{ls.student_1_name} ↔ {ls.student_2_name}"
        else:
            who = ls.student_1_name or "—"
        label = f"{lock_icon}{date_display} | {ls.duration_min}м | {who}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"lesson_detail:{ls.lesson_id}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(
            text="← Пред.", callback_data=f"lessons_page:{page - 1}:{filter_tag}",
        ))
    if start + page_size < len(lessons):
        nav_row.append(InlineKeyboardButton(
            text="След. →", callback_data=f"lessons_page:{page + 1}:{filter_tag}",
        ))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_lesson_detail(lesson, locked: bool = False, back_cb: str = "teacher:lesson_delete") -> InlineKeyboardMarkup:
    """Карточка занятия. Правка полей не поддерживается — если педагог ошибся,
    он удаляет занятие и создаёт заново через «Отметить занятие».
    Если locked — период сдан, кнопки удаления нет.
    """
    lesson_id = lesson.lesson_id
    rows: list[list[InlineKeyboardButton]] = []

    if locked:
        rows.append([InlineKeyboardButton(text="🔒 Период сдан", callback_data="noop")])
    else:
        rows.append([InlineKeyboardButton(
            text="🗑 Удалить занятие", callback_data=f"delete_lesson:{lesson_id}",
        )])

    rows.append([InlineKeyboardButton(text="« Назад к списку", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
