"""Характеризующие тесты для StudentService.get_student_card().

Репозитории заменены in-memory фейками; visibility — настоящий
TeacherVisibilityService поверх тех же фейков. Async — через asyncio.run.
"""
import asyncio
from dataclasses import replace

from bot.models import Student, Teacher, Group, Branch, Client, StudentGroup
from bot.models.enums import GroupBillingMode, StudentGroupTier
from bot.services import StudentService, TeacherVisibilityService, TierToggleError


def _run(coro):
    return asyncio.run(coro)


class _FakeStudentRepo:
    def __init__(self, students):
        self._students = students

    async def get_all(self):
        # Свежие копии, чтобы мутация group_ids не текла между тестами.
        return [replace(s) for s in self._students]

    async def get_by_id(self, student_id):
        for s in self._students:
            if s.student_id == student_id:
                return replace(s)
        return None

    async def update_tier(self, student_id, tier):
        for s in self._students:
            if s.student_id == student_id:
                s.group_tier = tier
                return

    async def add(self, name):
        student = Student(f"STU-NEW-{len(self._students) + 1}", name)
        self._students.append(student)
        return student


class _FakeTeacherGroupRepo:
    def __init__(self, teacher_to_groups):
        self._t2g = teacher_to_groups
        self._g2t = {}
        for tid, gids in teacher_to_groups.items():
            for gid in gids:
                self._g2t.setdefault(gid, []).append(tid)

    async def get_groups_for_teacher(self, teacher_id):
        return list(self._t2g.get(teacher_id, []))

    async def get_teachers_for_group(self, group_id):
        return list(self._g2t.get(group_id, []))


class _FakeStudentGroupRepo:
    def __init__(self, student_to_groups):
        self._s2g = student_to_groups
        self._all = [StudentGroup(student_id=sid, group_id=gid)
                     for sid, gids in student_to_groups.items() for gid in gids]

    async def get_all(self):
        return list(self._all)

    async def get_groups_for_student(self, student_id):
        return list(self._s2g.get(student_id, []))

    async def get_students_for_group(self, group_id):
        return [sid for sid, gids in self._s2g.items() if group_id in gids]

    async def get_map_by_student(self):
        return {sid: list(gids) for sid, gids in self._s2g.items()}

    async def add(self, student_id, group_id):
        self._s2g.setdefault(student_id, []).append(group_id)
        self._all.append(StudentGroup(student_id=student_id, group_id=group_id))


class _FakeByIdRepo:
    """Общий фейк для справочных репо с get_all/get_by_id (teacher/group/branch/client)."""

    def __init__(self, items, key):
        self._items = list(items)
        self._key = key

    async def get_all(self):
        return list(self._items)

    async def get_by_id(self, id_):
        return next((x for x in self._items if self._key(x) == id_), None)


def _service(
    students=None, teachers=None, groups=None, branches=None, clients=None,
    teacher_to_groups=None, student_to_groups=None,
):
    students = students if students is not None else [
        Student("STU-1", "Иванов Ваня", partner_id="STU-2", client_id="CLT-0001"),
        Student("STU-2", "Петров Петя"),
    ]
    teachers = teachers if teachers is not None else [
        Teacher("TCH-1", None, "Педагог Один", 500, 800, 900),
    ]
    groups = groups if groups is not None else [
        Group(group_id="GRP-0001", branch_id="BRN-001", name="ЮБ",
              billing_mode=GroupBillingMode.PER_VISIT,
              price_short=500, duration_short=35, price_full=700, duration_full=60),
    ]
    branches = branches if branches is not None else [Branch("BRN-001", "Центр")]
    clients = clients if clients is not None else [
        Client("CLT-0001", "Иванова Мама", tg_id=111, phone="+79990000000"),
    ]
    teacher_to_groups = teacher_to_groups if teacher_to_groups is not None else {"TCH-1": ["GRP-0001"]}
    student_to_groups = student_to_groups if student_to_groups is not None else {
        "STU-1": ["GRP-0001"], "STU-2": [],
    }

    student_repo = _FakeStudentRepo(students)
    tg_repo = _FakeTeacherGroupRepo(teacher_to_groups)
    sg_repo = _FakeStudentGroupRepo(student_to_groups)
    visibility = TeacherVisibilityService(student_repo, tg_repo, sg_repo)
    return StudentService(
        student_repo,
        _FakeByIdRepo(teachers, lambda t: t.teacher_id),
        _FakeByIdRepo(groups, lambda g: g.group_id),
        _FakeByIdRepo(branches, lambda b: b.branch_id),
        sg_repo,
        _FakeByIdRepo(clients, lambda c: c.client_id),
        visibility,
    )


