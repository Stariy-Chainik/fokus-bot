"""API кабинета администратора (Mini App): авторизация, роль, экраны, подтверждение оплаты."""
import asyncio
from datetime import date

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.api import register_admin_api
from bot.models.entities import FinanceEntry, TeacherPayout
from bot.repositories.salary_override_repo import SalaryDayOverride
from bot.models.enums import GroupBillingMode, LessonType, PaymentStatus
from bot.services import LessonService, PaymentService, ProfitService, StudentService, TeacherVisibilityService
from bot.services.salary_service import SalaryService
from config.settings import settings
from tests.fakes import (
    BranchRepoFake, ClientRepoWritable, GroupRepoFake, LessonRepoFake, PaymentRepoFake, StudentGroupRepoWritable,
    StudentRepoWritable, SubOverrideRepoFake, SubmissionRepoFake, TeacherGroupRepoWritable, TeacherRepoFake, UserRepoWritable,
    mk_branch, mk_group, mk_lesson, mk_payment, mk_student, mk_submission, mk_teacher, mk_user,
)
from tests.test_telegram_auth import TOKEN, make_init_data

ADMIN_TG, PARENT_TG = 826576855, 5037902894
YM = date.today().strftime("%Y-%m")


class FinanceRepoFake:
    def __init__(self) -> None:
        self.items: list[FinanceEntry] = []

    async def get_all(self):
        return list(self.items)

    async def get_by_period(self, period):
        return [e for e in self.items if e.period_month == period]

    async def add(self, period, kind, title, amount):
        e = FinanceEntry(entry_id=f"FIN-{len(self.items) + 1:06d}", period_month=period, kind=kind, title=title,
                         amount=amount, created_at="2026-09-18 10:00:00")
        self.items.append(e)
        return e

    async def delete(self, entry_id):
        before = len(self.items)
        self.items = [e for e in self.items if e.entry_id != entry_id]
        return len(self.items) < before


class PayoutRepoFake:
    def __init__(self) -> None:
        self.items: list[TeacherPayout] = []

    async def get_all(self):
        return list(self.items)

    async def get_by_period(self, period):
        return [p for p in self.items if p.period_month == period]

    async def get_by_teacher_period(self, teacher_id, period):
        return [p for p in self.items if p.teacher_id == teacher_id and p.period_month == period]

    async def add(self, teacher_id, period, amount, paid_by_tg_id, comment=""):
        p = TeacherPayout(payout_id=f"PO-{len(self.items) + 1:06d}", teacher_id=teacher_id, period_month=period,
                          amount=amount, paid_at="2026-09-18 10:00:00", paid_by_tg_id=paid_by_tg_id, comment=comment)
        self.items.append(p)
        return p


class SalaryOverrideRepoFake:
    def __init__(self) -> None:
        self.items: list[SalaryDayOverride] = []

    async def get_all(self):
        return list(self.items)

    async def get_for_teacher_period(self, teacher_id, period):
        return [o for o in self.items if o.teacher_id == teacher_id and o.date.startswith(period)]

    async def add(self, teacher_id, day, minutes, comment, created_by):
        self.items = [o for o in self.items if not (o.teacher_id == teacher_id and o.date == day)]
        o = SalaryDayOverride(f"SO-{len(self.items) + 1:06d}", teacher_id, day, minutes, comment, "2026-09-18 10:00:00", created_by)
        self.items.append(o)
        return o

    async def delete(self, override_id):
        before = len(self.items)
        self.items = [o for o in self.items if o.override_id != override_id]
        return len(self.items) < before


class NotifierFake:
    def __init__(self) -> None:
        self.sent: list[tuple[list, str]] = []

    async def send_many(self, addrs, text, rows=None):
        addrs = list(addrs)
        self.sent.append((addrs, text))
        return len(addrs)


