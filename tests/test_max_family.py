"""MAX: «👥 Кто привязан» — тот же список и та же заявка администратору, что в кабинете Telegram."""
import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("maxapi")

from bot.max.handlers import family as mx  # noqa: E402
from bot.max.states import MaxParentStates  # noqa: E402
from bot.screens.parent_family import family_screen  # noqa: E402
from bot.services import parent_unlink  # noqa: E402
from tests.fakes import FakeBot  # noqa: E402
from tests.test_admin_api import ADMIN_TG, PARENT_TG, make_api  # noqa: E402
from tests.test_admin_inbox_api import PendingRepoFake  # noqa: E402

ME = 900003                     # родитель в MAX


class _Ctx:
    def __init__(self) -> None:
        self.state, self.data = None, {}

    async def set_state(self, s):
        self.state = s

    async def update_data(self, **kw):
        self.data.update(kw)

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.state, self.data = None, {}


class _NamedBot(FakeBot):
    async def get_chat(self, chat_id):
        if chat_id != PARENT_TG:
            raise RuntimeError("chat not found")
        return SimpleNamespace(first_name="Мария", last_name="Иванова", username="maria")


def _event(payload: str):
    screens = []

    async def edit(text=None, attachments=None):
        screens.append(text)

    async def ack(notification=None):
        screens.append(f"alert:{notification}")
    user = SimpleNamespace(user_id=ME, first_name="Олег", last_name="Петров", username="oleg")
    return SimpleNamespace(callback=SimpleNamespace(payload=payload, user=user), message=object(),
                           edit=edit, ack=ack, bot=None), screens


@pytest.fixture()
def world(monkeypatch):
    dp, _ = make_api(monkeypatch)
    parent_unlink._names.clear()
    stu = dp["student_repo"].items[0]
    stu.parent_tg_ids, stu.parent_max_ids = [PARENT_TG], [ME]
    dp["pending_repo"] = PendingRepoFake()
    return dp


def _deps(dp, bot):
    return dict(student_repo=dp["student_repo"], tg_bot=bot, client_repo=dp["client_repo"],
                pending_repo=dp["pending_repo"], activity_repo=None)


def test_family_screen_lists_parents_with_unlink_buttons(world):
    bot = _NamedBot()
    ev, screens = _event("client:family")
    asyncio.run(mx.on_family(ev, _Ctx(), ME, **_deps(world, bot)))
    text = screens[-1]
    assert "Кто привязан к ученику Иванов Иван" in text and "• <b>Вы</b> · MAX" in text
    assert "• Мария Иванова · @maria · Telegram" in text and str(PARENT_TG) not in text
    view = asyncio.run(mx._view(world["student_repo"].items[0], ME, bot, world["client_repo"], world["pending_repo"], None))
    _, rows = family_screen(view, many=False)
    assert [b.value for row in rows for b in row][0].startswith("famunl:STU-0001:")
    assert [b.label for row in rows for b in row] == ["🚫 Отвязать: Мария Иванова", "« Меню"]


def test_unlink_request_with_reason_goes_to_admin(world):
    """«Отвязать» → «Указать причину» → текст: заявка в очереди и админам в Telegram; просит родитель MAX,
    имя — из события MAX. Повтор — «уже у администратора»; кнопки «Отвязать» у такой строки больше нет."""
    bot = _NamedBot()
    key = parent_unlink.parent_key("STU-0001", ("tg", PARENT_TG), mx.settings.bot_token)
    ctx = _Ctx()
    ev, screens = _event(f"famunl_why:STU-0001:{key}")
    asyncio.run(mx.on_unlink_reason_ask(ev, ctx, ME, world["student_repo"]))
    assert ctx.state == MaxParentStates.unlink_reason and "Напишите причину" in screens[-1]

    sent = []

    async def send_message(user_id=None, text=None, attachments=None):
        sent.append((user_id, text))
    msg_ev = SimpleNamespace(message=SimpleNamespace(body=SimpleNamespace(text="не знаю её"),
                                                     sender=SimpleNamespace(first_name="Олег", last_name=None, username=None)),
                             bot=SimpleNamespace(send_message=send_message))
    asyncio.run(mx.on_unlink_reason(msg_ev, ctx, ME, world["student_repo"], world["user_repo"], **{
        k: v for k, v in _deps(world, bot).items() if k != "student_repo"}))
    assert ctx.state is None and "Заявка отправлена администратору" in sent[-1][1]
    a = world["pending_repo"].items[0]
    assert (a.kind, a.parent_addr, a.teacher_keys) == ("unlink", f"m{ME}", str(PARENT_TG))
    assert "просит: Олег (MAX)" in a.comment and "Мария Иванова (@maria, Telegram)" in a.comment and "не знаю её" in a.comment
    chat, text, kb = bot.sent[-1]
    assert chat == ADMIN_TG and [b.callback_data for b in kb.inline_keyboard[0]] == [
        f"unlink_ok:{a.action_id}", f"unlink_no:{a.action_id}"]

    ev, screens = _event(f"famunl_do:STU-0001:{key}")
    asyncio.run(mx.on_unlink_send(ev, _Ctx(), ME, world["student_repo"], world["user_repo"], **{
        k: v for k, v in _deps(world, bot).items() if k != "student_repo"}))
    assert "уже у администратора" in screens[-1] and len(world["pending_repo"].items) == 1
    view = asyncio.run(mx._view(world["student_repo"].items[0], ME, bot, None, world["pending_repo"], None))
    text, rows = family_screen(view, many=False)
    assert "⏳ заявка на отвязку у администратора" in text and not any(
        b.value.startswith("famunl:") for row in rows for b in row)


def test_unlink_self_is_refused(world):
    key = parent_unlink.parent_key("STU-0001", ("max", ME), mx.settings.bot_token)
    ev, screens = _event(f"famunl:STU-0001:{key}")
    asyncio.run(mx.on_unlink_other(ev, ME, **_deps(world, _NamedBot())))
    assert screens == ["alert:Себя — кнопкой «↩️ Это не мой ребёнок» в меню"] and world["pending_repo"].items == []
    ev, screens = _event("famunl:STU-0001:nope")
    asyncio.run(mx.on_unlink_other(ev, ME, **_deps(world, _NamedBot())))
    assert screens == ["alert:Этот родитель уже отвязан"]
