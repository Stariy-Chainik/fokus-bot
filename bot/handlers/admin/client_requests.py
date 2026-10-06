from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery

from config.settings import settings
from bot.models import User
from bot.repositories import StudentRepository
from bot.screens.parent_menu import menu_rows
from bot.services.parent_notifier import resolve_notifier, parse_addr
from bot.services.pending_queue import DONE, KIND_CHILD, REJECTED, close_actions
from bot.keyboards.admin import kb_back
from bot.handlers.filters import AdminOnly, TeacherOrAdmin
from bot.services.new_child import KIND_NEWCHILD, approve_new_child, may_decide, reject_new_child
from bot.services.parent_unlink import KIND_UNLINK, approve_unlink, reject_unlink

logger = logging.getLogger(__name__)
router = Router(name="admin_client_requests")



@router.callback_query(F.data.startswith("admin_child_ok:"), AdminOnly())
async def cb_admin_child_ok(
    callback: CallbackQuery,
    user: User,
    student_repo: StudentRepository,
    pending_repo=None,
) -> None:

    _, parent_raw, student_id = callback.data.split(":", 2)
    parent_addr = parse_addr(parent_raw)

    student = await student_repo.get_by_id(student_id)
    if not student or parent_addr is None:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    await student_repo.add_parent(student_id, parent_addr)
    await close_actions(pending_repo, student_id, "", DONE, callback.from_user.id, kinds=(KIND_CHILD,))
    logger.info("Админ одобрил: %s → student_id=%s", parent_raw, student_id)

    await resolve_notifier(callback.bot).send(
        parent_addr,
        f"✅ Заявка одобрена!\n\nВы привязаны к ученику <b>{student.name}</b>.\n\nВыберите раздел:",
        rows=menu_rows(platform=parent_addr[0], receipt_email=settings.parent_receipt_email),
    )

    await callback.message.edit_text(
        f"✅ Одобрено\n\nУченик: {student.name}\nРодитель: {parent_raw}",
        reply_markup=kb_back("admin:menu"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_child_no:"), AdminOnly())
async def cb_admin_child_no(
    callback: CallbackQuery,
    user: User,
    student_repo: StudentRepository,
    pending_repo=None,
) -> None:

    _, parent_raw, student_id = callback.data.split(":", 2)
    parent_addr = parse_addr(parent_raw)

    student = await student_repo.get_by_id(student_id)
    student_name = student.name if student else student_id
    await close_actions(pending_repo, student_id, "", REJECTED, callback.from_user.id, kinds=(KIND_CHILD,))
    logger.info("Админ отклонил: %s → student_id=%s", parent_raw, student_id)

    if parent_addr:
        await resolve_notifier(callback.bot).send(
            parent_addr, "❌ Администратор отклонил вашу заявку.",
            rows=menu_rows(platform=parent_addr[0], receipt_email=settings.parent_receipt_email),
        )

    await callback.message.edit_text(
        f"❌ Отклонено\n\nУченик: {student_name}\nРодитель: {parent_raw}",
        reply_markup=kb_back("admin:menu"),
    )
    await callback.answer()


# ─── «Моего ребёнка нет в группе»: карточку заводит администратор или педагог группы ──────────

@router.callback_query(F.data.startswith("nchild_ok:"), TeacherOrAdmin())
async def cb_new_child_ok(
    callback: CallbackQuery, user: User, student_repo: StudentRepository,
    student_group_repo=None, teacher_group_repo=None, pending_repo=None,
) -> None:
    action = await pending_repo.get_by_id(callback.data.split(":", 1)[1]) if pending_repo else None
    if action is None or action.kind != KIND_NEWCHILD:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if not await may_decide(user, action, teacher_group_repo):
        await callback.answer("Это заявка не вашей группы", show_alert=True)
        return
    student = await approve_new_child(pending_repo, student_repo, student_group_repo,
                                      resolve_notifier(callback.bot), action, callback.from_user.id)
    if student is None:
        await callback.answer("Заявку уже решили", show_alert=True)
        return
    await callback.message.edit_text(
        f"✅ Заведён ученик <b>{student.name}</b> ({student.student_id}), родитель привязан.\n"
        f"Группа: {action.comment.split(' · ')[0]}",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("nchild_no:"), TeacherOrAdmin())
async def cb_new_child_no(
    callback: CallbackQuery, user: User, teacher_group_repo=None, pending_repo=None,
) -> None:
    action = await pending_repo.get_by_id(callback.data.split(":", 1)[1]) if pending_repo else None
    if action is None or action.kind != KIND_NEWCHILD:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if not await may_decide(user, action, teacher_group_repo):
        await callback.answer("Это заявка не вашей группы", show_alert=True)
        return
    if not await reject_new_child(pending_repo, resolve_notifier(callback.bot), action, callback.from_user.id):
        await callback.answer("Заявку уже решили", show_alert=True)
        return
    await callback.message.edit_text(f"❌ Отклонено: <b>{action.student_name}</b> · {action.comment}")
    await callback.answer()


# ─── Родитель просит отвязать другого родителя: решает администратор ─────────────────────────

@router.callback_query(F.data.startswith("unlink_ok:"), AdminOnly())
async def cb_unlink_ok(
    callback: CallbackQuery, user: User, student_repo: StudentRepository,
    client_repo=None, pending_repo=None,
) -> None:
    action = await pending_repo.get_by_id(callback.data.split(":", 1)[1]) if pending_repo else None
    if action is None or action.kind != KIND_UNLINK:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    student = await approve_unlink(pending_repo, student_repo, client_repo, resolve_notifier(callback.bot),
                                   action, callback.from_user.id)
    if student is None:
        await callback.answer("Заявку уже решили", show_alert=True)
        return
    await callback.message.edit_text(f"✅ Отвязано\n\nУченик: {student.name}\n{action.comment}")
    await callback.answer()


@router.callback_query(F.data.startswith("unlink_no:"), AdminOnly())
async def cb_unlink_no(callback: CallbackQuery, user: User, pending_repo=None) -> None:
    action = await pending_repo.get_by_id(callback.data.split(":", 1)[1]) if pending_repo else None
    if action is None or action.kind != KIND_UNLINK:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if not await reject_unlink(pending_repo, resolve_notifier(callback.bot), action, callback.from_user.id):
        await callback.answer("Заявку уже решили", show_alert=True)
        return
    await callback.message.edit_text(f"❌ Привязка оставлена\n\nУченик: {action.student_name}\n{action.comment}")
    await callback.answer()
