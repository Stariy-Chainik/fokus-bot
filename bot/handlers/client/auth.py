from __future__ import annotations
import logging
import re

from aiogram import Router, F
from aiogram.types import Message

from bot.models import User
from bot.repositories import (
    UserRepository, StudentRepository, StudentInviteRepository,
)
from bot.keyboards import kb_client_menu

logger = logging.getLogger(__name__)
router = Router(name="client_auth")

_CODE_RE = re.compile(r"^\s*(\d{6})\s*$")


@router.message(F.text.regexp(_CODE_RE))
async def on_invite_code(
    message: Message,
    user: User | None,
    user_repo: UserRepository,
    student_repo: StudentRepository,
    invite_repo: StudentInviteRepository,
) -> None:
    """Ввод 6-значного кода от пользователя без клиентской роли.

    Если у пользователя уже есть student_id — сообщение уходит в общий поток
    (например, оно может быть естественной частью другого FSM). Фильтр намеренно
    обрабатывает только «новые» клиенты, чтобы не перехватывать ввод у педагогов.
    """
    if user is not None and user.student_id:
        return  # уже клиент — не перехватываем

    match = _CODE_RE.match(message.text or "")
    if not match:
        return
    code = match.group(1)

    invite = await invite_repo.get_by_code(code)
    if invite is None:
        await message.answer(
            "❌ Код неверен, просрочен или уже использован.\n"
            "Обратитесь к администратору за новым кодом."
        )
        return

    student = await student_repo.get_by_id(invite.student_id)
    if student is None:
        logger.error("Invite %s ссылается на несуществующего ученика %s", invite.invite_id, invite.student_id)
        await message.answer("❌ Ошибка привязки. Обратитесь к администратору.")
        return

    tg_user = message.from_user
    if student.tg_id and student.tg_id != tg_user.id:
        await message.answer(
            "❌ Этот ученик уже привязан к другому Telegram-аккаунту.\n"
            "Обратитесь к администратору."
        )
        return

    # best-effort последовательность: Sheets не транзакционны.
    try:
        await student_repo.update_tg_id(student.student_id, tg_user.id)
        if user is None:
            await user_repo.add(tg_id=tg_user.id, student_id=student.student_id)
        else:
            await user_repo.update_student_id(tg_user.id, student.student_id)
        await invite_repo.mark_used(invite.invite_id, tg_user.id)
    except Exception as exc:
        logger.error("Ошибка привязки ученика %s к tg %s: %s", student.student_id, tg_user.id, exc)
        await message.answer("❌ Не удалось завершить привязку. Обратитесь к администратору.")
        return

    logger.info("Ученик %s привязан к tg %s через код %s", student.student_id, tg_user.id, invite.invite_id)

    # Уведомляем всех администраторов.
    username = f"@{tg_user.username}" if tg_user.username else "—"
    notify = (
        f"🔗 Ученик <b>{student.name}</b> привязан к Telegram\n"
        f"Аккаунт: {username}\n"
        f"tg_id: <code>{tg_user.id}</code>"
    )
    admins = [u for u in await user_repo.get_all() if u.is_admin]
    for admin in admins:
        try:
            await message.bot.send_message(admin.tg_id, notify)
        except Exception:
            pass

    # Пересчитаем роли: is_admin у user был, teacher_id мог быть — роль клиента добавилась.
    refreshed = await user_repo.get_by_tg_id(tg_user.id)
    is_admin = bool(refreshed and refreshed.is_admin)
    is_teacher = bool(refreshed and refreshed.teacher_id)

    await message.answer(
        f"✅ Готово! Вы привязаны как ученик <b>{student.name}</b>.",
        reply_markup=kb_client_menu(is_admin=is_admin, is_teacher=is_teacher),
    )
