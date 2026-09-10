from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def kb_mode_select() -> InlineKeyboardMarkup:
    """Выбор режима для пользователя, у которого есть и is_admin, и teacher_id."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👔 Администратор", callback_data="mode:admin")],
        [InlineKeyboardButton(text="🎓 Педагог", callback_data="mode:teacher")],
    ])


def kb_mode_select_family() -> InlineKeyboardMarkup:
    """Один Telegram и спортсмен, и родитель другого ребёнка — выбор кабинета."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏃 Кабинет спортсмена", callback_data="mode:athlete")],
        [InlineKeyboardButton(text="👨‍👩‍👧 Кабинет родителя", callback_data="mode:client")],
    ])


def kb_welcome_choice() -> InlineKeyboardMarkup:
    """Первый /start неизвестного пользователя: родитель или спортсмен."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨‍👩‍👧 Я родитель", callback_data="athreg:parent")],
        [InlineKeyboardButton(text="🏃 Я спортсмен", callback_data="athreg:athlete")],
    ])
