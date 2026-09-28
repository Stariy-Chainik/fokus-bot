"""Напоминания педагогам: сравнение расписания дня с отмеченными занятиями."""
from datetime import date
from types import SimpleNamespace as NS

from bot.repositories.group_schedule_repo import ScheduleSlot
from bot.services.lesson_reminders import missing_for_day

MON = date(2026, 9, 28)          # понедельник


def _ls(gid, day, tid="TCH-1"):
    return NS(group_id=gid, date=day, teacher_id=tid)


def test_missing_counts_slots_minus_recorded_and_picks_recent_teacher():
    groups = {"G1": NS(name="Старшая", archived=False), "G2": NS(name="Младшая", archived=False),
              "G3": NS(name="Архив", archived=True)}
    slots = [ScheduleSlot("S1", "G1", 1, "17:00", "18:00"), ScheduleSlot("S2", "G1", 1, "18:00", "19:00"),
             ScheduleSlot("S3", "G2", 1, "17:00", "18:00", "TCH-9"), ScheduleSlot("S4", "G3", 1, "10:00", "11:00"),
             ScheduleSlot("S5", "G2", 3, "17:00", "18:00")]
    lessons = [_ls("G1", "2026-09-28", "TCH-6"), _ls("G1", "2026-09-21", "TCH-6"), _ls("G1", "2026-09-14", "TCH-6")]
    res = {m.group_id: m for m in missing_for_day(MON, slots, lessons, groups, {"G1": ["TCH-6", "TCH-9"], "G2": ["TCH-9"]})}
    assert set(res) == {"G1", "G2"}                          # архивная и среда — не в счёт
    assert (res["G1"].recorded, res["G1"].missing, res["G1"].teacher_ids) == (1, 1, ["TCH-6"])   # вела Лобачева
    assert (res["G2"].missing, res["G2"].teacher_ids) == (1, ["TCH-9"])                      # педагог слота


def test_nothing_missing_when_all_recorded_and_fallback_to_assigned():
    groups = {"G1": NS(name="Старшая", archived=False)}
    slots = [ScheduleSlot("S1", "G1", 1, "17:00", "18:00")]
    assert missing_for_day(MON, slots, [_ls("G1", "2026-09-28")], groups, {}) == []
    res = missing_for_day(MON, slots, [], groups, {"G1": ["TCH-2", "TCH-3"]})
    assert res[0].teacher_ids == ["TCH-2", "TCH-3"]          # никто не вёл недавно — все педагоги группы
