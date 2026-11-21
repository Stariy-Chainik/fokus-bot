"""Состояние фоновых задач процесса для `/health/status`."""
from __future__ import annotations

import time

_STATE: dict[str, dict[str, object]] = {}


def mark_ok(name: str) -> None:
    st = _STATE.setdefault(name, {})
    st["lastOkAt"] = round(time.time(), 1)
    st["consecutiveErrors"] = 0


def mark_err(name: str, exc: BaseException) -> None:
    st = _STATE.setdefault(name, {})
    st["lastError"] = f"{type(exc).__name__}: {exc}"[:200]
    st["lastErrorAt"] = round(time.time(), 1)
    st["consecutiveErrors"] = int(st.get("consecutiveErrors") or 0) + 1  # type: ignore[call-overload]


def snapshot() -> dict[str, dict[str, object]]:
    return {k: dict(v) for k, v in _STATE.items()}


def clear() -> None:
    _STATE.clear()
