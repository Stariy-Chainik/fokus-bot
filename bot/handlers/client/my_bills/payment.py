from __future__ import annotations
import logging

from aiogram import F
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message


from bot.models import User
from bot.models.enums import PaymentStatus
from bot.repositories import StudentRepository, ClientRepository, UserRepository
from bot.repositories.client_repo import ClientRepository
from bot.services import PaymentService
from bot.services.payment_watcher import start_payment_watch
from bot.services.cloudkassir_service import CloudKassirService
from bot.states import ReceiptStates
from bot.keyboards.client import (
    kb_bill_back, kb_payment_method, kb_pay_cash,
    kb_pay_receipt, kb_cancel_receipt,
)
from config.settings import settings

logger = logging.getLogger(__name__)

from ._base import router, _period_label


_METHOD_LABELS = {
    "cash": "💵 Наличные",
    "bank": "🏦 По реквизитам",
    "sbp":  "📱 СБП",
}


async def _client_contact(student, client_repo: ClientRepository) -> tuple:
    """(телефон, email) клиента для фискального чека ЮКассы."""
    if not student.client_id:
        return "", ""
    client = await client_repo.get_by_id(student.client_id)
    if not client:
        return "", ""
    return client.phone or "", client.email or ""


async def _get_student_and_total(
    callback: CallbackQuery,
    student_repo: StudentRepository,
    payment_service: PaymentService,
    student_id: str,
    period_month: str,
) -> tuple | None:
    """Возвращает (student, unpaid_total, unpaid) или None.

    unpaid — список неоплаченных начислений по педагогам:
    [{tid, name, amount, pid}], pid — числовая часть PAY-id (для коротких callback).
    """
    all_students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    student = next((s for s in all_students if s.student_id == student_id), None)
    if not student:
        await callback.answer("Нет доступа", show_alert=True)
        return None
    bills = await payment_service.compute_bills_for_student_period(student_id, period_month)
    invoices = await payment_service.get_or_create_invoices_for_student_period(student, period_month)
    paid_teachers = {inv.teacher_id for inv in invoices if inv.status == PaymentStatus.PAID}
    pid_by_teacher = {
        inv.teacher_id: int(inv.payment_id.split("-")[-1]) for inv in invoices
    }
    unpaid = sorted(
        [
            {"tid": tid, "name": agg["name"], "amount": agg["total"],
             "pid": pid_by_teacher.get(tid, 0)}
            for tid, agg in bills.items() if tid not in paid_teachers
        ],
        key=lambda x: x["name"],
    )
    total = sum(u["amount"] for u in unpaid)
    if total == 0:
        await callback.answer("Нет неоплаченных начислений", show_alert=True)
        return None
    return student, total, unpaid


def _breakdown_lines(bills: dict, tids: list, limit: int = 850) -> list[str]:
    """Разбивка для админа: педагог/абонемент — сумма и даты занятий.
    Если не влезает в подпись Telegram — короткий вариант (число занятий)."""
    full, short = [], []
    for tid in tids:
        agg = bills.get(tid)
        if not agg:
            continue
        items = sorted(agg.get("items") or [], key=lambda b: b.date)
        if agg.get("subscription") or not items:
            full.append(f"• {agg['name']} — {agg['total']} руб.")
            short.append(full[-1])
            continue
        dates = ", ".join(
            f"{b.date[8:10]}.{b.date[5:7]} ({b.duration_min}м{', группа' if b.lesson_type == 'group' else ''})"
            for b in items
        )
        full.append(f"• {agg['name']} — {agg['total']} руб.: {dates}")
        short.append(f"• {agg['name']} — {agg['total']} руб. ({len(items)} зан.)")
    return full if len("\n".join(full)) <= limit else short


def _selected(data: dict, student_id: str, period_month: str, unpaid: list) -> list:
    """Выбранные педагоги из FSM (валидные), по умолчанию — все неоплаченные."""
    if data.get("pay_sel_key") == f"{student_id}:{period_month}":
        chosen = set(data.get("pay_sel") or [])
        sel = [u for u in unpaid if u["tid"] in chosen]
        if sel:
            return sel
    return unpaid


