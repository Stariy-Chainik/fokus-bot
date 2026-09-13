"""Родитель: оплата счёта (Telegram). Выбор педагогов, способы, чек; подтверждение — админ.

Данные (`bot/services/parent_views.py`) и экраны (`bot/screens/parent_bills.py`) общие с MAX.
"""
from __future__ import annotations
import logging

from aiogram import F
from aiogram.filters import StateFilter
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, BufferedInputFile

from typing import cast

from bot.handlers.filters import AdminOnly
from bot.models import User
from bot.models.enums import PaymentStatus
from bot.repositories import StudentRepository, ClientRepository, UserRepository
from bot.services import PaymentService
from bot.services.payment_methods import (
    CASH, RECEIPT_UNKNOWN, from_callback_code,
)
from bot.services.payment_watcher import start_payment_watch
from bot.services.parent_notifier import resolve_notifier, parse_addr, tg_addr
from bot.services.parent_views import (
    period_label as _period_label, unpaid_for, selected_from, selection_fsm_data,
    client_contact, qr_png, breakdown_lines, admin_confirm_rows, receipt_caption, cash_notice,
)
from bot.screens.adapters import to_aiogram_markup
from bot.screens import cb as cb_btn
from bot.screens.parent_bills import (
    bill_back_rows, teacher_select_screen, methods_screen, cash_screen, bank_screen,
    sbp_screen, online_pay_screen, receipt_prompt_screen, receipt_sent_screen, cash_sent_screen,
)
from bot.services.cloudkassir_service import CloudKassirService
from bot.states import ReceiptStates
from config.settings import settings
from bot.utils.callbacks import (
    CashNotifyCb,
    ClientPayCb,
    PayMethodCb,
    PaySelectToggleCb,
    ReceiptConfirmCb,
    ReceiptConfirmPartialCb,
    ReceiptPickCb,
    ReceiptRejectCb,
    ReceiptUploadCb,
)
from ._base import router

logger = logging.getLogger(__name__)


async def _edit(callback: CallbackQuery, screen: tuple) -> None:
    text, rows = screen
    try:
        await callback.message.edit_text(text, reply_markup=to_aiogram_markup(rows))
    except TelegramBadRequest:
        pass


async def _get_student_and_total(
    callback: CallbackQuery, student_repo: StudentRepository,
    payment_service: PaymentService, student_id: str, period_month: str,
) -> tuple | None:
    """(student, unpaid_total, unpaid) или None (с алертом)."""
    all_students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    student = next((s for s in all_students if s.student_id == student_id), None)
    if not student:
        await callback.answer("Нет доступа", show_alert=True)
        return None
    total, unpaid = await unpaid_for(student, period_month, payment_service)
    if total == 0:
        await callback.answer("Нет неоплаченных начислений", show_alert=True)
        return None
    return student, total, unpaid


_yookassa_on = lambda: bool(settings.yookassa_shop_id and settings.yookassa_secret_key)  # noqa: E731


async def _show_methods(callback: CallbackQuery, student_id: str, period_month: str, sel: list, who: str) -> None:
    await _edit(callback, methods_screen(
        student_id, period_month, who, sel, _yookassa_on(),
        cash=settings.payment_cash_enabled,
    ))


async def _show_teacher_select(
    callback: CallbackQuery, state: FSMContext, student_id: str, period_month: str, unpaid: list,
) -> None:
    data = await state.get_data()
    if data.get("pay_sel_key") != f"{student_id}:{period_month}":
        await state.update_data(
            pay_sel_key=f"{student_id}:{period_month}",
            pay_sel=[u["tid"] for u in unpaid], pay_unpaid=unpaid,
        )
        data = await state.get_data()
    chosen = set(data.get("pay_sel") or [])
    await _edit(callback, teacher_select_screen(
        student_id, period_month, data.get("pay_student_name") or "", unpaid, chosen,
    ))


