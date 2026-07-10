from __future__ import annotations
import logging

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramAPIError

from bot.models import User, Student, StudentRequest
from bot.repositories import (
    StudentRepository,
    GroupRepository, BranchRepository, StudentRequestRepository,
)
from bot.services import (
    StudentRequestService, LinkExistingOutcome,
)
from bot.models.enums import RequestStatus
from bot.keyboards.admin import (
    kb_back,
)
from bot.handlers.access import is_admin as _is_admin

from ._base import router

logger = logging.getLogger(__name__)


# ─── Заявки педагогов на создание новых учеников ─────────────────────────────

def _find_similar_students(all_students: list, name: str) -> list:
    """Точное совпадение по первому слову (фамилии), без учёта регистра. До 10 шт."""
    parts = name.split()
    if not parts:
        return []
    surname = parts[0].lower()
    similar = [
        s for s in all_students
        if s.name.split() and s.name.split()[0].lower() == surname
    ]
    return similar[:10]


async def _notify_other_admins(
    bot, req: StudentRequest, except_chat_id: int, resolution_text: str,
) -> None:
    """Редактирует сообщения у остальных админов, показывая что заявка уже обработана."""
    for chat_id, message_id in StudentRequestRepository.parse_admin_msgs(req):
        if chat_id == except_chat_id:
            continue
        try:
            await bot.edit_message_text(
                resolution_text, chat_id=chat_id, message_id=message_id,
            )
        except TelegramAPIError as exc:
            logger.warning("Не удалось обновить уведомление у админа chat_id=%s: %s", chat_id, exc)


async def _notify_teacher_student_created(bot, req: StudentRequest, student_name: str) -> None:
    try:
        await bot.send_message(
            req.teacher_tg_id,
            f"✅ Ученик <b>{student_name}</b> создан. "
            f"Он появится в ваших списках после обновления кэша групп.",
        )
    except Exception as exc:
        logger.error("Не удалось уведомить педагога о создании ученика: %s", exc)


