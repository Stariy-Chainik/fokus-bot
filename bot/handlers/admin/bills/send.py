from __future__ import annotations
import logging

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.repositories import (
    StudentRepository, GroupRepository,
    StudentGroupRepository, ClientRepository,
)
from bot.services import PaymentService
from bot.utils.bill_format import build_bill_text
from bot.handlers.access import is_admin as _is_admin

from ._base import (
    router, _sending_in_progress,
)
from .helpers import (
    _send_bill_to_parents, _student_group_names,
)

logger = logging.getLogger(__name__)


# ─── Отправка родителю ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("bill_send:"))
async def cb_bill_send(
    callback: CallbackQuery, user: User | None,
    student_repo: StudentRepository,
    group_repo: GroupRepository,
    payment_service: PaymentService,
    student_group_repo: StudentGroupRepository,
    client_repo: ClientRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = callback.data.split(":")
    student_id = parts[1]
    period_month = parts[2]
    group_id = parts[3] if len(parts) > 3 else "none"

    lock_key = f"{student_id}:{period_month}"
    if lock_key in _sending_in_progress:
        await callback.answer("Отправка уже выполняется", show_alert=True)
        return
    _sending_in_progress.add(lock_key)
    try:
        student = await student_repo.get_by_id(student_id)
        if not student:
            await callback.answer("Ученик не найден", show_alert=True)
            return

        bills = await payment_service.compute_bills_for_student_period(student_id, period_month)
        if not bills:
            await callback.answer("В счёте нет занятий", show_alert=True)
            return

        group_names = await _student_group_names(student_id, student_group_repo, group_repo)
        back_cb = (
            f"bvb:{period_month}:none" if group_id == "none"
            else f"bvg:{period_month}:{group_id}"
        )

        total_rec, sent_to, sent_invoices = await _send_bill_to_parents(
            callback, student, period_month, bills, group_names,
            payment_service, client_repo,
        )

        if total_rec == 0:
            # Нет ни клиента, ни родителей с доступом — показать шаблон для ручной отправки
            client = await client_repo.get_by_id(student.client_id) if student.client_id else None
            if client:
                header = (
                    "⚠️ <i>Клиент ещё не заходил в бот (нет Telegram). "
                    "Скопируй и отправь вручную:</i>\n\n"
                )
            else:
                header = (
                    "📤 <i>Родитель не привязан. "
                    "Скопируй и отправь вручную:</i>\n\n"
                )
            bill_text, _ = build_bill_text(student.name, group_names, period_month, bills)
            await callback.message.edit_text(
                header + bill_text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="« Назад к ученикам", callback_data=back_cb)],
                    [InlineKeyboardButton(text="« В меню", callback_data="admin:menu")],
                ]),
            )
            await callback.answer()
            return

        if sent_to == 0:
            await callback.message.edit_text(
                "❌ Не удалось отправить — родитель заблокировал бота или не запускал /start.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="« Назад", callback_data=back_cb)],
                ]),
            )
            await callback.answer()
            return

        status_line = f"✅ Счёт отправлен родителю ({sent_to} из {total_rec})"
        if sent_invoices:
            status_line += f" + {sent_invoices} кнопок оплаты"
        logger.info(
            "Счёт отправлен student=%s period=%s recipients=%s sent=%s",
            student_id, period_month, total_rec, sent_to,
        )
        await callback.message.edit_text(
            status_line,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад к ученикам", callback_data=back_cb)],
                [InlineKeyboardButton(text="« В меню", callback_data="admin:menu")],
            ]),
        )
        await callback.answer()
    finally:
        _sending_in_progress.discard(lock_key)

