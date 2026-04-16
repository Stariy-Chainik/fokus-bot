from __future__ import annotations
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.models import User
from bot.services import DiagnosticsService
from bot.keyboards.admin import kb_back

logger = logging.getLogger(__name__)
router = Router(name="admin_diagnostics")


def _is_admin(user: User | None) -> bool:
    return user is not None and user.is_admin


def _diag_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Проверка целостности", callback_data="diag:check")],
        [InlineKeyboardButton(text="« Назад", callback_data="admin:menu")],
    ])


@router.callback_query(F.data == "admin:diagnostics")
async def cb_diagnostics_menu(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("<b>Диагностика:</b>", reply_markup=_diag_menu())
    await callback.answer()


@router.callback_query(F.data == "diag:check")
async def cb_check(
    callback: CallbackQuery, user: User | None, diagnostics_service: DiagnosticsService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer("Выполняю проверку...")
    try:
        report = await diagnostics_service.run_consistency_check()
        lines = [
            "<b>Результат проверки:</b>",
            "",
            f"Занятий с несуществующим педагогом: {len(report.lessons_with_missing_teacher)}",
        ]
        if report.lessons_with_missing_teacher:
            lines.append("  " + ", ".join(report.lessons_with_missing_teacher[:10]))
        lines.append(f"Занятий с несуществующим учеником: {len(report.lessons_with_missing_student)}")
        if report.lessons_with_missing_student:
            lines.append("  " + ", ".join(report.lessons_with_missing_student[:10]))
        if not report.lessons_with_missing_teacher and not report.lessons_with_missing_student:
            lines.append("\n✅ Данные консистентны")
        await callback.message.edit_text("\n".join(lines), reply_markup=_diag_menu())
    except Exception as exc:
        logger.error("Ошибка диагностики: %s", exc)
        await callback.message.edit_text(f"Ошибка: {exc}", reply_markup=_diag_menu())
