from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def kb_mode_select() -> InlineKeyboardMarkup:
    """Выбор режима для пользователя, у которого есть и is_admin, и teacher_id."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👔 Администратор", callback_data="mode:admin")],
        [InlineKeyboardButton(text="🎓 Педагог", callback_data="mode:teacher")],
    ])
