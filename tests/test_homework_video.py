"""Видео к домашнему заданию (решение владельца 02.11.2026): спортсмен шлёт ролик в чат бота, файл хранит Telegram,
педагог получает его сразу; из кабинета бот заранее говорит, какое задание ждёт видео."""
import asyncio
from types import SimpleNamespace

import pytest
from aiogram.types import Message

from bot.handlers.athlete import video as hv
from bot.middlewares import parent_cabinet as pc
from bot.services.homework_video import send_ref, split_ref, video_ref
from bot.states import HomeworkVideoStates
from config.settings import settings
from tests.fakes import FakeBot, FakeCallbackQuery, FakeMessage, FakeState
from tests.test_athlete_api import ATHLETE_TG, api  # noqa: F401 — спортсмен STU-0001 с одним заданием

TEACHER_CHAT = 4242


@pytest.fixture()
def dp(api, monkeypatch):  # noqa: F811
    _, dp = api
    dp["teacher_repo"].items[0].tg_id = TEACHER_CHAT          # педагогу есть куда слать
    monkeypatch.setattr(settings, "miniapp_url", "https://fokus.test/app/")
    return dp


def _video(user_id=ATHLETE_TG, file_id="V1", **media):
    return FakeMessage(user_id=user_id, video=SimpleNamespace(file_id=file_id) if not media else None, **media)


def _send(message, dp, state=None):
    state = state or FakeState()
    asyncio.run(hv.on_homework_video(message, state, dp["diary_service"], dp["teacher_repo"]))
    return state


def test_refs_cover_video_note_and_file():
    assert video_ref(SimpleNamespace(video=SimpleNamespace(file_id="A"), video_note=None, document=None)) == "video:A"
    assert video_ref(SimpleNamespace(video=None, video_note=SimpleNamespace(file_id="B"), document=None)) == "note:B"
    doc = SimpleNamespace(file_id="C", mime_type="video/quicktime")
    assert video_ref(SimpleNamespace(video=None, video_note=None, document=doc)) == "doc:C"
    pdf = SimpleNamespace(file_id="D", mime_type="application/pdf")
    assert video_ref(SimpleNamespace(video=None, video_note=None, document=pdf)) is None
    assert split_ref("note:B") == ("note", "B") and split_ref("LEGACYID") == ("video", "LEGACYID")
    bot = FakeBot()
    asyncio.run(send_ref(bot, 7, "note:B", caption="подпись"))
    asyncio.run(send_ref(bot, 7, "doc:C", caption="файл"))
    assert [x[3] for x in bot.sent if len(x) > 3] == [("note", "B"), ("doc", "C")]
    assert bot.sent[1][1] == "подпись"                      # у кружка подпись — отдельным сообщением


def test_single_open_task_gets_the_video_and_teacher_is_notified(dp):
    msg = _video()
    _send(msg, dp)
    task = dp["task_repo"].items[0]
    assert task.videos == ["video:V1"]
    chat, caption, markup, media = msg.bot.sent[0]
    assert chat == TEACHER_CHAT and media == ("video", "V1")
    assert "Иванов Иван" in caption and "Ритм в самбе" in caption and markup is None    # задание без выдачи — без кнопки
    text, kb = msg.screens[-1]
    assert text.startswith("✅ Видео отправлено педагогу Река Станислав") and "ещё один" not in text


def test_cabinet_prompt_state_keeps_sending_to_the_same_task(dp):
    tasks = asyncio.run(dp["task_repo"].add_batch(["STU-0001"], "TCH-0001", "Шпагат", 0, group_id="GRP-0001"))
    state = FakeState({"hwv_task": tasks[0].task_id}, HomeworkVideoStates.waiting_video.state)
    msg = _video(file_id="V2")
    _send(msg, dp, state)
    assert tasks[0].videos == ["video:V2"] and dp["task_repo"].items[0].videos == []
    chat, caption, markup, _ = msg.bot.sent[0]
    assert "Задание №1" in caption and markup.inline_keyboard[0][0].web_app.url.endswith("?open=hw&bid=HW-000001")
    text, kb = msg.screens[-1]
    assert "ещё один ролик" in text and kb.inline_keyboard[0][0].callback_data == "hwv:cancel"
    assert asyncio.run(state.get_state()) == HomeworkVideoStates.waiting_video.state   # следующий ролик — туда же
    # второй ролик файлом — тоже к этому заданию, педагогу уходит документом с «Видео 2 из 2»
    msg2 = FakeMessage(user_id=ATHLETE_TG, document=SimpleNamespace(file_id="F3", mime_type="video/mp4"))
    _send(msg2, dp, state)
    assert tasks[0].videos == ["video:V2", "doc:F3"] and msg2.bot.sent[0][3] == ("doc", "F3")
    assert "Видео 2 из 2" in msg2.bot.sent[0][1]