def _dp():
    teacher = mk_teacher("TCH-0001", "Река Станислав", rate_group=1000, rate_for_teacher=1500, rate_for_student=2000)
    students = [mk_student("STU-0001", "Иванов Иван", parent_tg_ids=[PARENT_TG]), mk_student("STU-0002", "Петрова Анна")]
    groups = [mk_group("GRP-0001", "БП Джаз", billing_mode=GroupBillingMode.PER_VISIT, price_full=800)]
    lessons = [
        mk_lesson("LES-1", teacher, f"{YM}-03", students=[("STU-0001", "Иванов Иван")]),                 # 2000 ₽
        mk_lesson("LES-2", teacher, f"{YM}-10", students=[("STU-0001", "Иванов Иван")]),                 # 2000 ₽
        mk_lesson("LES-3", teacher, f"{YM}-12", duration=60, lesson_type=LessonType.GROUP,
                  attendees="STU-0001:60:800,STU-0002:60:800", group_id="GRP-0001"),
    ]
    student_repo, teacher_repo = StudentRepoWritable(students), TeacherRepoFake([teacher])
    group_repo, branch_repo = GroupRepoFake(groups), BranchRepoFake([mk_branch()])
    sg_repo = StudentGroupRepoWritable([("STU-0001", "GRP-0001", "", ""), ("STU-0002", "GRP-0001", "", "")])
    tg_repo = TeacherGroupRepoWritable({"TCH-0001": ["GRP-0001"]})
    lesson_repo, payment_repo, client_repo = LessonRepoFake(lessons), PaymentRepoFake([]), ClientRepoWritable([])
    sub_override_repo = SubOverrideRepoFake()
    payment_service = PaymentService(payment_repo, lesson_repo, teacher_repo, group_repo=group_repo,
                                     student_group_repo=sg_repo, subscription_override_repo=sub_override_repo)
    salary_service = SalaryService(lesson_repo)
    finance_repo = FinanceRepoFake()
    submission_repo = SubmissionRepoFake([mk_submission("TCH-0001", "2026-08")])
    visibility = TeacherVisibilityService(student_repo, tg_repo, sg_repo)
    return {
        "lesson_service": LessonService(lesson_repo, submission_repo, teacher_repo, salary_service=salary_service),
        "finance_entry_repo": finance_repo, "payout_repo": PayoutRepoFake(), "salary_override_repo": SalaryOverrideRepoFake(),
        "profit_service": ProfitService(teacher_repo, lesson_repo, payment_service, finance_repo, salary_service=salary_service),
        "notifier": NotifierFake(),
        "user_repo": UserRepoWritable([mk_user(ADMIN_TG, is_admin=True), mk_user(PARENT_TG)]),
        "subscription_override_repo": sub_override_repo,
        "student_repo": student_repo, "teacher_repo": teacher_repo, "group_repo": group_repo, "branch_repo": branch_repo,
        "student_group_repo": sg_repo, "teacher_group_repo": tg_repo, "lesson_repo": lesson_repo,
        "payment_repo": payment_repo, "submission_repo": submission_repo,
        "payment_service": payment_service, "salary_service": salary_service, "client_repo": client_repo,
        "visibility": visibility,
        "student_service": StudentService(student_repo, teacher_repo, group_repo, branch_repo, sg_repo, client_repo, visibility),
    }


def make_api(monkeypatch):
    """(dp, dp): приложение aiohttp создаётся на каждый вызов — у каждого свой event loop."""
    monkeypatch.setattr(settings, "bot_token", TOKEN)
    monkeypatch.setattr(settings, "miniapp_dev_tg_id", None)
    for name in ("owner_teacher_ids", "direct_pay_teacher_ids", "hall_rent_per_lesson", "debtors_since_period", "shift_groups",
                 "salary_duration_groups", "revenue_share_groups"):
        if hasattr(settings, name):
            monkeypatch.setattr(settings, name, "")
    dp = _dp()
    return dp, dp


@pytest.fixture()
def api(monkeypatch):
    return make_api(monkeypatch)


