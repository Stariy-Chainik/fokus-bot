"""Валидация DSL-настроек из .env и проверки /health + /health/status."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot import __main__ as main_mod
from config.settings import Settings


def _cfg(**overrides) -> Settings:
    return Settings(
        BOT_TOKEN="123:TEST",
        GOOGLE_CREDENTIALS_JSON="{}",
        SPREADSHEET_ID="sheet-id",
        **overrides,
    )


def test_validate_dsl_accepts_valid_rules():
    cfg = _cfg(
        SUBSCRIPTION_SHARE_GROUPS="GRP-0020:30:2026-10,GRP-0021:50",
        MERGEABLE_GROUPS="GRP-0001+GRP-0002,GRP-0003+GRP-0004+GRP-0005",
        HALL_RENT_PER_LESSON="TCH-0002:500",
        HALL_RENT_STUDENT_RATES="TCH-0002:STU-0012:800:2026-10-27,TCH-0002:STU-0306:800",
        HOMEWORK_GRADE_SCALE="TCH-0011:10",
        SHIFT_GROUPS="GRP-0021:0-60,GRP-0022:0-120",
        SALARY_DURATION_GROUPS="GRP-0022:90",
        STUDENT_LESSON_RATES="TCH-0005:STU-0019:3500:2026-09",
        PAIR_LESSON_PRICES="TCH-0002:STU-0001:1000:STU-0002:1600:2026-11-01:800",
        STUDENT_LESSON_PRICES="TCH-0002:STU-0001:1000:2026-11-01",
        STUDENT_DISCOUNTS="STU-0191:10:2026-10",
        GROUP_SALARY_RATES="GRP-0019:1500,GRP-0021:1050:2026-10",
        GROUP_HEADCOUNT_RATES="GRP-0007:12:1500:2026-11",
        SUBSCRIPTION_TWICE_PRICES="GRP-0010:5000",
        REVENUE_SHARE_GROUPS="GRP-0020:50",
        FULL_BILL_GROUPS="TCH-0003:GRP-0004",
        SUBSCRIPTION_EDIT_GROUPS="TCH-0002:GRP-0025",
    )
    assert cfg.validate_dsl() == []


def test_validate_dsl_reports_malformed_entries():
    cfg = _cfg(
        STUDENT_LESSON_RATES="TCH-0005:STU-0019:abc:2026-13",
        PAIR_LESSON_PRICES="TCH-0002:STU-0001:500:STU-0002:500:2026-02-30:1200",
        STUDENT_LESSON_PRICES="TCH-0002:STU-0001:1000:not-a-date",
        STUDENT_DISCOUNTS="STU-0001:120",
        SHIFT_GROUPS="GRP-0021:120-60",
        MERGEABLE_GROUPS="GRP-0001",
        HOMEWORK_GRADE_SCALE="TCH-0001:1",
        GROUP_HEADCOUNT_RATES="GRP-0007:0:1500",
    )
    errors = cfg.validate_dsl()
    joined = "\n".join(errors)
    for var in (
        "STUDENT_LESSON_RATES",
        "PAIR_LESSON_PRICES",
        "STUDENT_LESSON_PRICES",
        "STUDENT_DISCOUNTS",
        "SHIFT_GROUPS",
        "MERGEABLE_GROUPS",
        "HOMEWORK_GRADE_SCALE",
        "GROUP_HEADCOUNT_RATES",
    ):
        assert var in joined


def test_health_and_health_status_endpoints():
    app = web.Application()
    dp = SimpleNamespace(workflow_data={"lesson_repo": SimpleNamespace(_sheet_name="lessons")})
    main_mod._register_health(app, dp)  # type: ignore[arg-type]

    async def run():
        async with TestClient(TestServer(app)) as c:
            r1 = await c.get("/health")
            r2 = await c.get("/health/status")
            return r1.status, await r1.text(), r2.status, await r2.json()

    s1, t1, s2, j2 = asyncio.run(run())
    assert (s1, t1) == (200, "ok")
    assert s2 == 200 and j2["status"] == "ok" and j2["sheetsTotal"] == 1