@router.callback_query(F.data.startswith("client_pay:"))
async def cb_client_pay(
    callback: CallbackQuery, state: FSMContext,
    student_repo: StudentRepository, payment_service: PaymentService,
) -> None:
    try:
        cb = ClientPayCb.unpack(callback.data)
    except ValueError:
        await callback.answer("Ошибка данных", show_alert=True)
        return
    student_id, period_month = cb.student_id, cb.period_month
    preselect = cb.teacher_id  # client_pay:{sid}:{period}:{teacher_id} — из «Занятий»
    result = await _get_student_and_total(callback, student_repo, payment_service, student_id, period_month)
    if result is None:
        return
    student, total, unpaid = result
    await state.update_data(pay_sel_key=None, pay_sel=[], pay_unpaid=[], pay_student_name=student.name)
    if preselect and any(u["tid"] == preselect for u in unpaid):
        sel = [u for u in unpaid if u["tid"] == preselect]
        await state.update_data(pay_sel_key=f"{student_id}:{period_month}", pay_sel=[preselect], pay_unpaid=unpaid)
        await _show_methods(callback, student_id, period_month, sel, student.name)
    elif len(unpaid) > 1:
        await _show_teacher_select(callback, state, student_id, period_month, unpaid)
    else:
        await _show_methods(callback, student_id, period_month, unpaid, student.name)
    await callback.answer()


@router.callback_query(F.data.startswith("pselt:"))
async def cb_pay_select_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    idx = PaySelectToggleCb.unpack(callback.data).idx
    data = await state.get_data()
    unpaid = data.get("pay_unpaid") or []
    key = data.get("pay_sel_key") or ""
    if not unpaid or idx >= len(unpaid) or ":" not in key:
        await callback.answer("Сессия оплаты устарела, откройте счёт заново", show_alert=True)
        return
    student_id, period_month = key.split(":", 1)
    chosen = set(data.get("pay_sel") or [])
    chosen.symmetric_difference_update({unpaid[idx]["tid"]})
    await state.update_data(pay_sel=list(chosen))
    await _show_teacher_select(callback, state, student_id, period_month, unpaid)
    await callback.answer()