def _call(dp, method, path, tg_id=ADMIN_TG, json=None, headers=None):
    async def run():
        app = web.Application()
        register_admin_api(app, dp)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            h = headers if headers is not None else {"Authorization": f"tma {make_init_data(user_id=tg_id)}"}
            resp = await client.request(method, path, headers=h, json=json)
            return resp.status, await resp.json()
        finally:
            await client.close()
    return asyncio.run(run())


def test_requires_valid_init_data_and_admin_role(api):
    app, _ = api
    assert _call(app, "GET", "/api/admin/me", headers={})[0] == 401
    assert _call(app, "GET", "/api/admin/me", headers={"Authorization": "tma broken"})[0] == 401
    assert _call(app, "GET", "/api/admin/me", tg_id=PARENT_TG)[0] == 403       # родитель — не админ
    status, body = _call(app, "GET", "/api/admin/me")
    assert (status, body["isAdmin"], body["tgId"]) == (200, True, ADMIN_TG)


def test_dev_header_only_when_configured(api, monkeypatch):
    app, _ = api
    assert _call(app, "GET", "/api/admin/me", headers={"Authorization": "dev"})[0] == 401
    monkeypatch.setattr(settings, "miniapp_dev_tg_id", ADMIN_TG)
    assert _call(app, "GET", "/api/admin/me", headers={"Authorization": "dev"})[0] == 200


def test_students_list_and_card(api):
    app, _ = api
    status, body = _call(app, "GET", "/api/admin/students?q=иван")
    assert status == 200 and [s["name"] for s in body["students"]] == ["Иванов Иван"]
    assert body["students"][0]["groups"] == ["БП Джаз"] and body["students"][0]["hasParent"] is True
    assert body["total"] == 2
    assert [s["name"] for s in _call(app, "GET", "/api/admin/students?group=GRP-0001")[1]["students"]] == ["Иванов Иван", "Петрова Анна"]
    assert [s["name"] for s in _call(app, "GET", "/api/admin/students?group=GRP-0404")[1]["students"]] == []
    assert [s["name"] for s in _call(app, "GET", "/api/admin/students?noparent=1")[1]["students"]] == ["Петрова Анна"]
    assert _call(app, "GET", "/api/admin/students?debt=1")[1]["students"] == []          # долгов за закрытые месяцы нет
    status, card = _call(app, "GET", "/api/admin/students/STU-0001")
    assert status == 200 and card["name"] == "Иванов Иван" and card["teachers"] == ["Река Станислав"]
    cur = next(m for m in card["months"] if m["period"] == YM)
    assert (cur["total"], cur["paid"], cur["rest"]) == (4800, 0, 4800)
    assert _call(app, "GET", "/api/admin/students/STU-0404")[0] == 404


def test_teachers_list_and_card(api):
    app, _ = api
    status, body = _call(app, "GET", "/api/admin/teachers")
    assert status == 200 and body["teachers"][0]["groups"] == ["БП Джаз"]
    status, card = _call(app, "GET", "/api/admin/teachers/TCH-0001")
    assert status == 200 and card["rates"] == {"group": 1000, "teacher": 1500, "student": 2000}
    assert card["submitted"] == ["2026-08"] and card["salary"] == 1500 + 1500 + 1333


