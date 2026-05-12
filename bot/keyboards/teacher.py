from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Педагоги, от имени которых может записывать занятия другой педагог (ассистент)
_PROXY_BUTTONS: dict[str, list[tuple[str, str]]] = {
    "TCH-0002": [
        ("📝 Занятие Никишина", "proxy_record:TCH-0005"),
        ("📝 Занятие Криворчук", "proxy_record:TCH-0008"),
    ],
}


def kb_teacher_menu(can_switch_role: bool = False, teacher_id: str | None = None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✏️ Отметить занятие", callback_data="teacher:record_lesson")],
        [InlineKeyboardButton(text="💃 Пары", callback_data="teacher:my_pairs")],
        [InlineKeyboardButton(text="🎯 Солисты", callback_data="teacher:my_soloists")],
        [InlineKeyboardButton(text="🏢 Группы", callback_data="teacher:my_groups")],
        [InlineKeyboardButton(text="📋 Мои занятия", callback_data="teacher:my_lessons")],
        [InlineKeyboardButton(text="📊 Моя статистика", callback_data="teacher:my_stats")],
        [InlineKeyboardButton(text="📤 Сдать период", callback_data="teacher:submit_period")],
    ]
    if teacher_id and teacher_id in _PROXY_BUTTONS:
        for label, cb in _PROXY_BUTTONS[teacher_id]:
            rows.append([InlineKeyboardButton(text=label, callback_data=cb)])
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
        [InlineKeyboardButton(text="📋 Занятия за период", callback_data=f"t_stu_les:{student_id}")],
        [InlineKeyboardButton(text="✏️ Изменить имя", callback_data=f"t_rename_student:{student_id}")],
        [InlineKeyboardButton(text="« Назад", callback_data=back_cb)],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="go:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_my_pair_card(
    student_id: str, partner_id: str,
    back_cb: str = "teacher:my_pairs",
) -> InlineKeyboardMarkup:
    """Карточка пары (открыта из списка «Мои пары»)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Занятия пары", callback_data=f"t_pair_les:{student_id}:{partner_id}")],
        [InlineKeyboardButton(text="🔄 Изменить партнёра", callback_data=f"t_partner_assign:{student_id}")],
        [InlineKeyboardButton(text="❌ Убрать партнёра", callback_data=f"t_partner_clear:{student_id}")],
        [InlineKeyboardButton(text="« Назад", callback_data=back_cb)],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="go:home")],
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


def kb_shared_group_picker(
    groups: list,
    counts: dict,
    total: int,
) -> InlineKeyboardMarkup:
    buttons = []
    for g in groups:
        cnt = counts.get(g.group_id, 0)
        label = f"✓ {g.name} ({cnt})" if cnt > 0 else g.name
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"shared_group:{g.group_id}")])
    if total >= 2:
        buttons.append([InlineKeyboardButton(
            text=f"✅ Подтвердить ({total} выбрано)", callback_data="shared_confirm",
        )])
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="lesson_back:duration")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_lesson_type() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Групповое", callback_data="lesson_kind:group")],
        [InlineKeyboardButton(text="💃 Парное", callback_data="lesson_kind:pair")],
        [InlineKeyboardButton(text="👤 Соло-занятие", callback_data="lesson_kind:soloist")],
        [InlineKeyboardButton(text="🎯 Соло 2 и больше", callback_data="lesson_kind:shared")],
        [
            InlineKeyboardButton(text="« Назад", callback_data="lesson_back:date"),
            InlineKeyboardButton(text="« Отмена", callback_data="teacher:cancel_lesson"),
        ],
    ])


def kb_lesson_type_after_save(back_cb: str = "teacher:menu") -> InlineKeyboardMarkup:
    """После успешного сохранения: продолжить с тем же днём или выйти в меню."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Групповое", callback_data="lesson_kind:group")],
        [InlineKeyboardButton(text="💃 Парное", callback_data="lesson_kind:pair")],
        [InlineKeyboardButton(text="👤 Соло-занятие", callback_data="lesson_kind:soloist")],
        [InlineKeyboardButton(text="🎯 Соло 2 и больше", callback_data="lesson_kind:shared")],
        [InlineKeyboardButton(text="« В меню", callback_data=back_cb)],
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