async def _show_methods(
    callback: CallbackQuery, student_id: str, period_month: str, sel: list,
    student_name: str = "",
) -> None:
    total = sum(u["amount"] for u in sel)
    who = f" — {student_name}" if student_name else ""
    lines = [f"<b>💳 Оплата за {_period_label(period_month)}{who}</b>"]
    for u in sel:
        lines.append(f"  • {u['name']} — {u['amount']} руб.")
    lines.append(f"Сумма: <b>{total} руб.</b>\n\nВыберите способ оплаты:")
    lines.append(
        "\n<i>📱 СБП онлайн — оплата подтверждается автоматически, чек прикреплять не нужно.\n"
        "🏦 По реквизитам — после перевода обязательно прикрепите чек, "
        "иначе оплата не будет зачтена.</i>"
    )
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=kb_payment_method(
            student_id, period_month,
            cash=False, bank=True, sbp=False,
            yookassa=bool(settings.yookassa_shop_id and settings.yookassa_secret_key),
        ),
    )


async def _show_teacher_select(
    callback: CallbackQuery, state: FSMContext,
    student_id: str, period_month: str, unpaid: list,
) -> None:
    data = await state.get_data()
    if data.get("pay_sel_key") != f"{student_id}:{period_month}":
        await state.update_data(
            pay_sel_key=f"{student_id}:{period_month}",
            pay_sel=[u["tid"] for u in unpaid],
            pay_unpaid=unpaid,
        )
        data = await state.get_data()
    chosen = set(data.get("pay_sel") or [])
    rows = []
    for i, u in enumerate(unpaid):
        mark = "✅" if u["tid"] in chosen else "⬜"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {u['name']} — {u['amount']} руб.",
            callback_data=f"pselt:{i}",
        )])
    total = sum(u["amount"] for u in unpaid if u["tid"] in chosen)
    if total > 0:
        rows.append([InlineKeyboardButton(
            text=f"➡️ К оплате: {total} руб.", callback_data="pselgo",
        )])
    rows.append([InlineKeyboardButton(
        text="« К счёту", callback_data=f"client_bill:{student_id}:{period_month}",
    )])
    who = data.get("pay_student_name") or ""
    who_part = f" — {who}" if who else ""
    await callback.message.edit_text(
        f"<b>💳 Оплата за {_period_label(period_month)}{who_part}</b>\n"
        f"Отметьте, каких педагогов оплачиваете:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("client_pay:"))
async def cb_client_pay(
    callback: CallbackQuery,
    state: FSMContext,
    student_repo: StudentRepository,
    payment_service: PaymentService,
) -> None:
    # client_pay:{student_id}:{period_month}
    parts = callback.data.split(":", 2)
    if len(parts) < 3:
        await callback.answer("Ошибка данных", show_alert=True)
        return
    _, student_id, period_month = parts

    result = await _get_student_and_total(callback, student_repo, payment_service, student_id, period_month)
    if result is None:
        return
    student, total, unpaid = result

    # Сброс прежнего выбора при новом заходе в оплату
    await state.update_data(pay_sel_key=None, pay_sel=[], pay_unpaid=[],
                            pay_student_name=student.name)
    if len(unpaid) > 1:
        await _show_teacher_select(callback, state, student_id, period_month, unpaid)
    else:
        await _show_methods(callback, student_id, period_month, unpaid, student.name)
    await callback.answer()


@router.callback_query(F.data.startswith("pselt:"))
async def cb_pay_select_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    idx = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    unpaid = data.get("pay_unpaid") or []
    key = data.get("pay_sel_key") or ""
    if not unpaid or idx >= len(unpaid) or ":" not in key:
        await callback.answer("Сессия оплаты устарела, откройте счёт заново", show_alert=True)
        return
    student_id, period_month = key.split(":", 1)
    chosen = set(data.get("pay_sel") or [])
    tid = unpaid[idx]["tid"]
    chosen.symmetric_difference_update({tid})
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
    sel = _selected(data, student_id, period_month, unpaid)
    await _show_methods(callback, student_id, period_month, sel,
                        data.get("pay_student_name") or "")
    await callback.answer()


@router.callback_query(F.data.startswith("pay_method:"))
async def cb_pay_method(
    callback: CallbackQuery,
    state: FSMContext,
    student_repo: StudentRepository,
    payment_service: PaymentService,
    client_repo: ClientRepository,
    user_repo: UserRepository,
) -> None:
    # pay_method:{method}:{student_id}:{period_month}
    parts = callback.data.split(":", 3)
    if len(parts) < 4:
        await callback.answer("Ошибка данных", show_alert=True)
        return
    _, method, student_id, period_month = parts

    result = await _get_student_and_total(callback, student_repo, payment_service, student_id, period_month)
    if result is None:
        return
    _student, _, _unpaid = result
    _data = await state.get_data()
    _sel = _selected(_data, student_id, period_month, _unpaid)
    total = sum(u["amount"] for u in _sel)
    sel_tids = [u["tid"] for u in _sel]
    sel_pids = ".".join(str(u["pid"]) for u in _sel if u["pid"])
    sel_label = ", ".join(u["name"] for u in _sel)
    partial = len(_sel) < len(_unpaid)
    # выбранное — в FSM для флоу «прикрепить чек»
    await state.update_data(receipt_sel_pids=sel_pids, receipt_sel_label=sel_label,
                            receipt_sel_total=total, receipt_sel_partial=partial,
                            receipt_sel_tids=sel_tids)

    if method == "cash":
        text = (
            f"<b>💵 Оплата наличными</b>\n"
            f"Сумма: <b>{total} руб.</b>\n\n"
            f"Передайте деньги администратору или преподавателю.\n"
            f"Нажмите кнопку, чтобы уведомить администратора."
        )
        await callback.message.edit_text(text, reply_markup=kb_pay_cash(student_id, period_month))

    elif method == "bank":
        lines = [
            f"<b>🏦 Оплата по реквизитам</b>",
            f"Сумма: <b>{total} руб.</b>",
            "",
        ]
        if settings.payment_bank_details:
            lines += [settings.payment_bank_details.replace("\\n", "\n"), ""]
        lines.append("После оплаты прикрепите фото чека.")
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=kb_pay_receipt(method, student_id, period_month),
        )
        # Отправляем QR-код: к реквизитам добавляем назначение и сумму (ГОСТ Р 56042),
        # банк подставит их в платёжку при сканировании.
        qr_sent = False
        if settings.payment_qr_data:
            try:
                from io import BytesIO
                import qrcode
                from bot.utils.dates import display_period
                purpose = f"Оплата занятий, {_student.name}, {display_period(period_month)}"
                base = settings.payment_qr_data.replace("|INN=", "|PayeeINN=")  # ГОСТ-ключ ИНН
                qr_data = (
                    f"{base}"
                    f"|Purpose={purpose}"
                    f"|Sum={total * 100}"  # в копейках
                )
                img = qrcode.make(qr_data)
                buf = BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)
                from aiogram.types import BufferedInputFile
                await callback.message.answer_photo(
                    BufferedInputFile(buf.read(), filename="qr.png"),
                    caption=f"QR-код для оплаты — {total} руб., {_student.name}",
                )
                qr_sent = True
            except Exception as exc:
                logger.warning("Не удалось сгенерировать QR: %s", exc)
        if not qr_sent and settings.payment_qr_image_url:
            try:
                await callback.message.answer_photo(
                    settings.payment_qr_image_url,
                    caption="QR-код для оплаты",
                )
            except Exception as exc:
                logger.warning("Не удалось отправить QR-код: %s", exc)

    elif method == "sbp":
        lines = [
            f"<b>📱 Оплата через СБП</b>",
            f"Сумма: <b>{total} руб.</b>",
            "",
        ]
        if settings.payment_sbp_details:
            lines += [settings.payment_sbp_details, ""]
        lines.append("После оплаты прикрепите фото чека.")
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=kb_pay_receipt(method, student_id, period_month),
        )

    elif method == "ysbp":
        student = _student
        try:
            _phone, _email = await _client_contact(student, client_repo)
            url, payment_id = await payment_service.create_yookassa_payment(
                student.student_id, student.name, period_month, total, sbp=True,
                customer_phone=_phone, customer_email=_email,
                teacher_ids=sel_tids if partial else None,
            )
            start_payment_watch(
                payment_id, student.student_id, student.name, period_month,
                payment_service, callback.bot, user_repo, parent_tg_id=callback.from_user.id,
                teacher_ids=sel_tids if partial else None,
            )
            await callback.message.edit_text(
                f"<b>📱 Оплата через СБП</b>\n"
                f"Сумма: <b>{total} руб.</b>\n\n"
                f"Нажмите кнопку — откроется страница СБП (QR или переход в банк).\n"
                f"После оплаты статус обновится автоматически.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📱 Перейти к оплате", url=url)],
                    [InlineKeyboardButton(text="« К счёту", callback_data=f"client_bill:{student_id}:{period_month}")],
                ]),
            )
        except Exception as exc:
            logger.error("Ошибка создания СБП-платежа ЮКасса: %s", exc)
            await callback.answer("Ошибка при создании платежа. Попробуйте другой способ.", show_alert=True)
            return

    elif method == "yookassa":
        student = _student
        try:
            _phone, _email = await _client_contact(student, client_repo)
            url, payment_id = await payment_service.create_yookassa_payment(
                student.student_id, student.name, period_month, total,
                customer_phone=_phone, customer_email=_email,
                teacher_ids=sel_tids if partial else None,
            )
            start_payment_watch(
                payment_id, student.student_id, student.name, period_month,
                payment_service, callback.bot, user_repo, parent_tg_id=callback.from_user.id,
                teacher_ids=sel_tids if partial else None,
            )
            await callback.message.edit_text(
                f"<b>💳 Оплата картой онлайн</b>\n"
                f"Сумма: <b>{total} руб.</b>\n\n"
                f"Нажмите кнопку для перехода на страницу оплаты.\n"
                f"После оплаты статус обновится автоматически.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Перейти к оплате", url=url)],
                    [InlineKeyboardButton(text="« К счёту", callback_data=f"client_bill:{student_id}:{period_month}")],
                ]),
            )
        except Exception as exc:
            logger.error("Ошибка создания платежа ЮКасса: %s", exc)
            await callback.answer("Ошибка при создании платежа. Попробуйте другой способ.", show_alert=True)
            return

    await callback.answer()


