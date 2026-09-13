"""Зафиксированное поведение открытых дефектов из docs/FOUND_BUGS.md.

Тесты намеренно утверждают текущее (ошибочное) поведение: при исправлении дефекта
соответствующий тест переписывается осознанно, а не «ломается» молча.
"""
import pytest

from tests.fakes import ByIdRepo, FakeMessage, StudentGroupRepoFake, StudentRepoFake, mk_group, mk_student, run


def test_b2_wizard_header_shows_raw_shared_kind():
    """B2: для типа «shared» в шапке мастера печатается сырой ключ."""
    from bot.handlers.teacher.record_lesson._base import _header
    assert _header({"lesson_date": "2026-09-01", "kind": "pair", "duration_min": 45}) == \
        "<b>Дата: 01.09.2026 | Тип: Пара | 45 мин</b>\n\n"
    assert _header({"lesson_date": "2026-09-01", "kind": "shared"}) == "<b>Дата: 01.09.2026 | Тип: shared</b>\n\n"
    assert _header({}) == ""


def test_b8_joined_screen_raises_name_error():
    """B8: экран «📅 Месяцы членства» падает с NameError (переменная membership не определена)."""
    from bot.handlers.admin.branches.joined import _render_joined
    msg = FakeMessage()
    with pytest.raises(NameError, match="membership"):
        run(_render_joined(
            msg, "GRP-0002",
            ByIdRepo([mk_group("GRP-0002", "БП БТ Спортивная")], "group_id"),
            StudentRepoFake([mk_student("STU-0001", "Иванов Иван")]),
            StudentGroupRepoFake([("STU-0001", "GRP-0002", "2026-09", "")]),
        ))
