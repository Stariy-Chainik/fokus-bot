"""Этап D: снимок DI-контейнера — набор роутеров, middleware и ключей dp[...] не изменился.

SheetsClient подключается лениво, поэтому Dispatcher собирается без сети.
"""
from aiogram.fsm.storage.memory import MemoryStorage

from bot.__main__ import _build_dispatcher
from bot.middlewares import AuthMiddleware, DedupUpdateMiddleware
from bot.services.parent_notifier import ParentNotifier

DI_KEYS = {
    "user_repo", "teacher_repo", "student_repo", "lesson_repo", "payment_repo", "submission_repo",
    "branch_repo", "group_repo", "teacher_group_repo", "student_group_repo", "student_request_repo",
    "client_repo", "subscription_override_repo", "finance_entry_repo", "rate_history_repo", "payout_repo",
    "salary_override_repo", "salary_service", "lesson_service", "payment_service", "profit_service",
    "diagnostics_service", "visibility", "student_service", "student_request_service",
    "cloudkassir_service", "notifier", "training_entry_repo", "athlete_task_repo", "diary_service",
}


def _middlewares(manager) -> list[type]:
    items = getattr(manager, "_middlewares", None)
    if items is None:
        items = list(manager)
    return [type(m) for m in items]


def test_dispatcher_wiring_snapshot():
    previous = ParentNotifier.default
    try:
        dp = _build_dispatcher(MemoryStorage())
    finally:
        ParentNotifier.default = previous   # сборка ставит notifier процесса — не течёт в другие тесты
    assert set(dp.workflow_data) >= DI_KEYS
    assert set(dp.workflow_data) - DI_KEYS == set(), set(dp.workflow_data) - DI_KEYS
    assert [r.name for r in dp.sub_routers] == ["common", "admin", "teacher", "athlete", "client"]
    by_name = {r.name: r for r in dp.sub_routers}
    assert len(by_name["admin"].sub_routers) == 13
    assert len(by_name["teacher"].sub_routers) == 8
    assert len(by_name["client"].sub_routers) == 7
    # aiogram добавляет свои служебные middleware; наши — в нужных менеджерах и в нужном порядке
    outer, inner = _middlewares(dp.update.outer_middleware), _middlewares(dp.update.middleware)
    assert DedupUpdateMiddleware in outer and AuthMiddleware not in outer
    assert inner == [AuthMiddleware]
