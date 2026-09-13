"""bot/services/rosters.group_members — состав группы с разными ключами сортировки."""
from bot.services.rosters import BY_NAME_CI, group_members
from tests.fakes import StudentGroupRepoFake, StudentRepoFake, mk_student, run


def _world():
    students = StudentRepoFake([
        mk_student("STU-0003", "иванов Ваня"), mk_student("STU-0001", "Яковлев Пётр"),
        mk_student("STU-0002", "Белова Анна"), mk_student("STU-0004", "Чужой Ученик"),
    ])
    rows = StudentGroupRepoFake([
        ("STU-0001", "GRP-1", "", ""), ("STU-0002", "GRP-1", "", ""), ("STU-0003", "GRP-1", "", ""),
        ("STU-0004", "GRP-2", "", ""), ("STU-0009", "GRP-1", "", ""),          # битая ссылка — пропускается
        ("STU-0005", "GRP-1", "", "2026-09"),                                  # ушёл
    ])
    students.items.append(mk_student("STU-0005", "Ушедший Ученик"))
    return students, rows


def test_group_members_sort_variants():
    students, rows = _world()
    assert [s.student_id for s in run(group_members(students, rows, "GRP-1"))] == ["STU-0002", "STU-0001", "STU-0003"]
    assert [s.name for s in run(group_members(students, rows, "GRP-1", key=BY_NAME_CI))] == [
        "Белова Анна", "иванов Ваня", "Яковлев Пётр"]
    # key=None — порядок листа students (не student_groups)
    assert [s.student_id for s in run(group_members(students, rows, "GRP-1", key=None))] == ["STU-0003", "STU-0001", "STU-0002"]
    assert run(group_members(students, rows, "GRP-404")) == []


def test_group_members_include_left():
    students, rows = _world()
    assert "STU-0005" not in {s.student_id for s in run(group_members(students, rows, "GRP-1"))}
    assert "STU-0005" in {s.student_id for s in run(group_members(students, rows, "GRP-1", include_left=True))}