def test_pay_flow_marks_and_confirm(api):
    app, dp = api
    status, groups = _call(app, "GET", "/api/admin/pay/groups")
    assert status == 200 and groups["branches"][0]["groups"][0]["name"] == "БП Джаз"
    status, sts = _call(app, "GET", f"/api/admin/pay/students?ym={YM}&group=GRP-0001")
    assert [(s["name"], s["rest"]) for s in sts["students"]] == [("Иванов Иван", 4800), ("Петрова Анна", 800)]

    status, pos = _call(app, "GET", f"/api/admin/pay/student/STU-0001?ym={YM}")
    assert status == 200 and {p["key"]: p["remainder"] for p in pos["positions"]} == {"TCH-0001": 4800}
    assert pos["positions"][0]["unpaidLessons"] == 3 and pos["positions"][0]["pending"]["amount"] == 4800

    status, marks = _call(app, "GET", f"/api/admin/pay/marks/STU-0001?ym={YM}&key=TCH-0001")
    assert [(m["amount"], m["paid"]) for m in marks["marks"]] == [(2000, False), (2000, False), (800, False)]

    status, res = _call(app, "POST", "/api/admin/pay/confirm",
                        json={"studentId": "STU-0001", "periodMonth": YM, "key": "TCH-0001", "amount": 2000, "method": "cash"})
    assert status == 200 and res["credited"] == 2000
    status, marks = _call(app, "GET", f"/api/admin/pay/marks/STU-0001?ym={YM}&key=TCH-0001")
    assert [m["paid"] for m in marks["marks"]] == [True, False, False]          # закрыто самое раннее занятие
    assert marks["ledger"]["remainder"] == 2800
    paid_rows = [p for p in dp["payment_repo"].rows if p.status == PaymentStatus.PAID]
    assert [(p.total_amount, p.payment_method) for p in paid_rows] == [(2000, "cash")]

    bad = {"studentId": "STU-0001", "periodMonth": YM, "key": "TCH-0001", "amount": 0}
    assert _call(app, "POST", "/api/admin/pay/confirm", json=bad)[0] == 400


def test_bill_and_debtors(api):
    app, dp = api
    dp["payment_repo"].rows.append(mk_payment("PAY-9", "STU-0002", "2026-08", "TCH-0001", 1500))   # старый долг Петровой
    dp["lesson_repo"].items.append(mk_lesson("LES-0", await_teacher(dp), "2026-08-20", students=[("STU-0002", "Петрова Анна")]))
    status, bill = _call(app, "GET", f"/api/admin/bill/STU-0001?ym={YM}")
    assert status == 200 and bill["groups"] == ["БП Джаз"] and bill["total"] == 4800
    assert [r["name"] for r in bill["rows"]] == ["Река Станислав"] and len(bill["rows"][0]["items"]) == 3
    status, d = _call(app, "GET", "/api/admin/debtors")
    assert status == 200
    petrova = next(r for r in d["debtors"] if r["id"] == "STU-0002")
    assert petrova["closedTotal"] == 2000 and petrova["months"]["2026-08"] == 2000 and petrova["currentTotal"] == 800


def await_teacher(dp):
    return asyncio.run(dp["teacher_repo"].get_by_id("TCH-0001"))


def test_bill_send_requires_bot(api):
    app, _ = api
    assert _call(app, "POST", f"/api/admin/bill/STU-0001/send?ym={YM}")[0] == 503


def test_home_lists_who_marked_lessons_today(api):
    """Сводка отдаёт разбивку «отмечено сегодня» по педагогам — чипы фильтра на главной."""
    from datetime import date as _date
    app, dp = api
    today = _date.today().isoformat()
    second = mk_teacher("TCH-0002", "Никишин Влад", rate_group=1000, rate_for_teacher=1500, rate_for_student=2000)
    dp["teacher_repo"].items.append(second)
    first = dp["teacher_repo"].items[0]
    dp["lesson_repo"].items += [
        mk_lesson("LES-T1", first, today, students=[("STU-0001", "Иванов Иван")]),
        mk_lesson("LES-T2", second, today, students=[("STU-0002", "Петрова Анна")]),
        mk_lesson("LES-T3", second, today, students=[("STU-0001", "Иванов Иван")]),
    ]
    status, h = _call(app, "GET", "/api/admin/home")
    assert status == 200 and h["lessonsToday"] == 3
    assert h["todayTeachers"] == [                       # сначала тот, кто отметил больше
        {"id": "TCH-0002", "name": "Никишин Влад", "lessons": 2},
        {"id": "TCH-0001", "name": "Река Станислав", "lessons": 1},
    ]
    assert sum(t["lessons"] for t in h["todayTeachers"]) == h["lessonsToday"]


def test_home_without_lessons_today_has_no_teacher_chips(api):
    app, _dp = api
    h = _call(app, "GET", "/api/admin/home")[1]
    assert h["lessonsToday"] == 0 and h["todayTeachers"] == []
