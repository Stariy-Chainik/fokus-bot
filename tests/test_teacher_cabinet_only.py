"""TEACHER_CABINET_ONLY: педагог в Telegram — только кабинет; администратор и подтверждение оплат не затронуты."""
import asyncio
from types import SimpleNamespace

from aiogram.types import CallbackQuery

from bot.keyboards.teacher import kb_teacher_menu
from bot.middlewares import teacher_cabinet as tc
from config.settings import settings


def _run(data, user, monkeypatch):
    calls, handled = [], []

    async def redirect(event):
        calls.append(event.data)

    async def handler(ev, d):
        handled.append(ev)

    monkeypatch.setattr(tc, "_redirect", redirect)
    event = CallbackQuery.model_construct(id="1", data=data, message=None)
    asyncio.run(tc.TeacherCabinetOnlyMiddleware()(handler, event, {"user": user}))
    return calls, handled


def test_old_teacher_buttons_are_redirected_but_payment_decisions_pass(monkeypatch):
    teacher = SimpleNamespace(is_admin=False, teacher_id="TCH-0009")
    for data in ("teacher:record_lesson", "teacher:my_groups", "teacher:bills", "t_student_card:STU-1", "ttask:new:STU-1"):
        calls, handled = _run(data, teacher, monkeypatch)
        assert calls and not handled, data
    for data in ("pact:ACT-1:100:c", "pnay:ACT-1", "go:home", "mode:admin"):
        calls, handled = _run(data, teacher, monkeypatch)
        assert handled and not calls, data
    admin = SimpleNamespace(is_admin=True, teacher_id="TCH-0001")        # администратор в режиме педагога
    calls, handled = _run("teacher:record_lesson", admin, monkeypatch)
    assert handled and not calls


def test_menu_is_a_single_cabinet_button_for_teacher_only(monkeypatch):
    monkeypatch.setattr(settings, "teacher_cabinet_only", True)
    monkeypatch.setattr(settings, "miniapp_url", "https://x/app/")
    rows = kb_teacher_menu(teacher_id="TCH-0009").inline_keyboard
    assert len(rows) == 1 and rows[0][0].text == "📱 Войти в кабинет" and rows[0][0].web_app.url == "https://x/app/"
    full = kb_teacher_menu(can_switch_role=True, teacher_id="TCH-0001").inline_keyboard      # админ: меню прежнее
    assert len(full) > 1
    monkeypatch.setattr(settings, "teacher_cabinet_only", False)
    assert len(kb_teacher_menu(teacher_id="TCH-0009").inline_keyboard) > 1
