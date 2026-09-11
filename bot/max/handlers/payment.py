"""MAX: оплата счёта — выбор педагогов, способы, СБП онлайн, реквизиты + QR + чек, наличные.

Чек и уведомление о наличных уходят администраторам в Telegram с теми же кнопками
подтверждения; ответ родителю приходит в MAX через ParentNotifier.
"""
from __future__ import annotations
import logging

from aiogram.types import BufferedInputFile
from aiogram.exceptions import TelegramAPIError
from maxapi import F
from maxapi.types import MessageCallback, MessageCreated, InputMediaBuffer

from config.settings import settings
from bot.services.payment_watcher import start_payment_watch
from bot.services.parent_notifier import max_addr
from bot.screens import cb
from bot.services.parent_views import (
    period_label, unpaid_for, selected_from, selection_fsm_data, client_contact, qr_png,
    breakdown_lines, admin_confirm_rows, receipt_caption, cash_notice,
)
from bot.screens.adapters import to_aiogram_markup
from bot.screens.parent_bills import (
    teacher_select_screen, methods_screen, cash_screen, bank_screen, sbp_screen,
    online_pay_screen, receipt_prompt_screen, receipt_sent_screen, cash_sent_screen,
)
from ..render import edit_screen, send_screen, alert
from ..states import MaxParentStates
from . import router
from ._common import require_parent

logger = logging.getLogger(__name__)


async def _student_and_unpaid(event, max_uid, student_repo, payment_service, student_id, period_month):
    students = await require_parent(event, student_repo, max_uid)
    student = next((s for s in students or [] if s.student_id == student_id), None)
    if student is None:
        if students:
            await alert(event, "Нет доступа")
        return None
    total, unpaid = await unpaid_for(student, period_month, payment_service)
    if total == 0:
        await alert(event, "Нет неоплаченных начислений")
        return None
    return student, total, unpaid


def _yookassa_on() -> bool:
    return bool(settings.yookassa_shop_id and settings.yookassa_secret_key)


async def _teacher_select(event, context, student_id, period_month, unpaid) -> None:
    data = await context.get_data()
    if data.get("pay_sel_key") != f"{student_id}:{period_month}":
        await context.update_data(pay_sel_key=f"{student_id}:{period_month}",
                                  pay_sel=[u["tid"] for u in unpaid], pay_unpaid=unpaid)
        data = await context.get_data()
    chosen = set(data.get("pay_sel") or [])
    await edit_screen(event, *teacher_select_screen(student_id, period_month, data.get("pay_student_name") or "", unpaid, chosen))


@router.message_callback(F.callback.payload.startswith("client_pay:"))
async def on_pay(event: MessageCallback, context, max_uid, student_repo, payment_service):
    _, student_id, period_month = event.callback.payload.split(":", 2)
    res = await _student_and_unpaid(event, max_uid, student_repo, payment_service, student_id, period_month)
    if res is None:
        return
    student, total, unpaid = res
    await context.update_data(pay_sel_key=None, pay_sel=[], pay_unpaid=[], pay_student_name=student.name)
    if len(unpaid) > 1:
        await _teacher_select(event, context, student_id, period_month, unpaid)
    else:
        await edit_screen(event, *methods_screen(student_id, period_month, student.name, unpaid, _yookassa_on()))


@router.message_callback(F.callback.payload.startswith("pselt:"))
async def on_select_toggle(event: MessageCallback, context):
    idx = int(event.callback.payload.split(":", 1)[1])
    data = await context.get_data()
    unpaid, key = data.get("pay_unpaid") or [], data.get("pay_sel_key") or ""
    if not unpaid or idx >= len(unpaid) or ":" not in key:
        await alert(event, "Сессия оплаты устарела, откройте счёт заново")
        return
    student_id, period_month = key.split(":", 1)
    chosen = set(data.get("pay_sel") or [])
    chosen.symmetric_difference_update({unpaid[idx]["tid"]})
    await context.update_data(pay_sel=list(chosen))
    await _teacher_select(event, context, student_id, period_month, unpaid)


