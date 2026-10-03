"""Педагогам: заявки родителей об оплате с кнопками решения и уведомления об оплатах учеников их групп (FULL_BILL_TEACHER_IDS — все группы
педагога, FULL_BILL_GROUPS — только перечисленные).

Одна точка для всех путей зачёта: ЮКасса, очередь решений, кабинеты админа и педагога,
кнопки в боте — все они идут через PaymentService, а он зовёт `payment_received()`.
Отправка — фоновой задачей: оплата не ждёт Telegram. Без `setup()` (тесты, скрипты) — no-op.
"""
from __future__ import annotations

import asyncio
import logging

from bot.services import payment_methods
from config.settings import settings

logger = logging.getLogger(__name__)

_deps: dict = {}
_tasks: set = set()


def setup(tg_bot, user_repo, teacher_group_repo, student_group_repo, student_repo, teacher_repo=None) -> None:
    _deps.update(bot=tg_bot, users=user_repo, tg=teacher_group_repo, sg=student_group_repo, students=student_repo,
                 teachers=teacher_repo)


def payment_received(student_id: str, period: str, amount: int, method: str, actor: int,
                     student_name: str = "") -> None:
    """Запланировать уведомления; вызывается сразу после зачёта оплаты.

    Педагогам с полным счётом — об оплатах их учеников; администраторам — если оплату
    подтвердил педагог (деньги, особенно наличные, у него — админ должен это видеть).
    """
    if not _deps.get("bot") or amount <= 0:
        return
    task = asyncio.get_running_loop().create_task(
        _send(student_id, period, amount, method, int(actor or 0), student_name))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def recipients(student_id: str, actor: int) -> list[int]:
    """tg_id педагогов, которым интересна оплата ученика; кто отметил сам — не уведомляем.

    С полным счётом (FULL_BILL_TEACHER_IDS) и из PAYMENT_NOTIFY_TEACHER_IDS — если ученик в любой группе
    педагога; по списку групп (FULL_BILL_GROUPS) — если в одной из них.
    """
    groups = set(await _deps["sg"].get_groups_for_student(student_id))
    full = settings.full_bill_teacher_id_set | settings.payment_notify_teacher_id_set
    by_group = settings.full_bill_group_map
    out: list[int] = []
    for u in await _deps["users"].get_all():
        tid = u.teacher_id
        if not tid or u.tg_id == actor or u.tg_id in out:
            continue
        if groups & by_group.get(tid, set()) or (
                tid in full and groups & set(await _deps["tg"].get_groups_for_teacher(tid))):
            out.append(u.tg_id)
    return out


async def _send(student_id: str, period: str, amount: int, method: str, actor: int, name: str) -> None:
    from bot.services.payment_methods import CASH
    from bot.utils.dates import period_label
    try:
        users = await _deps["users"].get_all()
        by_tg = {u.tg_id: u for u in users}
        who = by_tg.get(actor)
        by_teacher = who is not None and bool(who.teacher_id) and not who.is_admin
        to = await recipients(student_id, actor)
        admins = [u.tg_id for u in users if u.is_admin and u.tg_id != actor] if by_teacher else []
        if not to and not admins:
            return
        if not name:
            s = await _deps["students"].get_by_id(student_id)
            name = s.name if s else student_id
        head = f"💳 Оплата {amount} ₽ — {name}, {period_label(period).lower()}"
        if by_teacher and who is not None:
            tname = await teacher_name(who.teacher_id)
            how = ("💵 Наличные — деньги у педагога" if method == CASH
                   else payment_methods.label(method, confirmed_by_tg_id=actor).split(" — ")[0])
            text = f"{head}\n{how}\nПодтвердил педагог: {tname}"
        else:
            text = f"{head}\n{payment_methods.label(method, confirmed_by_tg_id=actor)}"
        for tg_id in dict.fromkeys(to + admins):
            try:
                await _deps["bot"].send_message(tg_id, text)
            except Exception as exc:
                logger.warning("%s не ушло уведомление об оплате: %s", tg_id, exc)
    except Exception as exc:                        # уведомление вспомогательное
        logger.warning("Уведомление об оплате %s: %s", student_id, exc)


# ── заявки родителей об оплате (наличные, чеки) — педагогам, которые решают их сами ──

def payment_cancelled(row, actor: int, reason: str = "") -> None:
    """Педагогу, чью отметку оплаты снял администратор, — сообщение; свою отметку снял сам — молчим."""
    if not _deps.get("bot") or not row.confirmed_by_tg_id or row.confirmed_by_tg_id == actor:
        return
    _spawn(_send_cancelled(row, reason))


async def _send_cancelled(row, reason: str) -> None:
    from bot.utils.dates import period_label
    user = await _deps["users"].get_by_tg_id(row.confirmed_by_tg_id)
    if user is None or not user.teacher_id or user.is_admin:
        return
    text = (f"↩️ Администратор снял вашу отметку оплаты\nУченик: {row.student_name}\n"
            f"Период: {period_label(row.period_month)}\nСумма: {row.total_amount} руб."
            + (f"\nПричина: {reason}" if reason else "") + "\n\nНачисление снова числится неоплаченным.")
    try:
        await _deps["bot"].send_message(row.confirmed_by_tg_id, text)
    except Exception as exc:
        logger.warning("payment_events: педагогу %s не доставлено: %s", row.confirmed_by_tg_id, exc)


