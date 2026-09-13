"""Регистрация спортсмена: «Я спортсмен» → фамилия → выбор себя → привязка без одобрения.

Ищем только среди учеников спортивных групп (ATHLETE_GROUP_IDS). Админы получают
уведомление с кнопкой «Отвязать» (как при привязке родителя по ссылке группы).
"""
from __future__ import annotations
import logging

from aiogram import F
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config.settings import settings
from typing import cast

from bot.models import User
from bot.repositories import StudentRepository, UserRepository
from bot.services import DiaryService
from bot.states import AthleteRegStates
from bot.keyboards.common import kb_welcome_choice
from bot.keyboards.athlete import kb_athlete_menu, athlete_welcome_text
from bot.utils.notify import notify
from bot.services.parent_notifier import resolve_notifier
from bot.handlers.access import is_admin, is_teacher_or_admin
from bot.utils.group_links import build_athlete_payload, parse_athlete_payload
from ._base import router

logger = logging.getLogger(__name__)

_kb_cancel = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="« Отмена", callback_data="athreg:cancel")],
])


def _link_secret() -> str:
    return settings.group_link_secret or settings.bot_token


# ─── Персональная ссылка: t.me/<bot>?start=a_<student_id>_<token> ───────────

@router.message(CommandStart(deep_link=True, magic=F.args.startswith("a_")))
async def cmd_start_athlete_link(
    message: Message, command: CommandObject, state: FSMContext,
    student_repo: StudentRepository,
) -> None:
    student_id = parse_athlete_payload(command.args or "", _link_secret())
    student = await student_repo.get_by_id(student_id) if student_id else None
    if student is None:
        await message.answer("Ссылка недействительна или устарела. Попросите у педагога новую.")
        return
    await state.clear()
    tg_id = cast(int, message.from_user.id)
    if student.athlete_tg_id == tg_id:
        parents = await student_repo.get_by_parent_tg_id(tg_id)
        await message.answer(athlete_welcome_text(student), reply_markup=kb_athlete_menu(bool(parents)))
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Да, это я: {student.name}", callback_data=f"athreg:pick:{student_id}")],
        [InlineKeyboardButton(text="❌ Это не я", callback_data="athreg:cancel")],
    ])
    await message.answer(
        "🏃 <b>Кабинет спортсмена</b>\n\nЗдесь вы записываете свои тренировки, получаете задания "
        "и оценки педагога, а очки идут в рейтинг.\n\nПодтвердите, что это вы:",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("athreg:link:"))
async def cb_athlete_link(
    callback: CallbackQuery, user: User | None, student_repo: StudentRepository,
) -> None:
    """Админ/педагог: показать персональную ссылку спортсмена (из карточки ученика)."""
    if not is_teacher_or_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    student_id = callback.data.split(":", 2)[2]
    student = await student_repo.get_by_id(student_id)
    if student is None:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    me = await callback.bot.me()
    link = f"https://t.me/{me.username}?start={build_athlete_payload(student_id, _link_secret())}"
    status = (f"Кабинет уже привязан (<code>{student.athlete_tg_id}</code>)." if student.athlete_tg_id
              else "Кабинет ещё не привязан.")
    await callback.message.answer(
        f"🔗 <b>Ссылка для спортсмена {student.name}</b>\n{status}\n\n"
        f"Отправьте ученику — он откроет её и подтвердит «Да, это я»:\n<code>{link}</code>",
    )
    await callback.answer()


