"""PARENT_CABINET_ONLY: родитель в Telegram — только кабинет; админы и педагоги не затронуты."""
import asyncio
from types import SimpleNamespace

from aiogram.types import CallbackQuery, Message

from bot.middlewares import parent_cabinet as pc
from bot.screens.parent_menu import menu_rows


def _run(event, user, monkeypatch):
    calls, handled = [], []

    async def redirect(message, answer, text, alert=False):
        calls.append(text)

    async def handler(ev, data):
        handled.append(ev)
        return "ok"

    monkeypatch.setattr(pc, "_redirect", redirect)
    asyncio.run(pc.ParentCabinetOnlyMiddleware()(handler, event, {"user": user}))
    return calls, handled


def _cb(data):
    return CallbackQuery.model_construct(id="1", data=data, message=None)


def test_parent_old_buttons_and_files_are_redirected(monkeypatch):
    for data in ("client:my_bills", "client_bill:STU-1:2026-09", "client_pay:STU-1:2026-09", "pay_method:bank:x:y",
                 "receipt_upload:bank:STU-1:2026-09", "client:lessons", "cldiary:stu:STU-1", "cash_notify:STU-1:2026-09"):
        calls, handled = _run(_cb(data), None, monkeypatch)
        assert calls and not handled, data
    msg = Message.model_construct(message_id=1, photo=[object()], document=None, text=None)
    calls, handled = _run(msg, None, monkeypatch)
    assert calls and not handled


def test_registration_text_and_admin_flows_are_untouched(monkeypatch):
    for data in ("glink:GRP-1:STU-1", "client:add_child", "go:home", "client_reg:STU-1"):
        assert not pc.is_blocked_callback(data), data
    # админ и педагог: те же callback'и служат подтверждению оплат
    admin = SimpleNamespace(is_admin=True, teacher_id=None)
    teacher = SimpleNamespace(is_admin=False, teacher_id="TCH-0009")
    for user in (admin, teacher):
        calls, handled = _run(_cb("client_pay:STU-1:2026-09"), user, monkeypatch)
        assert handled and not calls
    text = Message.model_construct(message_id=1, photo=None, document=None, text="Иванова")
    calls, handled = _run(text, None, monkeypatch)
    assert handled and not calls


def test_menu_is_cabinet_button_only_when_enabled():
    rows = menu_rows(cabinet_url="https://x/app/")
    assert rows[0][0].kind == "webapp" and not any("my_bills" in b.value for r in rows for b in r)
    assert any(b.value == "client:my_bills" for r in menu_rows() for b in r)       # по умолчанию меню прежнее
