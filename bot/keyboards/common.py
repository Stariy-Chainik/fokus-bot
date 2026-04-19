from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def kb_mode_select(
    is_admin: bool = False, is_teacher: bool = False, is_client: bool = False,
) -> InlineKeyboardMarkup:
    """Выбор режима для пользователя с несколькими ролями."""
    rows = []
    if is_admin:
        rows.append([InlineKeyboardButton(text="👔 Администратор", callback_data="mode:admin")])
    if is_teacher:
        rows.append([InlineKeyboardButton(text="🎓 Педагог", callback_data="mode:teacher")])
    if is_client:
        rows.append([InlineKeyboardButton(text="🧑‍🎓 Ученик", callback_data="mode:client")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
