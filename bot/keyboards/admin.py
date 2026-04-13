from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton


def kb_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨‍🏫 Педагоги", callback_data="admin:teachers")],
        [InlineKeyboardButton(text="👩‍🎓 Ученики", callback_data="admin:students")],
        [InlineKeyboardButton(text="💰 Зарплаты", callback_data="admin:salaries")],
        [InlineKeyboardButton(text="🧾 Счета учеников", callback_data="admin:bills")],
        [InlineKeyboardButton(text="✏️ Редактировать занятие", callback_data="admin:edit_lesson")],
        [InlineKeyboardButton(text="🔧 Диагностика", callback_data="admin:diagnostics")],
    ])


def kb_teachers_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список педагогов", callback_data="teachers:list")],
        [InlineKeyboardButton(text="➕ Добавить педагога", callback_data="teachers:add")],
        [InlineKeyboardButton(text="🗑 Удалить педагога", callback_data="teachers:delete")],
        [InlineKeyboardButton(text="📊 Изменить ставки", callback_data="teachers:edit_rates")],
        [InlineKeyboardButton(text="« Назад", callback_data="admin:menu")],
    ])


def kb_students_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Поиск ученика", callback_data="students:list")],
        [InlineKeyboardButton(text="💃 Все пары", callback_data="students:all_pairs")],
        [InlineKeyboardButton(text="🎯 Все солисты", callback_data="students:all_soloists")],
        [InlineKeyboardButton(text="➕ Добавить ученика", callback_data="students:add")],
        [InlineKeyboardButton(text="🗑 Удалить ученика", callback_data="students:delete")],
        [InlineKeyboardButton(text="🔗 Привязать ученика к педагогу", callback_data="students:link")],
        [InlineKeyboardButton(text="✂️ Убрать связь педагог-ученик", callback_data="students:unlink")],
        [InlineKeyboardButton(text="« Назад", callback_data="admin:menu")],
    ])


_STUDENT_PAGE_SIZE = 20


def kb_student_paged(students: list, page: int, total: int, query: str = "") -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=s.name, callback_data=f"student_card:{s.student_id}")]
        for s in students
    ]
    nav = []
    q = query.replace(":", "_")
    if page > 0:
        nav.append(InlineKeyboardButton(text="← Пред.", callback_data=f"spage:{q}:{page - 1}"))
    if (page + 1) * _STUDENT_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="След. →", callback_data=f"spage:{q}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="🔍 Новый поиск", callback_data="students:list")])
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data="admin:students")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_student_card(student_id: str, has_partner: bool) -> InlineKeyboardMarkup:
    partner_label = "🔄 Изменить партнёра" if has_partner else "💃 Назначить партнёра"
    rows = [
        [InlineKeyboardButton(text=partner_label, callback_data=f"partner_assign:{student_id}")],
    ]
    if has_partner:
        rows.append([InlineKeyboardButton(text="❌ Убрать партнёра", callback_data=f"partner_clear:{student_id}")])
    rows.append([InlineKeyboardButton(text="« Назад к списку", callback_data="students:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_partner_candidates(candidates: list, student_id: str) -> InlineKeyboardMarkup:
    """
    candidates: list[tuple[Student, bool]] — ученик и флаг «у него уже есть партнёр».
    """
    buttons = []
    for s, has_partner in candidates:
        label = f"⚠️ {s.name}" if has_partner else s.name
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"partner_pick:{s.student_id}")])
    buttons.append([InlineKeyboardButton(text="« Отмена", callback_data=f"student_card:{student_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_salaries_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Зарплата педагога за период", callback_data="salaries:view")],
        [InlineKeyboardButton(text="« Назад", callback_data="admin:menu")],
    ])


def kb_bills_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Счёт ученика за период", callback_data="bills:view")],
        [InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data="bills:confirm_payment")],
        [InlineKeyboardButton(text="« Назад", callback_data="admin:menu")],
    ])


def kb_teacher_list(teachers: list, action_prefix: str, back_cb: str = "admin:teachers") -> InlineKeyboardMarkup:
    """Список педагогов для выбора. action_prefix например 'del_teacher' → callback_data='del_teacher:TCH-0001'"""
    buttons = [
        [InlineKeyboardButton(text=t.name, callback_data=f"{action_prefix}:{t.teacher_id}")]
        for t in teachers
    ]
    buttons.append([InlineKeyboardButton(text="« Отмена", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_teacher_card(teacher_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Изменить ставки", callback_data=f"card_edit_rates:{teacher_id}")],
        [InlineKeyboardButton(text="« Назад", callback_data="teachers:list")],
    ])


def kb_student_list(students: list, action_prefix: str, back_cb: str = "admin:students") -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=s.name, callback_data=f"{action_prefix}:{s.student_id}")]
        for s in students
    ]
    buttons.append([InlineKeyboardButton(text="« Отмена", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_rate_select(teacher_id: str, rate_group: int, rate_teacher: int, rate_student: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Групповое: {rate_group} руб.", callback_data=f"edit_rate:group:{teacher_id}")],
        [InlineKeyboardButton(text=f"Инд. педагогу: {rate_teacher} руб.", callback_data=f"edit_rate:teacher:{teacher_id}")],
        [InlineKeyboardButton(text=f"Инд. ученику: {rate_student} руб.", callback_data=f"edit_rate:student:{teacher_id}")],
        [InlineKeyboardButton(text="« Отмена", callback_data="admin:teachers")],
    ])


def kb_confirm(confirm_cb: str, cancel_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=confirm_cb),
            InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_cb),
        ]
    ])


def kb_back(cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад", callback_data=cb)]
    ])
