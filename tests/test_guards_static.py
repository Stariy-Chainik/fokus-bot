"""Инвариант доступа: каждый callback-хендлер admin/ и teacher/ защищён ролью.

Либо фильтр роли в декораторе (AdminOnly / TeacherOnly / TeacherOrAdmin /
BillingTeacherOnly), либо проверка роли в теле (is_admin / is_teacher / actor —
там, где гард стоит не первым оператором или роль вычисляется по данным), либо
FSM-состояние в декораторе — такой шаг достижим только из уже защищённого флоу.
Плюс: в проекте нет catch-all callback-хендлеров — отклонённый фильтром callback
никуда не «проваливается».
"""
import ast
import re
from pathlib import Path

HANDLERS = Path(__file__).resolve().parent.parent / "bot" / "handlers"
# Осознанно без проверки роли (поведение сохранено с рефакторинга 3.1):
ALLOWLIST = {
    "teacher/record_lesson/entry.py:cb_cancel_lesson",   # «Отмена» мастера: только чистит состояние, меню — по роли
    "teacher/record_lesson/soloist.py:cb_ms_confirm",     # проверяет FSM-состояние в теле, роль — внутри _finalize
}
ROLE_FILTERS = {"AdminOnly", "TeacherOnly", "TeacherOrAdmin", "BillingTeacherOnly"}
BODY_PREDICATES = re.compile(r"\b(_?is_admin|_?is_teacher|_?is_teacher_or_admin|actor|can_teacher_bill)\(user\)")


def _callback_handlers():
    for path in sorted(HANDLERS.rglob("*.py")):
        rel = path.relative_to(HANDLERS).as_posix()
        src = path.read_text(encoding="utf-8")
        lines = src.split("\n")
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "callback_query":
                    filters = {a.func.id for a in d.args if isinstance(a, ast.Call) and isinstance(a.func, ast.Name)}
                    body = "\n".join(lines[node.lineno - 1:node.end_lineno])
                    yield rel, node.name, d, filters, body


def _has_state_filter(deco: ast.Call) -> bool:
    """`SomeStates.step` или StateFilter(...) в декораторе: хендлер достижим только из FSM-флоу."""
    for a in deco.args:
        if isinstance(a, ast.Attribute) and a.attr and isinstance(a.value, ast.Name) and a.value.id.endswith("States"):
            return True
        if isinstance(a, ast.Call) and isinstance(a.func, ast.Name) and a.func.id == "StateFilter":
            return True
    return False


def test_every_admin_and_teacher_callback_is_role_guarded():
    unguarded = []
    for rel, name, deco, filters, body in _callback_handlers():
        if not (rel.startswith("admin/") or rel.startswith("teacher/")):
            continue
        if filters & ROLE_FILTERS or BODY_PREDICATES.search(body) or _has_state_filter(deco):
            continue
        if f"{rel}:{name}" in ALLOWLIST:
            continue
        unguarded.append(f"{rel}:{name}")
    assert not unguarded, "Хендлеры без проверки роли:\n  " + "\n  ".join(unguarded)


def test_no_catch_all_callback_handlers():
    """Фильтр роли, вернувший False, отдаёт callback дальше — дальше должно быть пусто."""
    catch_all = [f"{rel}:{name}" for rel, name, deco, _filters, _body in _callback_handlers() if not deco.args]
    assert catch_all == [], catch_all
