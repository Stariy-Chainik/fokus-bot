"""ADMIN_CABINET_ONLY / ATHLETE_CABINET_ONLY (решение владельца 21.10.2026): Telegram-бот только как мини-приложение —
меню администратора и спортсмена заменены кнопкой кабинета, прежние кнопки отвечают подсказкой, кнопки решений в
уведомлениях работают."""
import asyncio
from types import SimpleNamespace

from aiogram.types import CallbackQuery

from bot.keyboards.admin import kb_admin_menu
from bot.keyboards.athlete import athlete_welcome_text, kb_athlete_menu
from bot.keyboards.teacher import kb_teacher_menu
from bot.middlewares import cabinet_only as co
from config.settings import settings


def _run(mw, data, user, monkeypatch):
    calls, handled = [], []

    async def redirect(event, *_a, **_k):
        calls.append(event.data)

    async def handler(ev, d):
        handled.append(ev)

    monkeypatch.setattr(co, "_redirect", redirect)
    event = CallbackQuery.model_construct(id="1", data=data, message=None)
    asyncio.run(mw(handler, event, {"user": user}))
    return calls, handled


def test_admin_menu_buttons_redirected_but_notification_decisions_pass(monkeypatch):
    admin = SimpleNamespace(is_admin=True, teacher_id="TCH-0001")
    mw = co.AdminCabinetOnlyMiddleware()
    for data in ("teachers:list", "admin:students", "bills:view", "admin:debtors", "profit:view", "student_card:STU-1",
                 "teacher:record_lesson", "students:list", "admin:diary"):
        calls, handled = _run(mw, data, admin, monkeypatch)
        assert calls and not handled, data
    for data in ("pact:ACT-1:100:c", "pnay:ACT-1", "admin_child_ok:1:STU-1", "nchild_no:ACT-2", "unlink_ok:ACT-3",
                 "receipt_confirm:STU-1:2026-10", "rcpt_no:STU-1:2026-10:m5", "athreg:unlink:STU-1:7", "remind:x",
                 "go:home", "mode:admin", "noop"):
        calls, handled = _run(mw, data, admin, monkeypatch)
        assert handled and not calls, data
    teacher = SimpleNamespace(is_admin=False, teacher_id="TCH-0009")      # не администратор — не трогаем
    calls, handled = _run(mw, "teachers:list", teacher, monkeypatch)
    assert handled and not calls


def test_athlete_menu_buttons_redirected(monkeypatch):
    mw = co.AthleteCabinetOnlyMiddleware()
    for data in ("ath:log", "ath:menu", "athent:list:2026-10", "athlog:tp:1", "athcal_pick:2026-10-01"):
        calls, handled = _run(mw, data, None, monkeypatch)
        assert calls and not handled, data
    for data in ("athreg:athlete", "athreg:pick:STU-1", "mode:client", "client:lessons", "glink:x"):
        calls, handled = _run(mw, data, None, monkeypatch)
        assert handled and not calls, data


def test_menus_are_single_cabinet_buttons(monkeypatch):
    monkeypatch.setattr(settings, "miniapp_url", "https://x/app/")
    monkeypatch.setattr(settings, "admin_cabinet_only", True)
    monkeypatch.setattr(settings, "athlete_cabinet_only", True)
    monkeypatch.setattr(settings, "teacher_cabinet_only", False)
    rows = kb_admin_menu(can_switch_role=True).inline_keyboard
    assert len(rows) == 1 and rows[0][0].text == "📱 Открыть кабинет" and rows[0][0].web_app.url == "https://x/app/"
    # администратор в режиме педагога — тоже кнопка кабинета, чистый педагог без своего флага — меню
    assert len(kb_teacher_menu(can_switch_role=True, teacher_id="TCH-0001").inline_keyboard) == 1
    assert len(kb_teacher_menu(teacher_id="TCH-0009").inline_keyboard) > 1
    ath = kb_athlete_menu(can_switch_parent=True).inline_keyboard
    assert [b.text for r in ath for b in r] == ["📱 Открыть дневник", "👨‍👩‍👧 Кабинет родителя"]
    assert "в приложении" in athlete_welcome_text(SimpleNamespace(name="Иванов Иван"))
    monkeypatch.setattr(settings, "admin_cabinet_only", False)
    monkeypatch.setattr(settings, "athlete_cabinet_only", False)
    assert len(kb_admin_menu().inline_keyboard) > 1 and len(kb_athlete_menu().inline_keyboard) > 1