@router.callback_query(F.data == "athreg:parent")
async def cb_reg_parent(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Введите фамилию ученика для регистрации:")
    await callback.answer()


_LIST_LIMIT = 40  # больше — только поиск по фамилии


@router.callback_query(F.data == "athreg:athlete")
async def cb_reg_athlete(callback: CallbackQuery, state: FSMContext, diary_service: DiaryService) -> None:
    """Список учеников спортивных групп; ввод фамилии — запасной вариант."""
    await state.clear()
    candidates = await diary_service.registration_candidates("", settings.athlete_group_id_set)
    if not candidates or len(candidates) > _LIST_LIMIT:
        await _ask_surname(callback, state)
        return
    rows = [[InlineKeyboardButton(text=s.name, callback_data=f"athreg:pick:{s.student_id}")] for s in candidates]
    rows.append([InlineKeyboardButton(text="✍️ Меня нет в списке — ввести фамилию", callback_data="athreg:typein")])
    rows.append([InlineKeyboardButton(text="« Отмена", callback_data="athreg:cancel")])
    await callback.message.edit_text(
        "🏃 Кабинет спортсмена доступен ученикам спортивных групп.\n\nНайдите себя в списке:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.message(CommandStart(deep_link=True, magic=F.args == "athlete"))
async def cmd_start_athlete(message: Message, state: FSMContext, diary_service: DiaryService) -> None:
    """Ссылка t.me/<bot>?start=athlete — сразу список спортивной группы."""
    await state.clear()
    candidates = await diary_service.registration_candidates("", settings.athlete_group_id_set)
    if not candidates or len(candidates) > _LIST_LIMIT:
        await state.set_state(AthleteRegStates.waiting_surname)
        await message.answer("🏃 Кабинет спортсмена.\n\nВведите свою фамилию:", reply_markup=_kb_cancel)
        return
    rows = [[InlineKeyboardButton(text=s.name, callback_data=f"athreg:pick:{s.student_id}")] for s in candidates]
    rows.append([InlineKeyboardButton(text="✍️ Меня нет в списке — ввести фамилию", callback_data="athreg:typein")])
    rows.append([InlineKeyboardButton(text="« Отмена", callback_data="athreg:cancel")])
    await message.answer(
        "🏃 <b>Кабинет спортсмена</b>\nЗдесь вы записываете тренировки, получаете задания и оценки педагога, "
        "а очки идут в рейтинг.\n\nНайдите себя в списке:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _ask_surname(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AthleteRegStates.waiting_surname)
    await callback.message.edit_text(
        "🏃 Кабинет спортсмена доступен ученикам спортивных групп.\n\n"
        "Введите свою фамилию:",
        reply_markup=_kb_cancel,
    )
    await callback.answer()


@router.callback_query(F.data == "athreg:typein")
async def cb_reg_typein(callback: CallbackQuery, state: FSMContext) -> None:
    await _ask_surname(callback, state)


@router.callback_query(F.data == "athreg:cancel")
async def cb_reg_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Добро пожаловать!\n\nКто вы?", reply_markup=kb_welcome_choice())
    await callback.answer()


@router.callback_query(F.data == "athreg:retry")
async def cb_reg_retry(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AthleteRegStates.waiting_surname)
    await callback.message.edit_text("Введите свою фамилию ещё раз:", reply_markup=_kb_cancel)
    await callback.answer()


@router.message(AthleteRegStates.waiting_surname, F.text)
async def on_surname(message: Message, state: FSMContext, diary_service: DiaryService) -> None:
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Введите фамилию (минимум 2 буквы):", reply_markup=_kb_cancel)
        return
    matches = await diary_service.registration_candidates(query, settings.athlete_group_id_set)
    if not matches:
        await message.answer(
            f"Спортсмен с фамилией <b>{query}</b> не найден среди спортивных групп.\n"
            "Проверьте написание или обратитесь к педагогу.",
            reply_markup=_kb_cancel,
        )
        return
    rows = [
        [InlineKeyboardButton(
            text=("✅ Да, это я: " if len(matches) == 1 else "") + s.name,
            callback_data=f"athreg:pick:{s.student_id}",
        )]
        for s in matches[:10]
    ]
    rows.append([InlineKeyboardButton(text="❌ Меня нет в списке", callback_data="athreg:retry")])
    rows.append([InlineKeyboardButton(text="« Отмена", callback_data="athreg:cancel")])
    title = "Это вы?" if len(matches) == 1 else "Найдено несколько — выберите себя:"
    await message.answer(title, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("athreg:pick:"))
async def cb_reg_pick(
    callback: CallbackQuery, state: FSMContext,
    student_repo: StudentRepository, user_repo: UserRepository, diary_service: DiaryService,
) -> None:
    student_id = callback.data.split(":", 2)[2]
    student = await student_repo.get_by_id(student_id)
    if student is None:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    tg_id = callback.from_user.id
    if student.athlete_tg_id and student.athlete_tg_id != tg_id:
        await state.clear()
        await callback.message.edit_text(
            f"К спортсмену <b>{student.name}</b> уже привязан другой аккаунт.\n"
            "Если это вы — обратитесь к администратору.",
        )
        admins = [u.tg_id for u in await user_repo.get_admins()]
        sender = callback.from_user
        await notify(callback.bot, admins,
                     f"⚠️ {sender.full_name} (<code>{tg_id}</code>) пытался привязаться как спортсмен "
                     f"<b>{student.name}</b>, но к ученику уже привязан <code>{student.athlete_tg_id}</code>.")
        await callback.answer()
        return
    # Повторная проверка членства в спортивной группе (кнопка могла устареть)
    allowed = {s.student_id for s in await diary_service.registration_candidates("", settings.athlete_group_id_set)}
    if student_id not in allowed:
        await callback.answer("Этот ученик не в спортивной группе", show_alert=True)
        return

    if student.athlete_tg_id != tg_id:
        await student_repo.set_athlete_tg_id(student_id, tg_id)
        logger.info("Спортсмен привязан: tg_id=%s → %s", tg_id, student_id)
        sender = callback.from_user
        username = f" @{sender.username}" if sender.username else ""
        kb_undo = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🚫 Отвязать", callback_data=f"athreg:unlink:{student_id}:{tg_id}"),
        ]])
        admins = [u.tg_id for u in await user_repo.get_admins()]
        await notify(callback.bot, admins,
                     f"🏃 <b>Новый спортсмен в боте</b>\n\n"
                     f"{sender.full_name}{username} (<code>{tg_id}</code>)\n"
                     f"Ученик: <b>{student.name}</b>", reply_markup=kb_undo)
        await resolve_notifier(callback.bot).send_many(student.parent_addrs,
                     f"ℹ️ <b>{student.name}</b> завёл кабинет спортсмена в боте: теперь он(а) может "
                     f"вести дневник тренировок, а вы — видеть записи и оценки педагога "
                     f"в разделе «📓 Дневник тренировок».")
    await state.clear()
    await state.update_data(current_role="athlete")
    parents = await student_repo.get_by_parent_tg_id(tg_id)
    await callback.message.edit_text(
        f"✅ Вы привязаны как спортсмен <b>{student.name}</b>.\n\n"
        "Записывайте каждую самостоятельную тренировку — педагог увидит её и поставит "
        "оценку, а очки пойдут в рейтинг.\n\n" + athlete_welcome_text(student),
        reply_markup=kb_athlete_menu(can_switch_parent=bool(parents)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("athreg:unlink:"))
async def cb_reg_unlink(
    callback: CallbackQuery, user: User | None, student_repo: StudentRepository,
) -> None:
    if not is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, student_id, tg_raw = callback.data.split(":", 3)
    tg_id = int(tg_raw)
    student = await student_repo.get_by_id(student_id)
    if student is None or student.athlete_tg_id != tg_id:
        await callback.answer("Привязка уже отменена", show_alert=True)
        return
    await student_repo.set_athlete_tg_id(student_id, None)
    logger.info("Админ %s отвязал спортсмена tg_id=%s от %s", callback.from_user.id, tg_id, student_id)
    await notify(callback.bot, [tg_id],
                 f"Ваша привязка к спортсмену <b>{student.name}</b> отменена администратором.")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="« К карточке", callback_data=f"student_card:{student_id}"),
    ]])
    await callback.message.edit_text(
        f"{callback.message.html_text}\n\n🚫 <b>Спортсмен отвязан</b>", reply_markup=kb,
    )
    await callback.answer("Спортсмен отвязан")