@router.callback_query(F.data.startswith("cash_notify:"))
async def cb_cash_notify(
    callback: CallbackQuery,
    state: FSMContext,
    student_repo: StudentRepository,
    payment_service: PaymentService,
    user_repo: UserRepository,
) -> None:
    _, student_id, period_month = callback.data.split(":", 2)

    result = await _get_student_and_total(callback, student_repo, payment_service, student_id, period_month)
    if result is None:
        return
    student, _, unpaid = result
    data = await state.get_data()
    sel = _selected(data, student_id, period_month, unpaid)
    total = sum(u["amount"] for u in sel)
    partial = len(sel) < len(unpaid)
    confirm_cb = (
        f"rcpp:{student_id}:{period_month}:" + ".".join(str(u["pid"]) for u in sel if u["pid"])
        if partial else f"receipt_confirm:{student_id}:{period_month}"
    )
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=confirm_cb)],
        [InlineKeyboardButton(
            text="❌ Не подтверждать",
            callback_data=f"rcpt_no:{student_id}:{period_month}:{callback.from_user.id}",
        )],
    ])
    bills_map = await payment_service.compute_bills_for_student_period(student_id, period_month)
    breakdown = "\n".join(_breakdown_lines(bills_map, [u["tid"] for u in sel]))
    msg = (
        f"💵 Клиент сообщает об оплате наличными\n\n"
        f"Ученик: {student.name}\n"
        f"Период: {_period_label(period_month)}\n"
        f"Сумма: {total} руб."
        + (f"\n\n{breakdown}" if breakdown else "")
    )
    admins = await user_repo.get_admins()
    for admin in admins:
        try:
            await callback.bot.send_message(admin.tg_id, msg, reply_markup=confirm_kb)
        except TelegramAPIError as exc:
            logger.warning("Не удалось уведомить админа о наличной оплате tg_id=%s: %s", admin.tg_id, exc)

    await callback.message.edit_text(
        "✅ Администратор уведомлён. Ожидайте подтверждения.",
        reply_markup=kb_bill_back(student_id, period_month),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("receipt_upload:"))
