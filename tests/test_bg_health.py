"""Тесты автоалертов фоновых задач (`bg_health`) и ночного бэкапа Google Sheets (`backup_sheets`)."""
from __future__ import annotations

import asyncio
import gzip
import json
import os
from dataclasses import dataclass
from pathlib import Path

from bot.services import bg_health
from scripts.backup_sheets import export_spreadsheet, write_backup


@dataclass
class _Admin:
    tg_id: int


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


class _FakeUsers:
    def __init__(self, admins: list[int], *, fail: bool = False) -> None:
        self.admins = admins
        self.fail = fail

    async def get_admins(self) -> list[_Admin]:
        if self.fail:
            raise RuntimeError("Sheets down")
        return [_Admin(tg_id=x) for x in self.admins]


async def _drain() -> None:
    while bg_health._BG_TASKS:
        await asyncio.gather(*list(bg_health._BG_TASKS))


def test_bg_health_alerts_on_threshold_and_recovery() -> None:
    async def run() -> None:
        bg_health.clear()
        bot = _FakeBot()
        users = _FakeUsers([101, 202])
        bg_health.setup(bot, users, threshold=3, cooldown_sec=3600)
        await _drain()

        # 1-я и 2-я ошибки подряд — тишина (единичный сетевой всплеск)
        bg_health.mark_err("cache_warmer", RuntimeError("timeout 1"))
        bg_health.mark_err("cache_warmer", RuntimeError("timeout 2"))
        await _drain()
        assert bot.sent == []

        # 3-я ошибка — алерт обоим админам
        bg_health.mark_err("cache_warmer", RuntimeError("timeout 3"))
        await _drain()
        assert len(bot.sent) == 2
        assert {cid for cid, _ in bot.sent} == {101, 202}
        assert "Обновление кэша Google Sheets" in bot.sent[0][1]
        assert "3 ошибок подряд" in bot.sent[0][1]

        # 4-я ошибка в пределах cooldown — без спама
        bot.sent.clear()
        bg_health.mark_err("cache_warmer", RuntimeError("timeout 4"))
        await _drain()
        assert bot.sent == []

        # Восстановление — сообщение «восстановилась»
        bg_health.mark_ok("cache_warmer")
        await _drain()
        assert len(bot.sent) == 2
        assert "восстановилась" in bot.sent[0][1]
        assert "после 4 ошибок" in bot.sent[0][1]

        # Повторный mark_ok — тишина
        bot.sent.clear()
        bg_health.mark_ok("cache_warmer")
        await _drain()
        assert bot.sent == []
        bg_health.clear()

    asyncio.run(run())


def test_bg_health_uses_cached_admins_when_sheets_down() -> None:
    async def run() -> None:
        bg_health.clear()
        bot = _FakeBot()
        users = _FakeUsers([777])
        bg_health.setup(bot, users, threshold=1, cooldown_sec=60)
        await _drain()

        # Google Sheets упал — get_admins() бросает ошибку, но кэш админов работает
        users.fail = True
        bg_health.mark_err("lesson_reminders", ConnectionError("503 Service Unavailable"))
        await _drain()
        assert len(bot.sent) == 1
        assert bot.sent[0][0] == 777
        assert "Напоминания о занятиях" in bot.sent[0][1]
        bg_health.clear()

    asyncio.run(run())


def test_backup_sheets_batch_export_and_rotation(tmp_path: Path) -> None:
    class _Ws:
        def __init__(self, title: str, rows: list[list[str]]) -> None:
            self.title = title
            self._rows = rows

        def get_all_values(self) -> list[list[str]]:
            return self._rows

    class _Spreadsheet:
        id = "SPREAD-1"

        def worksheets(self) -> list[_Ws]:
            return [_Ws("students", [["id", "name"], ["STU-1", "Иванов"]]), _Ws("O'Brien", [])]

        def values_batch_get(self, ranges: list[str]) -> dict[str, object]:
            assert ranges == ["'students'", "'O''Brien'"]
            return {
                "valueRanges": [
                    {"values": [["id", "name"], ["STU-1", "Иванов"]]},
                    {},  # пустой лист без ключа values
                ]
            }

    payload = export_spreadsheet(_Spreadsheet())
    assert payload["spreadsheetId"] == "SPREAD-1"
    assert payload["sheetCount"] == 2
    assert payload["totalRows"] == 2
    assert payload["sheets"]["students"] == [["id", "name"], ["STU-1", "Иванов"]]
    assert payload["sheets"]["O'Brien"] == []

    # Записываем 3 архива с keep=2 — старейший должен удалиться
    p1 = write_backup(payload, tmp_path, keep=2, stamp="20261101-033000")
    os.utime(p1, (1000, 1000))
    p2 = write_backup(payload, tmp_path, keep=2, stamp="20261102-033000")
    os.utime(p2, (2000, 2000))
    p3 = write_backup(payload, tmp_path, keep=2, stamp="20261103-033000")

    assert not p1.exists()
    assert p2.exists() and p3.exists()
    with gzip.open(p3, "rb") as fh:
        restored = json.loads(fh.read().decode("utf-8"))
    assert restored["sheets"]["students"][1][1] == "Иванов"