@router.message_callback(F.callback.payload == "pselgo")
async def on_select_go(event: MessageCallback, context):
    data = await context.get_data()
    unpaid, key = data.get("pay_unpaid") or [], data.get("pay_sel_key") or ""
    if not unpaid or ":" not in key:
        await alert(event, "Сессия оплаты устарела, откройте счёт заново")
        return
    student_id, period_month = key.split(":", 1)
    sel = selected_from(data, student_id, period_month, unpaid)
    await edit_screen(event, *methods_screen(student_id, period_month, data.get("pay_student_name") or "", sel, _yookassa_on()))


@router.message_callback(F.callback.payload.startswith("pay_method:"))
async def on_method(event: MessageCallback, context, max_uid, student_repo, payment_service,
                    client_repo, user_repo, tg_bot, notifier):
    _, method, student_id, period_month = event.callback.payload.split(":", 3)
    res = await _student_and_unpaid(event, max_uid, student_repo, payment_service, student_id, period_month)
    if res is None:
        return
    student, _, unpaid = res
    sel = selected_from(await context.get_data(), student_id, period_month, unpaid)
    total = sum(u["amount"] for u in sel)
    sel_tids = [u["tid"] for u in sel]
    partial = len(sel) < len(unpaid)
    await context.update_data(**selection_fsm_data(sel, unpaid))

    if method == "cash":
        await edit_screen(event, *cash_screen(total, student_id, period_month))
    elif method == "bank":
        text, rows = bank_screen(total, student_id, period_month, settings.payment_bank_details)
        png = qr_png(student.name, period_month, total)
        if png:
            await edit_screen(event, text, [])
            await send_screen(event.bot, max_uid, f"QR-код для оплаты — {total} руб., {student.name}", rows,
                              media=InputMediaBuffer(png, filename="qr.png"))
        else:
            await edit_screen(event, text, rows)
    elif method == "sbp":
        await edit_screen(event, *sbp_screen(total, student_id, period_month, settings.payment_sbp_details))
    elif method in ("ysbp", "yookassa"):
        try:
            phone, email = await client_contact(student, client_repo)
            url, payment_id = await payment_service.create_yookassa_payment(
                student.student_id, student.name, period_month, total, sbp=(method == "ysbp"),
                customer_phone=phone, customer_email=email, teacher_ids=sel_tids if partial else None,
            )
            start_payment_watch(
                payment_id, student.student_id, student.name, period_month,
                payment_service, tg_bot, user_repo, parent_addr=max_addr(max_uid),
                teacher_ids=sel_tids if partial else None, notifier=notifier,
            )
            await edit_screen(event, *online_pay_screen(method, total, url, student_id, period_month))
        except Exception as exc:
            logger.error("MAX: ошибка создания платежа ЮКасса (%s): %s", method, exc)
            await alert(event, "Ошибка при создании платежа. Попробуйте другой способ.")


async def _notify_admins_tg(tg_bot, user_repo, text: str, rows, *, photo: bytes | None = None,
                            document: tuple[bytes, str] | None = None) -> None:
    kb = to_aiogram_markup(rows)
    for admin in await user_repo.get_admins():
        try:
            if photo is not None:
                await tg_bot.send_photo(admin.tg_id, BufferedInputFile(photo, filename="receipt.jpg"), caption=text, reply_markup=kb)
            elif document is not None:
                await tg_bot.send_document(admin.tg_id, BufferedInputFile(document[0], filename=document[1]), caption=text, reply_markup=kb)
            else:
                await tg_bot.send_message(admin.tg_id, text, reply_markup=kb)
        except TelegramAPIError as exc:
            logger.warning("Не удалось уведомить админа tg_id=%s: %s", admin.tg_id, exc)