async def cb_receipt_upload(
    callback: CallbackQuery,
    state: FSMContext,
    student_repo: StudentRepository,
) -> None:
    # receipt_upload:{method}:{student_id}:{period_month}
    parts = callback.data.split(":", 3)
    if len(parts) < 4:
        await callback.answer("Ошибка данных", show_alert=True)
        return
    _, method, student_id, period_month = parts

    all_students = await student_repo.get_by_parent_tg_id(callback.from_user.id)
    if not any(s.student_id == student_id for s in all_students):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(ReceiptStates.waiting_for_receipt)
    await state.update_data(
        receipt_method=method,
        receipt_student_id=student_id,
        receipt_period_month=period_month,
    )
    await callback.message.edit_text(
        "📎 Отправьте фото или документ чека об оплате:",
        reply_markup=kb_cancel_receipt(student_id, period_month),
    )
    await callback.answer()


@router.message(ReceiptStates.waiting_for_receipt, F.photo | F.document)
async def on_receipt_photo(
    message: Message,
    state: FSMContext,
    payment_service: PaymentService,
    student_repo: StudentRepository,
    user_repo: UserRepository,
) -> None:
    data = await state.get_data()
    student_id = data["receipt_student_id"]
    period_month = data["receipt_period_month"]
    method = data.get("receipt_method", "bank")
    await state.clear()

    students = await student_repo.get_by_parent_tg_id(message.from_user.id)
    student = next((s for s in students if s.student_id == student_id), None)
    student_name = student.name if student else student_id

    sel_partial = bool(data.get("receipt_sel_partial"))
    sel_total = data.get("receipt_sel_total")
    sel_label = data.get("receipt_sel_label") or ""
    sel_pids = data.get("receipt_sel_pids") or ""
    if sel_total is not None:
        total = sel_total
    else:
        bills = await payment_service.compute_bills_for_student_period(student_id, period_month)
        if student:
            invoices = await payment_service.get_or_create_invoices_for_student_period(student, period_month)
            paid_teachers = {inv.teacher_id for inv in invoices if inv.status == PaymentStatus.PAID}
            total = sum(agg["total"] for tid, agg in bills.items() if tid not in paid_teachers)
        else:
            total = sum(agg["total"] for agg in bills.values())
    method_label = _METHOD_LABELS.get(method, method)

    bills_map = await payment_service.compute_bills_for_student_period(student_id, period_month)
    sel_tids = data.get("receipt_sel_tids") or list(bills_map)
    breakdown = "\n".join(_breakdown_lines(bills_map, sel_tids))
    caption = (
        f"📎 Чек об оплате\n\n"
        f"Способ: {method_label}\n"
        f"Ученик: {student_name}\n"
        f"Период: {_period_label(period_month)}\n"
        f"Сумма: {total} руб."
        + (f"\n\n{breakdown}" if breakdown else "")
    )
    confirm_cb = (
        f"rcpp:{student_id}:{period_month}:{sel_pids}"
        if sel_partial and sel_pids else f"receipt_confirm:{student_id}:{period_month}"
    )
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=confirm_cb)],
        [InlineKeyboardButton(
            text="❌ Не подтверждать",
            callback_data=f"rcpt_no:{student_id}:{period_month}:{message.from_user.id}",
        )],
    ])

    admins = await user_repo.get_admins()
    for admin in admins:
        try:
            if message.photo:
                await message.bot.send_photo(
                    admin.tg_id, message.photo[-1].file_id,
                    caption=caption, reply_markup=confirm_kb,
                )
            else:
                await message.bot.send_document(
                    admin.tg_id, message.document.file_id,
                    caption=caption, reply_markup=confirm_kb,
                )
        except TelegramAPIError as exc:
            logger.warning("Не удалось отправить чек админу tg_id=%s: %s", admin.tg_id, exc)

    await message.answer(
        "✅ Чек отправлен администратору. Ожидайте подтверждения.",
        reply_markup=kb_bill_back(student_id, period_month),
    )