async def _group_toast(
    student_name: str, group_id: str,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> str:
    if not group_id:
        return f"Ученик «{student_name}» создан"
    group = await group_repo.get_by_id(group_id)
    if not group:
        return f"Ученик «{student_name}» создан"
    branch = await branch_repo.get_by_id(group.branch_id)
    bname = branch.name if branch else "—"
    return f"Ученик «{student_name}» добавлен в группу «{group.name}» (филиал «{bname}»)"


async def _finish_created_request(
    callback: CallbackQuery, req: StudentRequest, student: Student,
    group_repo: GroupRepository, branch_repo: BranchRepository,
) -> None:
    """Общий хвост req_approve/req_create_new: уведомления, экран, тост."""
    await _notify_teacher_student_created(callback.bot, req, student.name)
    await callback.message.edit_text(
        f"✅ Ученик <b>{student.name}</b> создан (ID: {student.student_id}).",
        reply_markup=kb_back("admin:menu"),
    )
    await _notify_other_admins(
        callback.bot, req, callback.message.chat.id,
        f"✅ Заявка обработана админом @{callback.from_user.username or callback.from_user.id}",
    )
    toast = await _group_toast(student.name, req.group_id or "", group_repo, branch_repo)
    await callback.answer(toast, show_alert=True)


@router.callback_query(F.data.startswith("req_approve:"))
async def cb_approve_student_request(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository,
    student_request_repo: StudentRequestRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    student_request_service: StudentRequestService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = callback.data.split(":", 1)[1]
    req = await student_request_repo.get_by_id(req_id)
    if not req or req.status != RequestStatus.PENDING:
        await callback.answer("Заявка уже обработана.", show_alert=True)
        return
    name = req.student_name
    all_students = await student_repo.get_all()
    similar = _find_similar_students(all_students, name)
    if similar:
        rows = [
            [InlineKeyboardButton(
                text=f"🔗 Привязать: {s.name}",
                callback_data=f"req_link_existing:{req_id}:{s.student_id}",
            )] for s in similar
        ]
        rows.append([InlineKeyboardButton(
            text="➕ Всё равно создать нового", callback_data=f"req_create_new:{req_id}",
        )])
        rows.append([InlineKeyboardButton(
            text="❌ Отменить", callback_data=f"req_reject:{req_id}",
        )])
        surname = name.split()[0] if name.split() else name
        await callback.message.edit_text(
            f"📝 Заявка от <b>{req.teacher_name}</b> на ученика <b>{name}</b>.\n\n"
            f"⚠️ В базе уже есть ученики с фамилией «<b>{surname}</b>»:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await callback.answer()
        return
    # Дублей нет — атомарно помечаем как APPROVED, затем создаём
    student = await student_request_service.approve_create(req, callback.from_user.id)
    if student is None:
        await callback.answer("Заявка уже обработана.", show_alert=True)
        return
    await _finish_created_request(callback, req, student, group_repo, branch_repo)


@router.callback_query(F.data.startswith("req_create_new:"))
async def cb_create_new_student_request(
    callback: CallbackQuery, user: User | None,
    student_request_repo: StudentRequestRepository,
    group_repo: GroupRepository, branch_repo: BranchRepository,
    student_request_service: StudentRequestService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = callback.data.split(":", 1)[1]
    req = await student_request_repo.get_by_id(req_id)
    if not req or req.status != RequestStatus.PENDING:
        await callback.answer("Заявка уже обработана.", show_alert=True)
        return
    student = await student_request_service.approve_create(req, callback.from_user.id)
    if student is None:
        await callback.answer("Заявка уже обработана.", show_alert=True)
        return
    await _finish_created_request(callback, req, student, group_repo, branch_repo)


@router.callback_query(F.data.startswith("req_link_existing:"))
async def cb_link_existing_student_request(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository,
    student_request_repo: StudentRequestRepository,
    student_request_service: StudentRequestService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, req_id, student_id = callback.data.split(":", 2)
    req = await student_request_repo.get_by_id(req_id)
    if not req or req.status != RequestStatus.PENDING:
        await callback.answer("Заявка уже обработана.", show_alert=True)
        return
    student = await student_repo.get_by_id(student_id)
    if not student:
        await callback.answer("Ученик не найден.", show_alert=True)
        return
    outcome = await student_request_service.approve_link_existing(
        req, student_id, callback.from_user.id,
    )
    if outcome is None:
        await callback.answer("Заявка уже обработана.", show_alert=True)
        return
    try:
        if outcome is LinkExistingOutcome.ALREADY_IN_GROUP:
            teacher_note = (
                f"✅ Ученик <b>{student.name}</b> уже в вашей группе — пользуйтесь."
            )
        elif outcome is LinkExistingOutcome.ADDED_TO_GROUP:
            teacher_note = (
                f"✅ Ученик <b>{student.name}</b> добавлен в вашу группу — пользуйтесь."
            )
        else:
            teacher_note = (
                f"ℹ️ По вашей заявке админ выбрал существующего ученика "
                f"<b>{student.name}</b>."
            )
        await callback.bot.send_message(req.teacher_tg_id, teacher_note)
    except Exception as exc:
        logger.error("Не удалось уведомить педагога о привязке ученика: %s", exc)
    if outcome is LinkExistingOutcome.ALREADY_IN_GROUP:
        admin_note = "Ученик уже состоит в этой группе — педагог его видит."
    elif outcome is LinkExistingOutcome.ADDED_TO_GROUP:
        admin_note = "Ученик добавлен в группу заявки — педагог теперь его видит."
    else:
        admin_note = "Группа в заявке не указана."
    await callback.message.edit_text(
        f"🔗 Выбран ученик <b>{student.name}</b> (ID: {student.student_id}).\n{admin_note}",
        reply_markup=kb_back("admin:menu"),
    )
    await _notify_other_admins(
        callback.bot, req, callback.message.chat.id,
        f"✅ Заявка обработана админом @{callback.from_user.username or callback.from_user.id}",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("req_reject:"))
async def cb_reject_student_request(
    callback: CallbackQuery, user: User | None,
    student_request_repo: StudentRequestRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = callback.data.split(":", 1)[1]
    req = await student_request_repo.get_by_id(req_id)
    if not req or req.status != RequestStatus.PENDING:
        await callback.answer("Заявка уже обработана.", show_alert=True)
        return
    if not await student_request_repo.mark_resolved(
        req_id, RequestStatus.REJECTED, callback.from_user.id,
    ):
        await callback.answer("Заявка уже обработана.", show_alert=True)
        return
    try:
        await callback.bot.send_message(
            req.teacher_tg_id,
            f"❌ Заявка на создание ученика <b>{req.student_name}</b> отклонена.\n"
            "Свяжитесь с администратором лично.",
        )
    except Exception as exc:
        logger.error("Не удалось уведомить педагога об отклонении заявки: %s", exc)
    await callback.message.edit_text(
        f"❌ Заявка от <b>{req.teacher_name}</b> на <b>{req.student_name}</b> отклонена.",
        reply_markup=kb_back("admin:menu"),
    )
    await _notify_other_admins(
        callback.bot, req, callback.message.chat.id,
        f"❌ Заявка отклонена админом @{callback.from_user.username or callback.from_user.id}",
    )
    await callback.answer()


# ─── Список ожидающих заявок ─────────────────────────────────────────────────

@router.callback_query(F.data == "admin:requests")
async def cb_requests_list(
    callback: CallbackQuery, user: User | None,
    student_request_repo: StudentRequestRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    pending = await student_request_repo.get_pending()
    if not pending:
        await callback.message.edit_text(
            "Ожидающих заявок нет.", reply_markup=kb_back("admin:menu"),
        )
        await callback.answer()
        return
    rows = []
    for r in sorted(pending, key=lambda x: x.created_at):
        rows.append([InlineKeyboardButton(
            text=f"📝 {r.teacher_name} → {r.student_name}",
            callback_data=f"req_approve:{r.request_id}",
        )])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="admin:menu")])
    await callback.message.edit_text(
        f"<b>Ожидающие заявки ({len(pending)}):</b>\n"
        "Нажмите на заявку чтобы обработать.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


