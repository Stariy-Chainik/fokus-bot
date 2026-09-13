"""Зафиксированное поведение открытых дефектов из docs/FOUND_BUGS.md.

Тесты намеренно утверждают текущее (ошибочное) поведение: при исправлении дефекта
соответствующий тест переписывается осознанно, а не «ломается» молча.
"""
from tests.fakes import (
    ByIdRepo, FakeMessage, StudentGroupRepoFake, StudentRepoFake, assert_golden, mk_group, mk_student, run, screen_dump,
)


def test_b2_wizard_header_shows_raw_shared_kind():
    """B2: для типа «shared» в шапке мастера печатается сырой ключ."""
    from bot.handlers.teacher.record_lesson._base import _header
    assert _header({"lesson_date": "2026-09-01", "kind": "pair", "duration_min": 45}) == \
        "<b>Дата: 01.09.2026 | Тип: Пара | 45 мин</b>\n\n"
    assert _header({"lesson_date": "2026-09-01", "kind": "shared"}) == "<b>Дата: 01.09.2026 | Тип: shared</b>\n\n"
    assert _header({}) == ""


def test_b8_joined_screen_renders_membership_months():
    """B8 (исправлен 2026-09-13): экран «📅 Месяцы членства» показывает месяцы вступления/ухода."""
    from bot.handlers.admin.branches.joined import _render_joined
    msg = FakeMessage()
    run(_render_joined(
        msg, "GRP-0002",
        ByIdRepo([mk_group("GRP-0002", "БП БТ Спортивная")], "group_id"),
        StudentRepoFake([mk_student("STU-0001", "Иванов Иван"), mk_student("STU-0002", "Петрова Анна"),
                         mk_student("STU-0003", "Сидоров Пётр")]),
        StudentGroupRepoFake([("STU-0001", "GRP-0002", "2026-09", ""), ("STU-0002", "GRP-0002", "", ""),
                              ("STU-0003", "GRP-0002", "2026-05", "2026-09")]),
    ))
    assert_golden("joined_screen", screen_dump(*msg.last))
