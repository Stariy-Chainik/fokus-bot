"""Результаты турниров ФТСАРР наших учеников (решение владельца 10.11.2026).

Лист `competition_results` — по строке на ученика в группе турнира: место, сколько пар в группе, партнёр,
класс и место внутри класса. Строки пишет `bot/services/competition_results.py` из открытого API
results.fdsarr.ru; ключ уникальности — (турнир, группа, ученик), повторный обход ничего не дублирует.
Лист `competition_scans` — какие турниры уже обойдены (чтобы ежедневный обход не качал их заново).
Оба листа создаёт scripts/setup_competition_results.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import gspread

from bot.utils import now_str
from .base import BaseRepository

logger = logging.getLogger(__name__)

HEADER = ["result_id", "student_id", "competition_id", "competition_name", "date", "city", "group_name", "program",
          "place", "total", "partner_name", "partner_id", "class", "class_position", "class_total", "points",
          "coaches", "fetched_at"]
SCAN_HEADER = ["competition_id", "name", "date", "city", "scanned_at", "groups", "hits"]


def _int(v) -> int:
    try:
        return int(float(str(v).strip().replace(",", ".")))
    except (ValueError, TypeError):
        return 0


def _float(v) -> float:
    try:
        return float(str(v).strip().replace(",", "."))
    except (ValueError, TypeError):
        return 0.0


@dataclass(frozen=True)
class CompetitionResult:
    result_id: str            # CR-XXXXXX
    student_id: str
    competition_id: int
    competition_name: str
    date: str                 # YYYY-MM-DD — первый день турнира
    city: str
    group_name: str           # «ВС. Юниоры-1, ST»
    program: str              # ST | LA | 10 (двоеборье, десять танцев) | «» — по названию группы
    place: int
    total: int                # пар (участников) в группе
    partner_name: str = ""    # «Фамилия Имя» партнёра по базе ФТСАРР
    partner_id: str = ""      # STU-… партнёра, если он тоже в боте
    dance_class: str = ""     # класс ученика в этой группе (E, D, …)
    class_position: int = 0   # место среди пар своего класса
    class_total: int = 0
    points: float = 0.0
    coaches: str = ""         # тренеры через |
    fetched_at: str = ""

    @property
    def key(self) -> tuple:
        return (self.competition_id, self.group_name, self.student_id)

    @property
    def coach_list(self) -> list[str]:
        return [c for c in self.coaches.split("|") if c]


def _row(r: dict) -> CompetitionResult:
    return CompetitionResult(
        result_id=str(r.get("result_id") or ""), student_id=str(r.get("student_id") or ""),
        competition_id=_int(r.get("competition_id")), competition_name=str(r.get("competition_name") or ""),
        date=str(r.get("date") or "")[:10], city=str(r.get("city") or ""),
        group_name=str(r.get("group_name") or ""), program=str(r.get("program") or ""),
        place=_int(r.get("place")), total=_int(r.get("total")),
        partner_name=str(r.get("partner_name") or ""), partner_id=str(r.get("partner_id") or ""),
        dance_class=str(r.get("class") or ""), class_position=_int(r.get("class_position")),
        class_total=_int(r.get("class_total")), points=_float(r.get("points")),
        coaches=str(r.get("coaches") or ""), fetched_at=str(r.get("fetched_at") or ""),
    )


def _values(r: CompetitionResult) -> list:
    return [r.result_id, r.student_id, r.competition_id, r.competition_name, r.date, r.city, r.group_name, r.program,
            r.place, r.total, r.partner_name, r.partner_id, r.dance_class, r.class_position, r.class_total,
            f"{r.points:.2f}", r.coaches, r.fetched_at]


class CompetitionResultRepository(BaseRepository):
    """Лист competition_results (scripts/setup_competition_results.py)."""

    async def get_all(self) -> list[CompetitionResult]:
        try:
            records = await self._parsed_all(_row)
        except gspread.WorksheetNotFound:          # лист ещё не создан — турниров нет, экраны работают
            logger.warning("Лист %s не найден — результаты турниров недоступны (scripts/setup_competition_results.py)",
                           self._sheet_name)
            return []
        return [r for r in records if r.result_id]

    async def get_for_student(self, student_id: str) -> list[CompetitionResult]:
        """Результаты ученика, свежие первыми."""
        return sorted((r for r in await self.get_all() if r.student_id == student_id),
                      key=lambda r: (r.date, r.competition_id, r.group_name), reverse=True)

    async def get_by_period(self, period: str) -> list[CompetitionResult]:
        return [r for r in await self.get_all() if r.date.startswith(period)]

    async def upsert_many(self, rows: list[CompetitionResult]) -> tuple[list[CompetitionResult], int]:
        """Добавить новые строки (одним запросом) и обновить изменившиеся (место, число пар, класс).

        Возвращает (новые строки с номерами, сколько обновлено). Ключ — (турнир, группа, ученик)."""
        if not rows:
            return [], 0
        stamp = now_str()
        existing = {r.key: r for r in await self.get_all()}
        updated = 0
        for r in rows:                                   # изменившиеся — по строке (_locked_row сам берёт замок листа)
            old = existing.get(r.key)
            if old is None or (old.place, old.total, old.dance_class, old.class_position, old.class_total,
                               old.partner_id) == (r.place, r.total, r.dance_class, r.class_position,
                                                   r.class_total, r.partner_id):
                continue
            async with self._locked_row(result_id=old.result_id) as idx:
                if idx is None:
                    continue
                await self._update_cells(idx, {9: r.place, 10: r.total, 12: r.partner_id, 13: r.dance_class,
                                               14: r.class_position, 15: r.class_total, 18: stamp})
            updated += 1
        added: list[CompetitionResult] = []
        async with self._sheet_lock():                   # номера под замком листа — параллельный обход не выдаст один CR-
            existing = {r.key: r for r in await self.get_all()}
            ids = [r.result_id for r in existing.values()]
            for r in rows:
                if r.key in existing:
                    continue
                from bot.utils.ids import _next_id
                rid = _next_id("CR", 6, ids)
                ids.append(rid)
                new = CompetitionResult(**{**r.__dict__, "result_id": rid, "fetched_at": stamp})
                added.append(new)
                existing[new.key] = new
            if added:
                await self._append_rows([_values(r) for r in added])
        if added or updated:
            logger.info("Результаты турниров: добавлено %d, обновлено %d", len(added), updated)
        return added, updated


@dataclass(frozen=True)
class CompetitionScan:
    competition_id: int
    name: str
    date: str
    city: str
    scanned_at: str
    groups: int
    hits: int


class CompetitionScanRepository(BaseRepository):
    """Лист competition_scans: обойдённые турниры — ежедневный обход их пропускает."""

    async def get_all(self) -> list[CompetitionScan]:
        try:
            records = await self._all_records()
        except gspread.WorksheetNotFound:
            return []
        return [CompetitionScan(_int(r.get("competition_id")), str(r.get("name") or ""), str(r.get("date") or "")[:10],
                                str(r.get("city") or ""), str(r.get("scanned_at") or ""), _int(r.get("groups")),
                                _int(r.get("hits")))
                for r in records if r.get("competition_id")]

    async def scanned_ids(self) -> set[int]:
        return {s.competition_id for s in await self.get_all()}

    async def mark(self, competition_id: int, name: str, date: str, city: str, groups: int, hits: int) -> None:
        async with self._locked_row(competition_id=competition_id) as idx:
            if idx is not None:                      # повторный обход — обновить отметку
                await self._update_cells(idx, {5: now_str(), 6: groups, 7: hits})
                return
        await self._append_row([competition_id, name, date, city, now_str(), groups, hits])