@router.callback_query(F.data.startswith("rcpt_no:"))
async def cb_receipt_reject(
    callback: CallbackQuery,
    user: User | None,
    student_repo: StudentRepository,
) -> None:
    """Админ не подтверждает оплату: счёт остаётся неоплаченным, родителю — уведомление."""
    if not user or not user.is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, period_month, parent_raw = callback.data.split(":", 3)
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

    try:
        await callback.bot.send_message(
            int(parent_raw),
            f"❌ Оплата за {_period_label(period_month)} ({student_name}) не подтверждена "
            f"администратором.\nПроверьте чек и сумму или свяжитесь со школой.",
            reply_markup=kb_bill_back(student_id, period_month),
        )
    except (TelegramAPIError, ValueError) as exc:
        logger.warning("Не удалось уведомить родителя об отказе: %s", exc)
    logger.info("Админ %s не подтвердил оплату student=%s period=%s",
                callback.from_user.id, student_id, period_month)
    await callback.answer("Оплата не подтверждена")


@router.callback_query(F.data.startswith("rcpp:"))
async def cb_receipt_confirm_partial(
    callback: CallbackQuery,
    user: User | None,
    payment_service: PaymentService,
    student_repo: StudentRepository,
    client_repo: ClientRepository,
    cloudkassir_service: CloudKassirService,
) -> None:
    """Подтверждение выборочной оплаты: только перечисленные счета PAY-…"""
    if not user or not user.is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, period_month, pids_raw = callback.data.split(":", 3)
    payment_ids = [f"PAY-{int(p):06d}" for p in pids_raw.split(".") if p.isdigit()]
    if not payment_ids:
        await callback.answer("Ошибка данных", show_alert=True)
        return

    repo = payment_service._payment_repo
    confirmed_total = 0
    count = 0
    for pid in payment_ids:
        row = await repo.get_by_id(pid)
        if row and row.status != PaymentStatus.PAID:
            if await payment_service.confirm_payment(pid, callback.from_user.id):
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


@router.callback_query(F.data.startswith("receipt_confirm:"))
async def cb_receipt_confirm(
    callback: CallbackQuery,
    user: User | None,
    payment_service: PaymentService,
    student_repo: StudentRepository,
    client_repo: ClientRepository,
    cloudkassir_service: CloudKassirService,
) -> None:
    if not user or not user.is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, student_id, period_month = callback.data.split(":", 2)

    # Получаем сумму к подтверждению до confirm (после — статус уже PAID)
    student = await student_repo.get_by_id(student_id)
    pending_total = 0
    if student:
        bills = await payment_service.compute_bills_for_student_period(student_id, period_month)
        invoices = await payment_service.get_or_create_invoices_for_student_period(student, period_month)
        paid_teachers = {inv.teacher_id for inv in invoices if inv.status == PaymentStatus.PAID}
        pending_total = sum(agg["total"] for tid, agg in bills.items() if tid not in paid_teachers)

    count = await payment_service.confirm_period(student_id, period_month, callback.from_user.id)
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