@router.callback_query(F.data == "pselgo")
async def cb_pay_select_go(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    unpaid = data.get("pay_unpaid") or []
    key = data.get("pay_sel_key") or ""
    if not unpaid or ":" not in key:
        await callback.answer("Сессия оплаты устарела, откройте счёт заново", show_alert=True)
        return
    student_id, period_month = key.split(":", 1)
    sel = selected_from(data, student_id, period_month, unpaid)
    await _show_methods(callback, student_id, period_month, sel, data.get("pay_student_name") or "")
    await callback.answer()


@router.callback_query(F.data.startswith("pay_method:"))
async def cb_pay_method(
    callback: CallbackQuery, state: FSMContext,
    student_repo: StudentRepository, payment_service: PaymentService,
    client_repo: ClientRepository, user_repo: UserRepository,
) -> None:
    try:
        cb = PayMethodCb.unpack(callback.data)
    except ValueError:
        await callback.answer("Ошибка данных", show_alert=True)
        return
    method, student_id, period_month = cb.method, cb.student_id, cb.period_month
    result = await _get_student_and_total(callback, student_repo, payment_service, student_id, period_month)
    if result is None:
        return
    student, _, unpaid = result
    sel = selected_from(await state.get_data(), student_id, period_month, unpaid)
    total = sum(u["amount"] for u in sel)
    sel_tids = [u["tid"] for u in sel]
    partial = len(sel) < len(unpaid)
    await state.update_data(**selection_fsm_data(sel, unpaid))

    if method == "cash":
        await _edit(callback, cash_screen(total, student_id, period_month))

    elif method == "bank":
        await _edit(callback, bank_screen(total, student_id, period_month, settings.payment_bank_details))
        png = qr_png(student.name, period_month, total)
        try:
            if png:
                await callback.message.answer_photo(
                    BufferedInputFile(png, filename="qr.png"),
                    caption=f"QR-код для оплаты — {total} руб., {student.name}",
                )
            elif settings.payment_qr_image_url:
                await callback.message.answer_photo(settings.payment_qr_image_url, caption="QR-код для оплаты")
        except Exception as exc:
            logger.warning("Не удалось отправить QR-код: %s", exc)

    elif method == "sbp":
        await _edit(callback, sbp_screen(total, student_id, period_month, settings.payment_sbp_details))

    elif method in ("ysbp", "yookassa"):
        try:
            phone, email = await client_contact(student, client_repo)
            url, payment_id = await payment_service.create_yookassa_payment(
                student.student_id, student.name, period_month, total, sbp=(method == "ysbp"),
                customer_phone=phone, customer_email=email,
                teacher_ids=sel_tids if partial else None,
            )
            start_payment_watch(
                payment_id, student.student_id, student.name, period_month,
                payment_service, callback.bot, user_repo, parent_addr=tg_addr(callback.from_user.id),
                teacher_ids=sel_tids if partial else None,
            )
            await _edit(callback, online_pay_screen(method, total, url, student_id, period_month))
        except Exception as exc:
            logger.error("Ошибка создания платежа ЮКасса (%s): %s", method, exc)
            await callback.answer("Ошибка при создании платежа. Попробуйте другой способ.", show_alert=True)
            return

    await callback.answer()


@router.callback_query(F.data.startswith("cash_notify:"))
async def cb_cash_notify(
    callback: CallbackQuery, state: FSMContext,
    student_repo: StudentRepository, payment_service: PaymentService, user_repo: UserRepository,
) -> None:
    cb = CashNotifyCb.unpack(callback.data)
    student_id, period_month = cb.student_id, cb.period_month
    result = await _get_student_and_total(callback, student_repo, payment_service, student_id, period_month)
    if result is None:
        return
    student, _, unpaid = result
    sel = selected_from(await state.get_data(), student_id, period_month, unpaid)
    total = sum(u["amount"] for u in sel)
    partial = len(sel) < len(unpaid)
    sel_pids = ".".join(str(u["pid"]) for u in sel if u["pid"])
    bills_map = await payment_service.compute_bills_for_student_period(student_id, period_month)
    ledgers = await payment_service.ledger_for(student, period_month)
    breakdown = "\n".join(breakdown_lines(bills_map, [u["tid"] for u in sel], ledgers=ledgers))
    msg = cash_notice(student.name, period_month, total, breakdown)
    kb = to_aiogram_markup(admin_confirm_rows(
        student_id, period_month, sel_pids, partial,
        tg_addr(callback.from_user.id), total, CASH,
    ))
    for admin in await user_repo.get_admins():
        try:
            await callback.bot.send_message(admin.tg_id, msg, reply_markup=kb)
        except TelegramAPIError as exc:
            logger.warning("Не удалось уведомить админа о наличной оплате tg_id=%s: %s", admin.tg_id, exc)
    await _edit(callback, cash_sent_screen(student_id, period_month))
    await callback.answer()


@router.callback_query(F.data.startswith("receipt_upload:"))
async def cb_receipt_upload(
    callback: CallbackQuery, state: FSMContext, student_repo: StudentRepository,
) -> None:
    try:
        cb = ReceiptUploadCb.unpack(callback.data)
    except ValueError:
        await callback.answer("Ошибка данных", show_alert=True)
        return
    method, student_id, period_month = cb.method, cb.student_id, cb.period_month
    all_students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not any(s.student_id == student_id for s in all_students):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(ReceiptStates.waiting_for_receipt)
    await state.update_data(receipt_method=method, receipt_student_id=student_id, receipt_period_month=period_month)
    await _edit(callback, receipt_prompt_screen(student_id, period_month))
    await callback.answer()


@router.message(ReceiptStates.waiting_for_receipt, F.photo | F.document)
async def on_receipt_photo(
    message: Message, state: FSMContext,
    payment_service: PaymentService, student_repo: StudentRepository, user_repo: UserRepository,
) -> None:
    data = await state.get_data()
    student_id = data["receipt_student_id"]
    period_month = data["receipt_period_month"]
    method = data.get("receipt_method", "bank")
    await state.clear()

    students = await student_repo.get_by_parent_tg_id(message.from_user.id)
    student = next((s for s in students if s.student_id == student_id), None)
    student_name = student.name if student else student_id

    sel_total = data.get("receipt_sel_total")
    sel_pids = data.get("receipt_sel_pids") or ""
    sel_partial = bool(data.get("receipt_sel_partial"))
    if sel_total is not None:
        total = sel_total
    elif student:
        total, _ = await unpaid_for(student, period_month, payment_service)
    else:
        bills = await payment_service.compute_bills_for_student_period(student_id, period_month)
        total = sum(agg.total for agg in bills.values())

    bills_map = await payment_service.compute_bills_for_student_period(student_id, period_month)
    ledgers = await payment_service.ledger_for(student, period_month) if student else {}
    sel_tids = data.get("receipt_sel_tids") or list(bills_map)
    caption = receipt_caption(method, student_name, period_month, total,
                              "\n".join(breakdown_lines(bills_map, sel_tids, ledgers=ledgers)))
    confirm_kb = to_aiogram_markup(admin_confirm_rows(
        student_id, period_month, sel_pids, sel_partial,
        tg_addr(message.from_user.id), total, method,
    ))
    for admin in await user_repo.get_admins():
        try:
            if message.photo:
                await message.bot.send_photo(admin.tg_id, message.photo[-1].file_id, caption=caption, reply_markup=confirm_kb)
            else:
                await message.bot.send_document(admin.tg_id, message.document.file_id, caption=caption, reply_markup=confirm_kb)
        except TelegramAPIError as exc:
            logger.warning("Не удалось отправить чек админу tg_id=%s: %s", admin.tg_id, exc)

    text, rows = receipt_sent_screen(student_id, period_month)
    await message.answer(text, reply_markup=to_aiogram_markup(rows))


@router.callback_query(F.data.startswith("rcpt_no:"), AdminOnly())
async def cb_receipt_reject(
    callback: CallbackQuery,
    user: User,
    student_repo: StudentRepository,
) -> None:
    """Админ не подтверждает оплату: счёт остаётся неоплаченным, родителю — уведомление."""
    cb = ReceiptRejectCb.unpack(callback.data)
    student_id, period_month, parent_raw = cb.student_id, cb.period_month, cb.parent_raw
    student = await student_repo.get_by_id(student_id)
    student_name = student.name if student else student_id

    old_text = callback.message.caption or callback.message.text or ""
    suffix = "\n\n❌ Оплата не подтверждена"
    try:
        if callback.message.caption is not None:
            await callback.message.edit_caption(caption=old_text + suffix, reply_markup=None)
        else:
            await callback.message.edit_text(old_text + suffix, reply_markup=None)
    except TelegramBadRequest:
        pass

    ok = await resolve_notifier(callback.bot).send(
        parse_addr(parent_raw),
        f"❌ Оплата за {_period_label(period_month)} ({student_name}) не подтверждена "
        f"администратором.\nПроверьте чек и сумму или свяжитесь со школой.",
        rows=bill_back_rows(student_id, period_month),
    )
    if not ok:
        logger.warning("Не удалось уведомить родителя %s об отказе", parent_raw)
    logger.info("Админ %s не подтвердил оплату student=%s period=%s",
                callback.from_user.id, student_id, period_month)
    await callback.answer("Оплата не подтверждена")


@router.callback_query(F.data.startswith("rcpp:"), AdminOnly())
async def cb_receipt_confirm_partial(
    callback: CallbackQuery,
    user: User,
    payment_service: PaymentService,
    student_repo: StudentRepository,
    client_repo: ClientRepository,
    cloudkassir_service: CloudKassirService,
) -> None:
    """Подтверждение выборочной оплаты: только перечисленные счета PAY-…"""
    cb = ReceiptConfirmPartialCb.unpack(callback.data)
    student_id, period_month, pids_raw = cb.student_id, cb.period_month, cb.pids
    claimed = cb.claimed  # сумма из чека
    payment_method = from_callback_code(cb.method_code)
    payment_ids = [f"PAY-{int(p):06d}" for p in pids_raw.split(".") if p.isdigit()]
    if not payment_ids:
        await callback.answer("Ошибка данных", show_alert=True)
        return

    repo = payment_service._payment_repo
    confirmed_total = 0
    count = 0
    if claimed:
        # Зачитываем ровно сумму чека по остаткам выбранных педагогов (остаток мог вырасти)
        student_row = await student_repo.get_by_id(student_id)
        teacher_ids = []
        for pid in payment_ids:
            row = await repo.get_by_id(pid)
            if row and row.teacher_id not in teacher_ids:
                teacher_ids.append(row.teacher_id)
        confirmed_total, count = await payment_service.record_payment(
            student_id, student_row.name if student_row else student_id, period_month,
            claimed, callback.from_user.id, teacher_ids or None, "чек", payment_method,
        )
    else:  # старые кнопки без суммы — закрываем остатки целиком
        for pid in payment_ids:
            row = await repo.get_by_id(pid)
            if row and row.status != PaymentStatus.PAID:
                if await payment_service.confirm_payment(
                    pid, callback.from_user.id, payment_method,
                ):
                    count += 1
                    confirmed_total += row.total_amount
    if count == 0:
        await callback.answer("Счета уже подтверждены или не найдены", show_alert=True)
        return

    old_text = callback.message.caption or callback.message.text or ""
    suffix = f"\n\n✅ Оплата подтверждена ({count} счёт(а), {confirmed_total} руб.)"
    try:
        if callback.message.caption is not None:
            await callback.message.edit_caption(caption=old_text + suffix, reply_markup=None)
        else:
            await callback.message.edit_text(old_text + suffix, reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("Оплата подтверждена")

    student = await student_repo.get_by_id(student_id)
    if student and confirmed_total > 0 and cloudkassir_service._public_id:
        phone = None
        if student.client_id:
            client = await client_repo.get_by_id(student.client_id)
            phone = client.phone if client else None
        if phone:
            await cloudkassir_service.send_income_receipt(
                phone, student.name, period_month, confirmed_total,
            )


@router.callback_query(F.data.startswith("receipt_confirm:"), AdminOnly())
async def cb_receipt_confirm(
    callback: CallbackQuery,
    user: User,
    payment_service: PaymentService,
    student_repo: StudentRepository,
    client_repo: ClientRepository,
    cloudkassir_service: CloudKassirService,
) -> None:
    cb = ReceiptConfirmCb.unpack(callback.data)
    student_id, period_month = cb.student_id, cb.period_month
    claimed = cb.claimed  # сумма из чека
    payment_method = from_callback_code(cb.method_code)

    # Получаем сумму к подтверждению до confirm (после — статус уже PAID)
    student = await student_repo.get_by_id(student_id)
    pending_total = 0
    if student:
        ledgers = await payment_service.ledger_for(student, period_month)
        pending_total = sum(ledger.remainder for ledger in ledgers.values())

    if claimed:
        pending_total, count = await payment_service.record_payment(
            student_id, student.name if student else student_id, period_month,
            claimed, callback.from_user.id, None, "чек", payment_method,
        )
    else:  # старые кнопки без суммы — закрываем все остатки
        count = await payment_service.confirm_period(
            student_id, period_month, callback.from_user.id, payment_method,
        )
    if count > 0:
        old_text = callback.message.caption or callback.message.text or ""
        confirmed_suffix = "\n\n✅ Оплата подтверждена"
        try:
            if callback.message.caption is not None:
                await callback.message.edit_caption(
                    caption=old_text + confirmed_suffix,
                    reply_markup=None,
                )
            else:
                await callback.message.edit_text(
                    old_text + confirmed_suffix,
                    reply_markup=None,
                )
        except TelegramBadRequest:
            pass
        await callback.answer("Оплата подтверждена")

        # Фискальный чек через CloudKassir
        if student and pending_total > 0 and cloudkassir_service._public_id:
            phone = None
            if student.client_id:
                client = await client_repo.get_by_id(student.client_id)
                phone = client.phone if client else None
            if phone:
                await cloudkassir_service.send_income_receipt(
                    phone, student.name, period_month, pending_total,
                )
            else:
                logger.warning(
                    "CloudKassir: нет телефона для student=%s, чек не выбит", student_id,
                )
    else:
        await callback.answer("Счёт уже подтверждён или не найден", show_alert=True)


# ─── Чек, присланный без шага «Прикрепить чек» ────────────────────────────────
# Родители часто отправляют фото чека просто так (или после перезапуска бота, когда
# состояние диалога сброшено). Раньше такое сообщение молча терялось. Теперь: один
# неоплаченный счёт — пересылаем админам сразу; несколько — просим выбрать.

async def _unpaid_bills_of_parent(students: list, payment_service: PaymentService) -> list:
    """[(student, period, total)] по текущему и прошлому месяцу."""
    from bot.utils.dates import last_periods
    out = []
    for student in students:
        for period in last_periods(2):
            total, _ = await unpaid_for(student, period, payment_service)
            if total > 0:
                out.append((student, period, total))
    return out


def _file_of(message: Message) -> tuple[str, str] | None:
    if message.photo:
        return "photo", message.photo[-1].file_id
    if message.document:
        return "document", message.document.file_id
    return None


async def _forward_receipt(
    bot, user_repo: UserRepository, kind: str, file_id: str, caption: str, rows,
) -> int:
    kb = to_aiogram_markup(rows)
    sent = 0
    for admin in await user_repo.get_admins():
        try:
            if kind == "photo":
                await bot.send_photo(admin.tg_id, file_id, caption=caption, reply_markup=kb)
            else:
                await bot.send_document(admin.tg_id, file_id, caption=caption, reply_markup=kb)
            sent += 1
        except TelegramAPIError as exc:
            logger.warning("Не удалось отправить чек админу tg_id=%s: %s", admin.tg_id, exc)
    return sent


async def _send_unbound_receipt(
    bot, user_repo: UserRepository, payment_service: PaymentService,
    student, period_month: str, total: int, kind: str, file_id: str, parent_tg_id: int,
) -> None:
    bills_map = await payment_service.compute_bills_for_student_period(student.student_id, period_month)
    ledgers = await payment_service.ledger_for(student, period_month)
    caption = receipt_caption("bank", student.name, period_month, total,
                              "\n".join(breakdown_lines(bills_map, list(bills_map), ledgers=ledgers)))
    rows = admin_confirm_rows(
        student.student_id, period_month, "", False,
        tg_addr(parent_tg_id), total, RECEIPT_UNKNOWN,
    )
    await _forward_receipt(bot, user_repo, kind, file_id, caption, rows)
    logger.info("Чек без шага «Прикрепить»: tg_id=%s → %s %s", parent_tg_id, student.student_id, period_month)


@router.message(StateFilter(None), F.photo | F.document)
async def on_unbound_receipt(
    message: Message, user: User | None, state: FSMContext,
    student_repo: StudentRepository, payment_service: PaymentService, user_repo: UserRepository,
) -> None:
    if user is not None and (user.is_admin or user.teacher_id):
        return
    students = await student_repo.get_by_parent_tg_id(message.from_user.id)
    if not students:
        return
    file = _file_of(message)
    if file is None:
        return
    kind, file_id = file
    bills = await _unpaid_bills_of_parent(students, payment_service)
    if not bills:
        await message.answer(
            "📎 Файл получил, но неоплаченных счетов сейчас нет. "
            "Если это чек за другой период — напишите администратору.",
            reply_markup=to_aiogram_markup([[cb_btn("« Меню", "go:home")]]),
        )
        return
    if len(bills) == 1:
        student, period_month, total = bills[0]
        await _send_unbound_receipt(message.bot, user_repo, payment_service,
                                    student, period_month, total, kind, file_id, message.from_user.id)
        text, rows = receipt_sent_screen(student.student_id, period_month)
        await message.answer(text, reply_markup=to_aiogram_markup(rows))
        return
    await state.set_state(ReceiptStates.choosing_bill)
    await state.update_data(rc_kind=kind, rc_file_id=file_id)
    rows = [[cb_btn(f"{s.name} · {_period_label(p)} — {t} руб.", f"rcpick:{s.student_id}:{p}")]
            for s, p, t in bills]
    rows.append([cb_btn("« Отмена", "go:home")])
    await message.answer("📎 Чек получил. За какой счёт эта оплата?", reply_markup=to_aiogram_markup(rows))


@router.callback_query(F.data.startswith("rcpick:"), ReceiptStates.choosing_bill)
async def cb_receipt_pick(
    callback: CallbackQuery, state: FSMContext,
    student_repo: StudentRepository, payment_service: PaymentService, user_repo: UserRepository,
) -> None:
    cb = ReceiptPickCb.unpack(callback.data)
    student_id, period_month = cb.student_id, cb.period_month
    data = await state.get_data()
    await state.clear()
    kind, file_id = data.get("rc_kind"), data.get("rc_file_id")
    students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    student = next((s for s in students if s.student_id == student_id), None)
    if student is None or not file_id:
        await callback.answer("Не удалось привязать чек, отправьте его ещё раз", show_alert=True)
        return
    total, _ = await unpaid_for(student, period_month, payment_service)
    await _send_unbound_receipt(callback.bot, user_repo, payment_service,
                                student, period_month, total, cast(str, kind), file_id, callback.from_user.id)
    await _edit(callback, receipt_sent_screen(student_id, period_month))
    await callback.answer()
