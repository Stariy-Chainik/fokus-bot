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
        [InlineKeyboardButton(text="🔄 Пересобрать billing из lessons", callback_data="diag:rebuild")],
        [InlineKeyboardButton(text="« Назад", callback_data="admin:menu")],
    ])


@router.callback_query(F.data == "admin:diagnostics")
async def cb_diagnostics_menu(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("Диагностика:", reply_markup=_diag_menu())
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
            "Результат проверки:",
            "",
            f"Individual занятий без billing: {len(report.individual_without_billing)}",
        ]
        if report.individual_without_billing:
            lines.append("  " + ", ".join(report.individual_without_billing[:10]))
        lines.append(f"Billing без занятия: {len(report.billing_without_lesson)}")
        if report.billing_without_lesson:
            lines.append("  " + ", ".join(report.billing_without_lesson[:10]))
        if not report.individual_without_billing and not report.billing_without_lesson:
            lines.append("\n✅ Данные консистентны")
        await callback.message.edit_text("\n".join(lines), reply_markup=_diag_menu())
    except Exception as exc:
        logger.error("Ошибка диагностики: %s", exc)
        await callback.message.edit_text(f"Ошибка: {exc}", reply_markup=_diag_menu())


@router.callback_query(F.data == "diag:rebuild")
async def cb_rebuild_confirm(callback: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "Пересборка удалит все billing и создаст заново из lessons.\n"
        "payment_id в billing будет потерян!\n\nВы уверены?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, пересобрать", callback_data="diag:rebuild_confirm")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:diagnostics")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "diag:rebuild_confirm")
async def cb_rebuild_do(
    callback: CallbackQuery, user: User | None, diagnostics_service: DiagnosticsService,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer("Пересобираю billing...")
    try:
        report = await diagnostics_service.rebuild_billing()
        lines = ["Пересборка завершена.", "", f"Создано строк: {report.rebuilt_billing_count}"]
        if report.errors:
            lines.append(f"\nОшибки ({len(report.errors)}):")
            lines.extend(f"  {e}" for e in report.errors[:5])
        else:
            lines.append("\n✅ Без ошибок")
        await callback.message.edit_text("\n".join(lines), reply_markup=_diag_menu())
    except Exception as exc:
        logger.error("Ошибка пересборки billing: %s", exc)
        await callback.message.edit_text(f"Ошибка пересборки: {exc}", reply_markup=_diag_menu())