def test_unknown_student_returns_none():
    assert _run(_service().get_student_card("STU-404")) is None


def test_full_card_groups_teachers_partner_client():
    card = _run(_service().get_student_card("STU-1"))
    assert card.student.student_id == "STU-1"
    assert card.student.group_ids == ["GRP-0001"]  # мутация как в старом хендлере
    assert card.teacher_names == ["Педагог Один"]
    assert card.partner is not None and card.partner.name == "Петров Петя"
    assert [g.group_id for g in card.groups] == ["GRP-0001"]
    assert card.groups[0].group.name == "ЮБ"
    assert card.groups[0].branch_name == "Центр"
    assert card.primary_group is card.groups[0].group
    assert card.client.name == "Иванова Мама"


def test_bare_student_no_groups_no_partner_no_client():
    card = _run(_service().get_student_card("STU-2"))
    assert card.teacher_names == []          # рендер покажет «не привязан»
    assert card.partner is None and card.student.partner_id is None
    assert card.groups == [] and card.primary_group is None
    assert card.client is None and card.student.client_id is None


def test_dangling_partner_reference_kept_on_student():
    svc = _service(students=[Student("STU-1", "Иванов Ваня", partner_id="STU-GONE")])
    card = _run(svc.get_student_card("STU-1"))
    # Партнёр не найден, но ссылка остаётся — рендер покажет «(удалён: STU-GONE)».
    assert card.partner is None
    assert card.student.partner_id == "STU-GONE"


def test_dangling_group_reference_yields_row_without_group():
    svc = _service(student_to_groups={"STU-1": ["GRP-GONE", "GRP-0001"], "STU-2": []})
    card = _run(svc.get_student_card("STU-1"))
    assert [g.group_id for g in card.groups] == ["GRP-GONE", "GRP-0001"]
    assert card.groups[0].group is None
    # primary_group — первая НАЙДЕННАЯ группа, а не первая по списку.
    assert card.primary_group.group_id == "GRP-0001"


def test_missing_branch_falls_back_to_branch_id():
    svc = _service(branches=[])
    card = _run(svc.get_student_card("STU-1"))
    assert card.groups[0].branch_name == "BRN-001"


def test_unknown_teacher_id_falls_back_to_id():
    svc = _service(teachers=[], teacher_to_groups={"TCH-GONE": ["GRP-0001"]})
    card = _run(svc.get_student_card("STU-1"))
    assert card.teacher_names == ["TCH-GONE"]


def test_dangling_client_reference_kept_on_student():
    svc = _service(clients=[])
    card = _run(svc.get_student_card("STU-1"))
    # Клиент не найден — рендер предложит «Создать клиента», client_id остаётся.
    assert card.client is None
    assert card.student.client_id == "CLT-0001"


# ─── create_with_group ───────────────────────────────────────────────────────

def test_create_with_group_links_and_describes_group():
    svc = _service()
    created = _run(svc.create_with_group("Сидоров Сеня", "GRP-0001"))
    assert created.student.name == "Сидоров Сеня"
    assert created.group.name == "ЮБ"
    assert created.branch_name == "Центр"
    card = _run(svc.get_student_card(created.student.student_id))
    assert card.student.group_ids == ["GRP-0001"]


def test_create_without_group():
    svc = _service()
    created = _run(svc.create_with_group("Сидоров Сеня", ""))
    assert created.group is None and created.branch_name == ""
    card = _run(svc.get_student_card(created.student.student_id))
    assert card.student.group_ids == []


def test_create_with_unknown_group_still_links_membership():
    # Как в старом хендлере: членство пишется до чтения группы,
    # битый group_id даёт запись + пустой group_info.
    svc = _service()
    created = _run(svc.create_with_group("Сидоров Сеня", "GRP-GONE"))
    assert created.group is None
    card = _run(svc.get_student_card(created.student.student_id))
    assert card.student.group_ids == ["GRP-GONE"]