def test_closed_task_in_state_falls_back_to_open_ones(dp):
    closed = asyncio.run(dp["task_repo"].add("STU-0001", "TCH-0001", "старое", 10))
    asyncio.run(dp["task_repo"].close(closed.task_id))
    state = FakeState({"hwv_task": closed.task_id}, HomeworkVideoStates.waiting_video.state)
    msg = _video(file_id="V4")
    _send(msg, dp, state)
    assert dp["task_repo"].items[0].videos == ["video:V4"] and closed.videos == []
    assert asyncio.run(state.get_state()) is None


def test_several_open_tasks_ask_which_one_then_pick_attaches(dp):
    asyncio.run(dp["task_repo"].add_batch(["STU-0001"], "TCH-0001", "Шпагат", 0, group_id="GRP-0001"))
    msg = _video(file_id="V5")
    state = _send(msg, dp)
    text, kb = msg.screens[-1]
    assert "К какому заданию" in text and not msg.bot.sent
    buttons = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert buttons == ["hwv:pick:TK-000001", "hwv:pick:TK-000002", "hwv:cancel"]
    assert kb.inline_keyboard[1][0].text == "Задание №1 от 15.10 «Шпагат»"
    assert asyncio.run(state.get_data())["hwv_file"] == "video:V5"
    cb = FakeCallbackQuery("hwv:pick:TK-000002", user_id=ATHLETE_TG)
    asyncio.run(hv.cb_homework_video_pick(cb, state, dp["diary_service"], dp["teacher_repo"]))
    assert dp["task_repo"].items[1].videos == ["video:V5"] and cb.bot.sent[0][3] == ("video", "V5")
    assert asyncio.run(state.get_state()) is None and cb.message.screens[-1][0].startswith("✅ Видео отправлено")
    # кнопка без ролика в состоянии — просим прислать заново
    cb2 = FakeCallbackQuery("hwv:pick:TK-000001", user_id=ATHLETE_TG)
    asyncio.run(hv.cb_homework_video_pick(cb2, FakeState(), dp["diary_service"], dp["teacher_repo"]))
    assert cb2.alerts == [("Пришлите видео ещё раз", True)]


def test_non_athlete_and_no_open_tasks(dp):
    stranger = _video(user_id=555)
    _send(stranger, dp)
    assert not stranger.screens and not stranger.bot.sent          # не спортсмен — молчим
    for t in dp["task_repo"].items:
        asyncio.run(dp["task_repo"].close(t.task_id))
    msg = _video()
    _send(msg, dp)
    assert "Открытых заданий нет" in msg.screens[-1][0] and not msg.bot.sent


def test_cancel_clears_state():
    cb = FakeCallbackQuery("hwv:cancel", user_id=ATHLETE_TG)
    state = FakeState({"hwv_task": "TK-1"}, HomeworkVideoStates.waiting_video.state)
    asyncio.run(hv.cb_homework_video_cancel(cb, state))
    assert asyncio.run(state.get_state()) is None and cb.message.screens[-1][0] == "Готово."


def test_parent_cabinet_middleware_lets_video_files_through(monkeypatch):
    """PARENT_CABINET_ONLY гасит фото и файлы (чек — в кабинете), но ролик файлом — это видео к заданию."""
    calls, handled = [], []

    async def redirect(*_a, **_k):
        calls.append(1)

    async def handler(ev, data):
        handled.append(ev)

    monkeypatch.setattr(pc, "_redirect", redirect)
    mw = pc.ParentCabinetOnlyMiddleware()
    video_doc = Message.model_construct(message_id=1, photo=None, text=None,
                                        document=SimpleNamespace(mime_type="video/mp4"))
    asyncio.run(mw(handler, video_doc, {"user": None}))
    pdf = Message.model_construct(message_id=2, photo=None, text=None, document=SimpleNamespace(mime_type="application/pdf"))
    asyncio.run(mw(handler, pdf, {"user": None}))
    assert len(handled) == 1 and len(calls) == 1
