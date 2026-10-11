"""Удалённый ученик (решение владельца 11.10.2026): его долг нигде не считается, а номер повторно не выдаётся —
иначе новый ученик унаследовал бы чужие занятия, оплаты и долг (STU-0283: Андреева → Молярова, 05–06.10.2026)."""
import asyncio

from bot.repositories.base import BaseRepository
from bot.repositories.student_repo import StudentRepository
from tests.fakes import FakeWorksheet
from tests.test_admin_api import YM, _call, make_api

STUDENT_HEADERS = ["student_id", "name", "partner_id", "group_id", "group_tier", "client_id", "parent_tg_ids",
                   "kindergarten_group", "athlete_tg_id", "parent_max_ids"]
LESSON_HEADERS = ["lesson_id", "teacher_id", "teacher_name", "type", "student_1_id", "student_1_name", "student_2_id",
                  "student_2_name", "date", "duration_min", "earned", "recorded_at", "updated_at", "attendees", "group_id",
                  "student_3_id", "student_3_name", "student_4_id", "student_4_name"]


class _Sheets:
    """Несколько листов по имени; отсутствующий лист — ошибка, как у Google."""
    def __init__(self, sheets: dict) -> None:
        self.sheets = sheets

    def get_worksheet(self, name):
        return self.sheets[name]

    def invalidate_cache(self, name=None):
        pass


def _clean():
    BaseRepository._cache.clear(), BaseRepository._locks.clear(), BaseRepository._headers.clear()


def test_deleted_student_debt_is_not_counted_anywhere(monkeypatch):
    dp, _ = make_api(monkeypatch)
    before = _call(dp, "GET", f"/api/admin/home?ym={YM}")[1]
    assert before["collected"]["accrued"] == 5600                       # Иванов 2000 + 2000 + 800, Петрова 800
    dp["student_repo"].items = [s for s in dp["student_repo"].items if s.student_id != "STU-0001"]   # Иванова удалили
    ledger = asyncio.run(dp["payment_service"].compute_ledger_map())
    assert not [k for k in ledger if k[0] == "STU-0001"]
    after = _call(dp, "GET", f"/api/admin/home?ym={YM}")[1]
    assert after["collected"]["accrued"] == 800 and after["lessons"]["plan"] == 800     # остался долг Петровой


def test_new_student_never_reuses_number_of_deleted_one():
    _clean()
    students = FakeWorksheet(STUDENT_HEADERS, [["STU-0001", "Иванов Иван", "", "", "full", "", "", "", "", ""],
                                               ["STU-0002", "Петрова Анна", "", "", "full", "", "", "", "", ""]])
    # STU-0005 удалён из листа учеников, но его занятие осталось в журнале
    lessons = FakeWorksheet(LESSON_HEADERS, [["LES-1", "TCH-0001", "Река", "individual", "STU-0005", "Удалённый", "", "",
                                              "2026-10-01", 45, 0, "", "", "", "", "", "", "", ""]])
    repo = StudentRepository(_Sheets({"students": students, "lessons": lessons}), "students")
    s = asyncio.run(repo.add("Новая Ученица"))
    assert s.student_id == "STU-0006"                                   # не STU-0003 и не STU-0005

    async def two():
        return await asyncio.gather(repo.add("Первая"), repo.add("Вторая"))
    a, b = asyncio.run(two())
    assert {a.student_id, b.student_id} == {"STU-0007", "STU-0008"}     # одновременные — разные номера
    _clean()
