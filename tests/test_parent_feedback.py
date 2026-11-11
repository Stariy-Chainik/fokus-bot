"""«💬 Написать руководителю» (решение владельца 11.11.2026): обращение родителя из кабинета уходит только
руководителю с кнопкой «Ответить», ответ одним сообщением возвращается родителю, журнал — лист parent_feedback."""
import asyncio

from bot.handlers.admin.feedback import cb_feedback_cancel, cb_feedback_reply, msg_feedback_reply
from bot.repositories.base import BaseRepository
from bot.repositories.parent_feedback_repo import HEADER, ParentFeedbackRepository
from bot.services import parent_feedback
from bot.states.admin_states import FeedbackReplyStates
from config.settings import settings
from tests.fakes import FakeBot, FakeCallbackQuery, FakeMessage, FakeSheetsClient, FakeState, FakeWorksheet, mk_user
from tests.test_admin_api import ADMIN_TG, PARENT_TG, make_api
from tests.test_parent_api import _call as parent_call

URL = "/api/parent/feedback"


def _send(dp, bot, **body):
    return parent_call(dp, "POST", URL, json={"studentId": "STU-0001", "kind": "wish", "text": "x", **body}, bot=bot)


def test_parent_sends_and_only_director_gets_it(monkeypatch):
    dp, _ = make_api(monkeypatch)
    parent_feedback._last_sent.clear()
    bot = FakeBot()
    status, r = _send(dp, bot, text="  Хочу больше стандарта  ")
    assert status == 200 and r == {"ok": True, "id": "FB-000001"}
    row = dp["feedback_repo"].items[0]
    assert (row.student_id, row.parent_addr, row.kind, row.text, row.status) == \
           ("STU-0001", str(PARENT_TG), "wish", "Хочу больше стандарта", "new")
    # OWNER_TEACHER_IDS пуст → администраторы; в тексте вид, ребёнок, группа, сам текст; кнопка «Ответить»
    assert [m[0] for m in bot.sent] == [ADMIN_TG]
    text, kb = bot.sent[0][1], bot.sent[0][2]
    assert "💡 Пожелание" in text and "Иванов Иван" in text and "БП Джаз" in text and "Хочу больше стандарта" in text
    assert [b.callback_data for row_ in kb.inline_keyboard for b in row_] == ["fbreply:FB-000001"]
    # повтор раньше чем через 10 минут — 429; потом: не тот вид и пустой текст — 400, чужой ребёнок — 404
    assert _send(dp, bot, text="ещё")[0] == 429
    parent_feedback._last_sent.clear()
    assert _send(dp, bot, kind="spam")[0] == 400 and _send(dp, bot, text="   ")[0] == 400
    assert _send(dp, bot, studentId="STU-0002")[0] == 404
    assert len(bot.sent) == 1 and len(dp["feedback_repo"].items) == 1
    lst = parent_call(dp, "GET", URL)[1]
    assert [k["id"] for k in lst["kinds"]] == ["wish", "remark", "thanks", "question"] and lst["maxLen"] == 2000
    assert lst["items"][0]["text"] == "Хочу больше стандарта" and lst["items"][0]["answered"] is False


def test_director_replies_and_parent_gets_the_answer(monkeypatch):
    dp, _ = make_api(monkeypatch)
    parent_feedback._last_sent.clear()
    _send(dp, FakeBot(), kind="question", text="Когда турнир?")
    admin, state = mk_user(ADMIN_TG, is_admin=True), FakeState()
    cb = FakeCallbackQuery("fbreply:FB-000001", user_id=ADMIN_TG)
    asyncio.run(cb_feedback_reply(cb, admin, state, feedback_repo=dp["feedback_repo"]))
    assert asyncio.run(state.get_state()) == FeedbackReplyStates.waiting_reply
    assert "Когда турнир?" in cb.message.screens[-1][0]
    msg = FakeMessage("  В декабре, напишу в чат группы ", user_id=ADMIN_TG)
    asyncio.run(msg_feedback_reply(msg, admin, state, feedback_repo=dp["feedback_repo"]))
    row = dp["feedback_repo"].items[0]
    assert row.answered and row.reply == "В декабре, напишу в чат группы" and row.replied_by == ADMIN_TG
    assert asyncio.run(state.get_state()) is None and "✅ Ответ отправлен" in msg.screens[-1][0]
    assert msg.bot.sent[0][0] == PARENT_TG and "В декабре" in msg.bot.sent[0][1] and "Когда турнир?" in msg.bot.sent[0][1]
    item = parent_call(dp, "GET", URL)[1]["items"][0]
    assert item["answered"] is True and item["reply"] == "В декабре, напишу в чат группы"
    # неизвестное обращение — alert; «Отмена» снимает состояние
    cb2 = FakeCallbackQuery("fbreply:FB-000404", user_id=ADMIN_TG)
    asyncio.run(cb_feedback_reply(cb2, admin, FakeState(), feedback_repo=dp["feedback_repo"]))
    assert cb2.alerts[-1] == ("Обращение не найдено", True)
    st = FakeState(state=FeedbackReplyStates.waiting_reply)
    asyncio.run(cb_feedback_cancel(FakeCallbackQuery("fbreply_cancel", user_id=ADMIN_TG), admin, st))
    assert asyncio.run(st.get_state()) is None


def test_recipients_setting_then_owner_then_admins(monkeypatch):
    dp, _ = make_api(monkeypatch)
    asyncio.run(dp["user_repo"].add(4242, teacher_id="TCH-0001"))
    assert asyncio.run(parent_feedback.recipients(dp["user_repo"])) == [ADMIN_TG]
    monkeypatch.setattr(settings, "owner_teacher_ids", "TCH-0001")
    assert asyncio.run(parent_feedback.recipients(dp["user_repo"])) == [4242]
    monkeypatch.setattr(settings, "feedback_tg_ids", "111, 222")
    assert asyncio.run(parent_feedback.recipients(dp["user_repo"])) == [111, 222]


def test_repository_adds_and_marks_answered_raw():
    BaseRepository._cache.clear(), BaseRepository._locks.clear(), BaseRepository._headers.clear()
    raws = []

    class Sheet(FakeWorksheet):
        def batch_update(self, data, raw=True, **kw):
            raws.append(raw)
            return super().batch_update(data, raw=raw, **kw)
    ws = Sheet(HEADER, [])
    repo = ParentFeedbackRepository(FakeSheetsClient(ws), "parent_feedback")
    row = asyncio.run(repo.add("STU-0001", "m456", "Мама", "remark", "Холодно в зале"))
    assert row.feedback_id == "FB-000001" and ws.rows[0][:8] == ["FB-000001", row.ts, "STU-0001", "m456", "Мама", "remark",
                                                                  "Холодно в зале", "new"]
    assert asyncio.run(repo.set_reply("FB-000001", "Включим обогреватель", 826576855)) and raws == [True]
    got = asyncio.run(repo.get_for_parent("m456"))[0]
    assert got.answered and got.reply == "Включим обогреватель" and got.replied_by == 826576855 and got.replied_at
    assert not asyncio.run(repo.set_reply("FB-000404", "x", 1))
    BaseRepository._cache.clear(), BaseRepository._locks.clear(), BaseRepository._headers.clear()
