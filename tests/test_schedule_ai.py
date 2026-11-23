"""Тесты гибридного AI-разбора расписаний и переключения сверки чеков на OpenAI GPT."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from aiogram.types import Chat, Message, User

from bot.services import receipt_check as rc
from bot.services.schedule_ai import looks_like_schedule, validate_ai_schedule
from bot.services.schedule_pilot import SchedulePilot, SchedulePilots, Slot
from config.settings import settings

CHAT = -1001111111111
PUBLISHED = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)  # понедельник 12:00 МСК


def _msg(text: str, *, mid: int = 50, uid: int = 222, edited: bool = False) -> Message:
    return Message(
        message_id=mid,
        date=PUBLISHED,
        chat=Chat(id=CHAT, type="supergroup"),
        from_user=User(id=uid, is_bot=False, first_name="Назар"),
        text=text,
        edit_date=int(PUBLISHED.timestamp()) + 60 if edited else None,
    )


def test_looks_like_schedule_prefilter():
    assert not looks_like_schedule("Всем привет! Как дела?")
    assert not looks_like_schedule("17:00")
    assert looks_like_schedule("Расписание Леры на понедельник\n17:00 Егор и Катя")
    assert looks_like_schedule("Ангелина Пн 16.30 Ира М.")


def test_validate_ai_schedule_splits_days_routes_teachers_and_rejects_bad_data():
    published = date(2026, 11, 23)
    allowed = {"TCH-0002", "TCH-0007", "TCH-0013"}
    raw = {
        "is_schedule": True,
        "schedules": [
            {
                "teacher_id": "TCH-0002",
                "days": [
                    {
                        "date": "2026-11-23",
                        "slots": [
                            {"time": "17:00", "end": None, "duration": None, "names": "Егор и Катя"},
                            {"time": "18:00", "end": "19:30", "duration": None, "names": "группа ДЖАЗ"},
                            {"time": "19:30", "end": None, "duration": None, "names": "свободно"},
                        ],
                    },
                    {
                        "date": "2026-11-25",
                        "slots": [
                            {"time": "16:30", "end": None, "duration": 45, "names": "Шабанова Алиса"},
                        ],
                    },
                    # Выходной / только свободные слоты — пропускается
                    {
                        "date": "2026-11-26",
                        "slots": [{"time": "17:00", "end": None, "duration": None, "names": "свободно"}],
                    },
                    # Дата вне допустимого окна — отклоняется
                    {
                        "date": "2025-01-01",
                        "slots": [{"time": "17:00", "end": None, "duration": 45, "names": "Иван"}],
                    },
                ],
            },
            {
                "teacher_id": "TCH-0013",
                "days": [
                    {
                        "date": "2026-11-23",
                        "slots": [{"time": "18:15", "end": None, "duration": 60, "names": "Коля и Соня"}],
                    },
                ],
            },
            # Неизвестный teacher_id — игнорируется
            {
                "teacher_id": "TCH-9999",
                "days": [
                    {
                        "date": "2026-11-23",
                        "slots": [{"time": "19:00", "end": None, "duration": 45, "names": "Кто-то"}],
                    },
                ],
            },
        ],
    }

    parsed = validate_ai_schedule(raw, published, allowed)
    assert set(parsed) == {"TCH-0002", "TCH-0013"}
    assert set(parsed["TCH-0002"]) == {date(2026, 11, 23), date(2026, 11, 25)}
    mon = parsed["TCH-0002"][date(2026, 11, 23)]
    assert [(s.time, s.duration, s.suggested, s.end, s.names) for s in mon] == [
        ("17:00", 60, True, None, "Егор и Катя"),
        ("18:00", 90, False, "19:30", "группа ДЖАЗ"),
        ("19:30", None, False, None, ""),
    ]
    assert parsed["TCH-0013"][date(2026, 11, 23)][0].names == "Коля и Соня"
    assert validate_ai_schedule({"is_schedule": False, "schedules": []}, published, allowed) == {}


def test_hybrid_pilots_prefers_local_and_routes_nazar_to_lera_and_angelina_weekly(tmp_path):
    db = tmp_path / "hybrid.db"
    angelina = SchedulePilot(CHAT, 111, "TCH-0002", db, accept_weekday=False, accept_plain_title=True, accept_weekly=True)
    nazar = SchedulePilot(CHAT, 222, "TCH-0007", db, accept_weekday=False, accept_plain_title=True)
    lera = SchedulePilot(CHAT, 333, "TCH-0013", db, accept_weekday=False, accept_plain_title=True)
    ai_calls: list[tuple[str, str]] = []

    async def fake_ai(text: str, published: date, sender_tid: str, teachers: list[dict[str, str]]):
        ai_calls.append((sender_tid, text))
        if "Леры" in text:
            return {
                "TCH-0013": {
                    date(2026, 11, 23): [Slot("17:00", 1020, "Иванов Иван", 60)],
                },
            }
        if "неделю" in text:
            return {
                "TCH-0002": {
                    date(2026, 11, 23): [Slot("16:30", 990, "Егор", 60)],
                    date(2026, 11, 24): [Slot("18:00", 1080, "Катя", 45)],
                },
            }
        return {}

    pilots = SchedulePilots([angelina, nazar, lera], ai_parser=fake_ai)

    async def run():
        # 1. Обычный формат Назара → локальный парсер, AI НЕ вызывается
        await pilots.receive(_msg("Расписание:\n17:00 Иванов Иван 45", mid=10, uid=222), 1, edited=False)
        await pilots.wait_ai()
        assert ai_calls == []
        assert len(await nazar.drafts([])) == 1

        # 2. Назар скидывает расписание Леры → локальный пропускает, GPT кладёт в кабинет Леры (TCH-0013)
        await pilots.receive(_msg("Расписание Леры на пн:\n17:00 Иванов Иван 60", mid=11, uid=222), 2, edited=False)
        await pilots.wait_ai()
        assert len(ai_calls) == 1
        lera_drafts = await lera.drafts([])
        assert len(lera_drafts) == 1 and lera_drafts[0]["date"] == "2026-11-23"
        assert len(await nazar.drafts([])) == 1  # у Назара чужой пост не появился

        # 3. Нестандартный недельный пост Ангелины → GPT режет на 2 отдельных дневных черновика
        weekly_text = "Девочки, на неделю так:\n🔥 Пн 23.11\n16.30 Егор (60)\n🔥 Вт 24.11\n18.00 Катя 45\nСр выходной"
        await pilots.receive(_msg(weekly_text, mid=12, uid=111), 3, edited=False)
        await pilots.wait_ai()
        ang_drafts = await angelina.drafts([])
        assert sorted(d["date"] for d in ang_drafts) == ["2026-11-23", "2026-11-24"]

        # 4. Обычный чат без времени → AI вообще не вызывается
        await pilots.receive(_msg("Спасибо всем за тренировку!", mid=13, uid=111), 4, edited=False)
        await pilots.wait_ai()
        assert len(ai_calls) == 2

    asyncio.run(run())


def test_ai_error_degrades_gracefully(tmp_path):
    db = tmp_path / "err.db"
    nazar = SchedulePilot(CHAT, 222, "TCH-0007", db, accept_weekday=False, accept_plain_title=True)

    async def failing_ai(*args):
        raise RuntimeError("OpenAI HTTP 429: insufficient_quota")

    pilots = SchedulePilots([nazar], ai_parser=failing_ai)

    async def run():
        await pilots.receive(_msg("Расписание Леры:\n17:00 Егор", mid=20, uid=222), 1, edited=False)
        await pilots.wait_ai()
        return await nazar.drafts([])

    assert asyncio.run(run()) == []


def test_receipt_check_prefers_openai_over_claude(monkeypatch):
    called: list[str] = []

    async def fake_vertex(data, mime):
        called.append("vertex")
        return rc.ReceiptInfo(5000, "23.11.2026")

    async def fake_openai(data, mime):
        called.append("openai")
        return rc.ReceiptInfo(5000, "23.11.2026")

    async def fake_claude(data, mime):
        called.append("claude")
        return rc.ReceiptInfo(5000, "23.11.2026")

    monkeypatch.setattr(rc, "recognize_vertex_express", fake_vertex)
    monkeypatch.setattr(rc, "recognize_openai", fake_openai)
    monkeypatch.setattr(rc, "recognize_claude", fake_claude)

    monkeypatch.setattr(settings, "openai_api_key", "AQ.fake-express-key")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    assert settings.receipt_check_enabled is True
    assert asyncio.run(rc.recognize_auto(b"img", "image/jpeg")) == rc.ReceiptInfo(5000, "23.11.2026")
    assert called == ["vertex"]

    called.clear()
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    assert asyncio.run(rc.recognize_auto(b"img", "image/jpeg")) == rc.ReceiptInfo(5000, "23.11.2026")
    assert called == ["openai"]

    called.clear()
    monkeypatch.setattr(settings, "openai_api_key", "")
    assert settings.receipt_check_enabled is True
    assert asyncio.run(rc.recognize_auto(b"img", "image/jpeg")) == rc.ReceiptInfo(5000, "23.11.2026")
    assert called == ["claude"]

    monkeypatch.setattr(settings, "anthropic_api_key", "")
    assert settings.receipt_check_enabled is False


def test_schedule_ai_routes_aq_key_to_vertex_express(monkeypatch):
    from bot.services import schedule_ai

    seen: list[tuple[str, str]] = []

    async def fake_vertex(api_key: str, model: str, user_prompt: str):
        seen.append((api_key, model))
        return {
            "is_schedule": True,
            "schedules": [{
                "teacher_id": "TCH-0005",
                "days": [{"date": "2026-11-23", "slots": [{"time": "17:00", "end": None, "duration": 60, "names": "Егор"}]}],
            }],
        }

    monkeypatch.setattr(settings, "openai_api_key", "AQ.fake-express-key")
    monkeypatch.setattr(settings, "openai_schedule_model", "gpt-4o-mini")
    monkeypatch.setattr(schedule_ai, "_call_vertex_express", fake_vertex)

    res = asyncio.run(schedule_ai.parse_schedule_openai(
        "Влад Пн\n17:00 Егор 60",
        date(2026, 11, 23),
        "TCH-0001",
        [{"id": "TCH-0005", "name": "Никишин Влад"}],
    ))
    assert seen == [("AQ.fake-express-key", "gemini-2.5-flash")]
    assert "TCH-0005" in res and date(2026, 11, 23) in res["TCH-0005"]