def test_create_with_group_missing_branch_shows_dash():
    svc = _service(branches=[])
    created = _run(svc.create_with_group("Сидоров Сеня", "GRP-0001"))
    assert created.branch_name == "—"


# ─── toggle_tier ─────────────────────────────────────────────────────────────

def test_toggle_tier_unknown_student():
    assert _run(_service().toggle_tier("STU-404")) is TierToggleError.STUDENT_NOT_FOUND


def test_toggle_tier_no_groups():
    assert _run(_service().toggle_tier("STU-2")) is TierToggleError.NO_GROUPS


def test_toggle_tier_no_per_visit_group():
    svc = _service(groups=[
        Group(group_id="GRP-0001", branch_id="BRN-001", name="ЮБ",
              billing_mode=GroupBillingMode.NONE),
    ])
    assert _run(svc.toggle_tier("STU-1")) is TierToggleError.NO_PER_VISIT_GROUP


def test_toggle_tier_flips_full_to_short_and_back():
    svc = _service()
    assert _run(svc.toggle_tier("STU-1")) is None
    assert _run(svc.get_student_card("STU-1")).student.group_tier == StudentGroupTier.SHORT
    assert _run(svc.toggle_tier("STU-1")) is None
    assert _run(svc.get_student_card("STU-1")).student.group_tier == StudentGroupTier.FULL


# ─── partner_candidates ──────────────────────────────────────────────────────

def _pair_world():
    """Мини-мир для кандидатов: G1 = {STU-1, STU-2, STU-3}, G2 = {STU-4}.

    STU-2 уже в паре со STU-3 (⚠️-флаг), STU-4 в другой группе — не кандидат.
    """
    students = [
        Student("STU-1", "Бета Ученик"),
        Student("STU-2", "Альфа Ученик", partner_id="STU-3"),
        Student("STU-3", "Гамма Ученик", partner_id="STU-2"),
        Student("STU-4", "Дельта Ученик"),
    ]
    student_to_groups = {
        "STU-1": ["GRP-0001"], "STU-2": ["GRP-0001"],
        "STU-3": ["GRP-0001"], "STU-4": ["GRP-0002"],
    }
    return students, student_to_groups


def test_partner_candidates_none_when_student_has_no_groups():
    svc = _service(student_to_groups={"STU-1": [], "STU-2": []})
    student = _run(svc.get_student_card("STU-1")).student
    assert _run(svc.partner_candidates(student)) is None


def test_partner_candidates_shared_group_sorted_with_partner_flag():
    students, s2g = _pair_world()
    svc = _service(students=students, student_to_groups=s2g)
    lead = students[0]  # STU-1, без партнёра
    result = _run(svc.partner_candidates(lead))
    # Сортировка по имени; сам ученик и чужая группа исключены; флаг — «уже в паре».
    assert [(s.student_id, flag) for s, flag in result] == [
        ("STU-2", True), ("STU-3", True),
    ]


def test_partner_candidates_excludes_current_partner():
    students, s2g = _pair_world()
    svc = _service(students=students, student_to_groups=s2g)
    lead = students[1]  # STU-2, партнёр STU-3
    result = _run(svc.partner_candidates(lead))
    # Текущий партнёр (STU-3) исключён, остаётся только STU-1.
    assert [(s.student_id, flag) for s, flag in result] == [("STU-1", False)]


def test_partner_candidates_empty_when_alone_in_group():
    svc = _service(student_to_groups={"STU-1": ["GRP-0001"], "STU-2": []})
    student = _run(svc.get_student_card("STU-1")).student
    result = _run(svc.partner_candidates(student))
    assert result == []  # группы есть, кандидатов нет — не None


def test_partner_candidates_in_group_limits_to_members():
    students, s2g = _pair_world()
    svc = _service(students=students, student_to_groups=s2g)
    lead = students[0]  # STU-1
    result = _run(svc.partner_candidates_in_group(lead, "GRP-0001"))
    assert [(s.student_id, flag) for s, flag in result] == [
        ("STU-2", True), ("STU-3", True),
    ]
    # Лид не обязан быть членом группы — кандидаты берутся из состава group_id.
    other_group = _run(svc.partner_candidates_in_group(lead, "GRP-0002"))
    assert [(s.student_id, flag) for s, flag in other_group] == [("STU-4", False)]
