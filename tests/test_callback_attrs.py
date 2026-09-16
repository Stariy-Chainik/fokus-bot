"""Статический инвариант: у `cb = XCb.unpack(...)` в хендлерах читаются только поля датакласса XCb.

Ошибка в имени поля (`cb.period_month` вместо `cb.period`) не ловится ни mypy (unpack
возвращает Self), ни roundtrip-тестами строк — только этим сканом или падением на проде.
"""
from __future__ import annotations

import ast
import dataclasses
import pathlib

import bot.utils.callbacks as cbmod

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLASSES = {n: c for n, c in vars(cbmod).items() if isinstance(c, type) and dataclasses.is_dataclass(c)}


def _allowed(cls) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)} | {k for k in dir(cls) if not k.startswith("_")}


def _is_unpack(call: ast.AST) -> str | None:
    if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "unpack"
            and isinstance(call.func.value, ast.Name) and call.func.value.id in CLASSES):
        return call.func.value.id
    return None


def _problems() -> list[str]:
    out: list[str] = []
    for path in sorted((ROOT / "bot").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            var_cls: dict[str, type] = {}
            for node in ast.walk(func):
                if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                        and (name := _is_unpack(node.value))):
                    var_cls[node.targets[0].id] = CLASSES[name]
            for node in ast.walk(func):
                if not isinstance(node, ast.Attribute):
                    continue
                cls = None
                if isinstance(node.value, ast.Name) and node.value.id in var_cls:
                    cls = var_cls[node.value.id]
                elif (name := _is_unpack(node.value)):
                    cls = CLASSES[name]
                if cls is not None and node.attr not in _allowed(cls):
                    out.append(f"{path.relative_to(ROOT)}:{node.lineno}: .{node.attr} — нет у {cls.__name__}"
                               f" (поля: {[f.name for f in dataclasses.fields(cls)]})")
    return out


def test_handlers_read_only_existing_callback_fields():
    assert _problems() == []