def request_created(action) -> None:
    """Новая заявка в очереди решений.

    Наличные у педагога (held_by) — администраторам: «зачтите, когда получите деньги».
    Заявка родителя — педагогу из FULL_BILL_TEACHER_IDS копия с кнопками: чек он подтверждает
    сам (`pact:`), наличные — только «✋ Деньги у меня» (тот же `pact:`, хендлер не зачитывает
    оплату педагогу, а передаёт заявку администратору). Решение одно на всех: `claim()`.
    """
    from bot.repositories.pending_action_repo import KIND_CASH, KIND_RECEIPT
    if not _deps.get("bot") or action is None or action.kind not in (KIND_CASH, KIND_RECEIPT):
        return
    if action.held_by:
        _spawn(_send_held(action))
    elif settings.full_bill_teacher_id_set:
        _spawn(_send_request(action))


def cash_held(action, teacher_id: str) -> None:
    """Педагог подтвердил, что наличные родителя у него — администраторам заявка на зачёт."""
    if _deps.get("bot") and action is not None:
        action.held_by = teacher_id
        _spawn(_send_held(action))


def _spawn(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def held_rows(action) -> list:
    """Кнопки администратору по наличным у педагога: зачесть, когда деньги переданы."""
    from bot.screens import cb
    return [[cb(f"✅ Деньги получены — зачесть {action.amount} ₽", f"pact:{action.action_id}:{action.amount}:c")],
            [cb("❌ Отклонить", f"pnay:{action.action_id}")]]


def teacher_rows(action) -> list:
    """Кнопки педагогу по заявке родителя: чек — подтвердить, наличные — «деньги у меня»."""
    from bot.repositories.pending_action_repo import KIND_CASH
    from bot.screens import cb
    from bot.services.payment_methods import callback_code
    if action.kind == KIND_CASH:
        return [[cb("✋ Деньги у меня — передам администратору", f"pact:{action.action_id}:{action.amount}:c")],
                [cb("❌ Денег не получала", f"pnay:{action.action_id}")]]
    return [[cb("✅ Подтвердить оплату", f"pact:{action.action_id}:{action.amount}:{callback_code(action.method or '')}")],
            [cb("❌ Не подтверждать", f"pnay:{action.action_id}")]]


async def teacher_name(teacher_id: str) -> str:
    t = await _deps["teachers"].get_by_id(teacher_id) if _deps.get("teachers") else None
    return t.name if t else teacher_id


async def _send_held(action) -> None:
    from bot.screens.adapters import to_aiogram_markup
    from bot.utils.dates import period_label
    try:
        admins = [u.tg_id for u in await _deps["users"].get_all() if u.is_admin]
        text = (f"💵 Наличные у педагога: {action.amount} ₽ — {action.student_name}, "
                f"{period_label(action.period_month).lower()}\n"
                f"Деньги у: {await teacher_name(action.held_by)}\n"
                f"Зачтите оплату, когда педагог передаст деньги. Заявка — в «Ждут решения».")
        kb = to_aiogram_markup(held_rows(action))
        for tg_id in admins:
            try:
                await _deps["bot"].send_message(tg_id, text, reply_markup=kb)
            except Exception as exc:
                logger.warning("Админу %s не ушла заявка %s: %s", tg_id, action.action_id, exc)
    except Exception as exc:
        logger.warning("Наличные у педагога %s: %s", getattr(action, "action_id", "?"), exc)


async def deciders(student_id: str) -> list[int]:
    """tg_id педагогов с полным счётом, в чьих группах учится ученик."""
    groups = set(await _deps["sg"].get_groups_for_student(student_id))
    out: list[int] = []
    for u in await _deps["users"].get_all():
        tid = u.teacher_id
        if (tid and tid in settings.full_bill_teacher_id_set and u.tg_id not in out
                and groups & set(await _deps["tg"].get_groups_for_teacher(tid))):
            out.append(u.tg_id)
    return out


async def may_decide(user, student_id: str) -> bool:
    """Может ли пользователь решить заявку ученика: админ — любую, педагог — учеников своих групп."""
    if user is None:
        return False
    if user.is_admin:
        return True
    if not _deps or user.teacher_id not in settings.full_bill_teacher_id_set:
        return False
    groups = set(await _deps["sg"].get_groups_for_student(student_id))
    return bool(groups & set(await _deps["tg"].get_groups_for_teacher(user.teacher_id)))


async def _send_request(action) -> None:
    from bot.repositories.pending_action_repo import KIND_CASH
    from bot.screens.adapters import to_aiogram_markup
    from bot.utils.dates import period_label
    try:
        to = await deciders(action.student_id)
        if not to:
            return
        title = "💵 Оплата наличными" if action.kind == KIND_CASH else "🧾 Чек об оплате"
        text = (f"{title}: {action.amount} ₽ — {action.student_name}, {period_label(action.period_month).lower()}\n"
                + ("Если деньги у вас — нажмите «Деньги у меня», оплату зачтёт администратор, когда вы их передадите."
                   if action.kind == KIND_CASH else "Родитель ждёт подтверждения. Решение видно и в кабинете → «Ждут решения»."))
        kb = to_aiogram_markup(teacher_rows(action))
        bot = _deps["bot"]
        for tg_id in to:
            try:
                if action.file_id and action.file_type == "photo":
                    await bot.send_photo(tg_id, action.file_id, caption=text, reply_markup=kb)
                elif action.file_id:
                    await bot.send_document(tg_id, action.file_id, caption=text, reply_markup=kb)
                else:
                    await bot.send_message(tg_id, text, reply_markup=kb)
            except Exception as exc:
                logger.warning("Педагогу %s не ушла заявка %s: %s", tg_id, action.action_id, exc)
    except Exception as exc:
        logger.warning("Заявка %s педагогам: %s", getattr(action, "action_id", "?"), exc)