@router.message_callback(F.callback.payload.startswith("cash_notify:"))
async def on_cash_notify(event: MessageCallback, context, max_uid, student_repo, payment_service, user_repo, tg_bot):
    _, student_id, period_month = event.callback.payload.split(":", 2)
    res = await _student_and_unpaid(event, max_uid, student_repo, payment_service, student_id, period_month)
    if res is None:
        return
    student, _, unpaid = res
    sel = selected_from(await context.get_data(), student_id, period_month, unpaid)
    total = sum(u["amount"] for u in sel)
    partial = len(sel) < len(unpaid)
    sel_pids = ".".join(str(u["pid"]) for u in sel if u["pid"])
    bills_map = await payment_service.compute_bills_for_student_period(student_id, period_month)
    breakdown = "\n".join(breakdown_lines(bills_map, [u["tid"] for u in sel]))
    await _notify_admins_tg(tg_bot, user_repo, cash_notice(student.name, period_month, total, breakdown),
                            admin_confirm_rows(student_id, period_month, sel_pids, partial, max_addr(max_uid)))
    await edit_screen(event, *cash_sent_screen(student_id, period_month))


@router.message_callback(F.callback.payload.startswith("receipt_upload:"))
async def on_receipt_upload(event: MessageCallback, context, max_uid, student_repo):
    _, method, student_id, period_month = event.callback.payload.split(":", 3)
    students = await require_parent(event, student_repo, max_uid)
    if not students or not any(s.student_id == student_id for s in students):
        return
    await context.set_state(MaxParentStates.waiting_receipt)
    await context.update_data(receipt_method=method, receipt_student_id=student_id, receipt_period_month=period_month)
    await edit_screen(event, *receipt_prompt_screen(student_id, period_month))


def _receipt_attachment(message):
    for att in message.body.attachments or []:
        kind = getattr(att, "type", None)
        kind = getattr(kind, "value", kind)
        url = getattr(getattr(att, "payload", None), "url", None)
        if url and kind in ("image", "file"):
            return kind, url, getattr(att, "filename", None) or "receipt"
    return None


@router.message_created(MaxParentStates.waiting_receipt)
async def on_receipt_message(event: MessageCreated, context, max_uid, student_repo, payment_service, user_repo, tg_bot):
    att = _receipt_attachment(event.message)
    if att is None:
        await event.message.answer("Отправьте фото или файл чека.")
        return
    data = await context.get_data()
    await context.clear()
    student_id, period_month = data["receipt_student_id"], data["receipt_period_month"]
    method = data.get("receipt_method", "bank")
    students = await student_repo.get_by_parent_max_id(max_uid)
    student = next((s for s in students if s.student_id == student_id), None)
    student_name = student.name if student else student_id
    sel_total, sel_pids, sel_partial = data.get("receipt_sel_total"), data.get("receipt_sel_pids") or "", bool(data.get("receipt_sel_partial"))
    if sel_total is not None:
        total = sel_total
    elif student:
        total, _ = await unpaid_for(student, period_month, payment_service)
    else:
        total = 0
    bills_map = await payment_service.compute_bills_for_student_period(student_id, period_month)
    sel_tids = data.get("receipt_sel_tids") or list(bills_map)
    caption = receipt_caption(method, student_name, period_month, total, "\n".join(breakdown_lines(bills_map, sel_tids)))
    rows = admin_confirm_rows(student_id, period_month, sel_pids, sel_partial, max_addr(max_uid))
    kind, url, filename = att
    try:
        blob = await event.bot.download_bytes(url)
    except Exception as exc:
        logger.error("MAX: не удалось скачать чек: %s", exc)
        await event.message.answer("Не удалось получить файл. Попробуйте ещё раз.")
        return
    if kind == "image":
        await _notify_admins_tg(tg_bot, user_repo, caption, rows, photo=blob)
    else:
        await _notify_admins_tg(tg_bot, user_repo, caption, rows, document=(blob, filename))
    text, back_rows = receipt_sent_screen(student_id, period_month)
    await send_screen(event.bot, max_uid, text, back_rows)


# ─── Чек без шага «Прикрепить чек» (MAX) ─────────────────────────────────────