def kb_group_roster_per_visit(
    students: list, selected_ids: set, tiers: dict,
    price_short: int, duration_short: int,
    price_full: int, duration_full: int,
    back_cb: str = "lesson_back:attendance",
) -> InlineKeyboardMarkup:  # price_short/price_full не показываем педагогу
    """
    Ростер группы с per-visit биллингом. Рядом с именем — тариф на это занятие.
    Вторая кнопка в ряду переключает тариф разово (карточку не меняет).
    tiers: dict[student_id -> "short" | "full"].
    """
    has_short = price_short > 0
    rows = []
    for s in students:
        mark = "✅" if s.student_id in selected_ids else "⬜"
        tier = tiers.get(s.student_id, "full")
        if has_short and tier == "short":
            suffix = f"{duration_short}м"
            alt_text = f"🔄 {duration_full}м"
        else:
            suffix = f"{duration_full}м"
            alt_text = f"🔄 {duration_short}м"
        row = [InlineKeyboardButton(
            text=f"{mark} {s.name} · {suffix}",
            callback_data=f"ms_toggle:{s.student_id}",
        )]
        if has_short:
            row.append(InlineKeyboardButton(
                text=alt_text,
                callback_data=f"ms_tier:{s.student_id}",
            ))
        rows.append(row)
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


def kb_pair_multi_select(
    pairs: list, selected_keys: set,
    back_cb: str = "lesson_back:duration",
) -> InlineKeyboardMarkup:
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


def kb_pair_from_soloists(
    soloists: list, selected_ids: set,
    back_cb: str = "lesson_back:duration",
    done_label: str | None = None,
    done_cb: str = "pso_confirm",
    finish_label: str | None = None,
) -> InlineKeyboardMarkup:
    """Чекбоксы для выбора 2–4 солистов как разового индивидуального занятия.
    finish_label — если задан, добавляет кнопку прямого завершения (shared_confirm) над кнопкой «назад»."""
    rows = []
    for s in soloists:
        mark = "✅" if s.student_id in selected_ids else "⬜"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {s.name}", callback_data=f"pso_toggle:{s.student_id}",
        )])
    if finish_label:
        rows.append([InlineKeyboardButton(text=finish_label, callback_data="shared_confirm")])
    label = done_label if done_label is not None else f"💾 Подтвердить ({len(selected_ids)}/1–4)"
    rows.append([InlineKeyboardButton(text=label, callback_data=done_cb)])
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
            InlineKeyboardButton(text="30 мин", callback_data="duration:30"),
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


def kb_lesson_list(
    lessons: list, page: int = 0, page_size: int = 10,
    locked_ids: set | None = None,
    filter_date: str | None = None,
    filter_month: str | None = None,
    filter_type: str | None = None,
    back_cb: str = "teacher:lesson_delete",
    type_cb_prefix: str = "lessons_type",
) -> InlineKeyboardMarkup:
    """Список занятий с пагинацией. locked_ids — занятия из сданного периода.
    filter_date/filter_month/filter_type сохраняются в callback пагинации и между перерисовками.
    filter_type: None=все, "group"=групповые, "individual"=парные/соло.
    type_cb_prefix: префикс callback для переключателя типа ("lessons_type" для педагога,
    "aedl_type:{teacher_id}" для админа)."""
    from bot.utils.dates import format_date_display, format_date_short_with_wd
    locked_ids = locked_ids or set()
    start = page * page_size
    page_lessons = lessons[start: start + page_size]
    if filter_date:
        filter_tag = filter_date
    elif filter_month:
        filter_tag = f"m-{filter_month}"
    else:
        filter_tag = "all"
    type_code = {"group": "g", "individual": "i"}.get(filter_type or "", "a")

    from bot.models.enums import LessonType
    buttons = []

    def _tbtn(label: str, code: str) -> InlineKeyboardButton:
        marker = "● " if code == type_code else ""
        return InlineKeyboardButton(
            text=f"{marker}{label}",
            callback_data=f"{type_cb_prefix}:{code}:{filter_tag}",
        )
    buttons.append([
        _tbtn("Все", "a"),
        _tbtn("👥 Групповые", "g"),
        _tbtn("👤 Индив.", "i"),
    ])

    group_by_date = filter_date is None
    current_date: str | None = None
    for ls in page_lessons:
        lock_icon = "🔒 " if ls.lesson_id in locked_ids else ""
        if ls.type == LessonType.GROUP:
            who = "группа"
        else:
            names = [n for n in (
                ls.student_1_name, ls.student_2_name,
                ls.student_3_name, ls.student_4_name,
            ) if n]
            if len(names) >= 2:
                who = " + ".join(names)
            else:
                who = names[0] if names else "—"

        if group_by_date and ls.date != current_date:
            current_date = ls.date
            buttons.append([InlineKeyboardButton(
                text=f"━━━ 📅 {format_date_short_with_wd(ls.date)} ━━━",
                callback_data="noop",
            )])
            label = f"{lock_icon}{ls.duration_min}м · {who}"
        else:
            label = f"{lock_icon}{format_date_display(ls.date)} · {ls.duration_min}м · {who}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"lesson_detail:{ls.lesson_id}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(
            text="← Пред.", callback_data=f"lessons_page:{page - 1}:{filter_tag}:{type_code}",
        ))
    if start + page_size < len(lessons):
        nav_row.append(InlineKeyboardButton(
            text="След. →", callback_data=f"lessons_page:{page + 1}:{filter_tag}:{type_code}",
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
