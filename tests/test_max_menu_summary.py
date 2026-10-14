"""Сводка над кнопками меню родителя в боте MAX: данные (`parent_views.menu_summary`) и текст (`parent_menu`)."""
import asyncio
from types import SimpleNamespace

from bot.screens import parent_menu
from bot.screens.parent_menu import welcome_text
from bot.services import parent_views
from bot.services.parent_views import ChildSummary, menu_summary
from bot.services.payment_ledger import TeacherLedger


def _ledger(accrued, paid):
    return TeacherLedger(teacher_id="T", name="x", accrued=accrued, paid=paid)


class _Payments:
    def __init__(self, ledgers, lessons):
        self.ledgers, self.lessons, self.synced = ledgers, lessons, []

    async def ledger_for(self, student, period, sync=True):
        self.synced.append(sync)
        return self.ledgers.get((student.student_id, period), {})

    async def student_lesson_marks(self, sid, period):
        return SimpleNamespace(lessons=self.lessons.get(sid, []))


class _Pending:
    async def get_open(self):
        return [SimpleNamespace(kind="receipt", amount=9200, period_month="2026-10", student_id="STU-1"),
                SimpleNamespace(kind="child", amount=0, period_month="", student_id="STU-1"),     # не оплата
                SimpleNamespace(kind="cash", amount=500, period_month="2026-10", student_id="STU-9")]


def test_menu_summary_reads_without_writes(monkeypatch):
    monkeypatch.setattr(parent_views, "current_period", lambda: "2026-10")
    monkeypatch.setattr(parent_views, "visible_periods", lambda n: ["2026-10", "2026-09", "2026-08"])
    lesson = lambda d, m: SimpleNamespace(date=d, duration_min=m)      # noqa: E731
    pay = _Payments(
        {("STU-1", "2026-10"): {"T": _ledger(17000, 7800)}, ("STU-1", "2026-09"): {"T": _ledger(3000, 1400)},
         ("STU-2", "2026-09"): {"T": _ledger(800, 800)}},
        {"STU-1": [lesson("2026-10-02", 45), lesson("2026-10-13", 60)]},
    )
    kids = [SimpleNamespace(student_id="STU-1", name="Иванова Маша"), SimpleNamespace(student_id="STU-2", name="Петров")]
    out = asyncio.run(menu_summary(kids, pay, _Pending()))
    assert set(pay.synced) == {False}                                   # меню ничего не пишет в таблицу
    a, b = out
    assert a.due == [("2026-09", 1600), ("2026-10", 9200)]            # закрытые месяцы первыми
    assert a.pending == [("receipt", 9200, "2026-10")]
    assert (a.lessons, a.minutes, a.last) == (2, 105, "2026-10-13")
    assert (b.due, b.pending, b.accrued, b.lessons) == ([], [], 800, 0)


def test_summary_text(monkeypatch):
    monkeypatch.setattr(parent_menu, "current_period", lambda: "2026-10")
    kid = ChildSummary(name="Иванова Маша", due=[("2026-09", 1600), ("2026-10", 9200)],
                       pending=[("receipt", 9200, "2026-10")], accrued=20000, lessons=2, minutes=105,
                       last="2026-10-13")
    text = welcome_text([SimpleNamespace(name="Иванова Маша")], [kid])
    assert "⚠️ Долг за сентябрь: <b>1600 руб.</b>" in text
    assert "💳 Оплатить за октябрь: <b>9200 руб.</b>" in text
    assert "⏳ Ждёт подтверждения школы: чек 9200 руб. за октябрь" in text
    assert "📅 Октябрь: 2 занятия · 1 ч 45 мин, последнее 13.10" in text
    assert "Всё оплачено" not in text and text.endswith("Выберите раздел:")
    assert "<b>Иванова Маша</b>\n" not in text.split("\n\n", 1)[1]       # один ребёнок — имя не повторяется


def test_summary_text_several_children(monkeypatch):
    monkeypatch.setattr(parent_menu, "current_period", lambda: "2026-10")
    kids = [ChildSummary(name="Петров <A>", accrued=800, lessons=1, minutes=60, last="2026-10-15"),
            ChildSummary(name="Пусто")]                                 # ничего нет — блок пропускается
    text = parent_menu.summary_text(kids, several=True, today="2026-10-15")
    assert text == "<b>Петров &lt;A&gt;</b>\n✅ Всё оплачено\n📅 Октябрь: 1 занятие · 1 ч, последнее сегодня"


def test_welcome_without_summary_unchanged():
    assert welcome_text([SimpleNamespace(name="Иванов")]) == \
        "👨‍👩‍👧 <b>Личный кабинет родителя</b>\nРебёнок: <b>Иванов</b>\n\nВыберите раздел:"
    assert welcome_text([SimpleNamespace(name="Иванов")], []) == welcome_text([SimpleNamespace(name="Иванов")])