async def _unpaid_bills_of_parent(students: list, payment_service) -> list:
    from bot.utils.dates import last_periods
    out = []
    for student in students:
        for period in last_periods(2):
            total, _ = await unpaid_for(student, period, payment_service)
            if total > 0:
                out.append((student, period, total))
    return out


async def _forward_unbound(event_bot, tg_bot, user_repo, payment_service, student, period_month, total,
                           kind, url, filename, max_uid) -> bool:
    bills_map = await payment_service.compute_bills_for_student_period(student.student_id, period_month)
    caption = receipt_caption("bank", student.name, period_month, total,
                              "\n".join(breakdown_lines(bills_map, list(bills_map))))
    rows = admin_confirm_rows(student.student_id, period_month, "", False, max_addr(max_uid))
    try:
        blob = await event_bot.download_bytes(url)
    except Exception as exc:
        logger.error("MAX: не удалось скачать чек: %s", exc)
        return False
    if kind == "image":
        await _notify_admins_tg(tg_bot, user_repo, caption, rows, photo=blob)
    else:
        await _notify_admins_tg(tg_bot, user_repo, caption, rows, document=(blob, filename))
    logger.info("MAX: чек без шага «Прикрепить»: max_id=%s → %s %s", max_uid, student.student_id, period_month)
    return True


@router.message_created(F.message.body.attachments, None)
async def on_unbound_receipt(event: MessageCreated, context, max_uid, student_repo, payment_service, user_repo, tg_bot):
    att = _receipt_attachment(event.message)
    if att is None:
        return
    students = await student_repo.get_by_parent_max_id(max_uid)
    if not students:
        return
    kind, url, filename = att
    bills = await _unpaid_bills_of_parent(students, payment_service)
    if not bills:
        await send_screen(event.bot, max_uid,
                          "📎 Файл получил, но неоплаченных счетов сейчас нет. "
                          "Если это чек за другой период — напишите администратору.",
                          [[cb("« Меню", "go:home")]])
        return
    if len(bills) == 1:
        student, period_month, total = bills[0]
        ok = await _forward_unbound(event.bot, tg_bot, user_repo, payment_service, student, period_month, total,
                                    kind, url, filename, max_uid)
        text, rows = receipt_sent_screen(student.student_id, period_month) if ok else ("Не удалось получить файл. Попробуйте ещё раз.", [])
        await send_screen(event.bot, max_uid, text, rows)
        return
    await context.set_state(MaxParentStates.choosing_bill)
    await context.update_data(rc_kind=kind, rc_url=url, rc_filename=filename)
    rows = [[cb(f"{s.name} · {period_label(p)} — {t} руб.", f"rcpick:{s.student_id}:{p}")] for s, p, t in bills]
    rows.append([cb("« Отмена", "go:home")])
    await send_screen(event.bot, max_uid, "📎 Чек получил. За какой счёт эта оплата?", rows)


@router.message_callback(F.callback.payload.startswith("rcpick:"), MaxParentStates.choosing_bill)
async def on_receipt_pick(event: MessageCallback, context, max_uid, student_repo, payment_service, user_repo, tg_bot):
    _, student_id, period_month = event.callback.payload.split(":", 2)
    data = await context.get_data()
    await context.clear()
    students = await student_repo.get_by_parent_max_id(max_uid)
    student = next((s for s in students if s.student_id == student_id), None)
    if student is None or not data.get("rc_url"):
        await alert(event, "Не удалось привязать чек, отправьте его ещё раз")
        return
    total, _ = await unpaid_for(student, period_month, payment_service)
    ok = await _forward_unbound(event.bot, tg_bot, user_repo, payment_service, student, period_month, total,
                                data.get("rc_kind"), data["rc_url"], data.get("rc_filename") or "receipt", max_uid)
    if ok:
        await edit_screen(event, *receipt_sent_screen(student_id, period_month))
    else:
        await alert(event, "Не удалось получить файл. Отправьте чек ещё раз.")
