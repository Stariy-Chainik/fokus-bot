from __future__ import annotations
import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from bot.services import (
    StudentService,
)
from bot.handlers.common import show_card
from bot.screens.cards import admin_student_card_view
from bot.keyboards.admin import (
    kb_student_card,
)

logger = logging.getLogger(__name__)
router = Router(name="admin_students")




# ─── Общий рендер карточки ученика ────────────────────────────────────────────

async def _render_student_card(
    callback: CallbackQuery, student_id: str, back_cb: str,
    student_service: StudentService,
) -> None:
    card = await student_service.get_student_card(student_id)
    if card is None:
        await callback.answer("Ученик не найден", show_alert=True)
        return
    view = admin_student_card_view(card)
    await show_card(
        callback,
        view.text,
        reply_markup=kb_student_card(
            student_id, has_partner=view.has_partner, back_cb=back_cb,
            tier_toggle=view.tier_toggle,
            has_groups=view.has_groups,
            client_rows=view.client_rows,
        ),
    )

