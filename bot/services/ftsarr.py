"""Открытые данные ФТСАРР (решение владельца 10.11.2026): календарь турниров и их результаты.

Календарь — страница https://lkds.fdsarr.ru/common/calendar (GET с `filter_year`, `filter_month[]`, `regions[]`):
у турнира с результатами есть ссылка `/common/calendar/competitions/{id}/results`. Сама ссылка ведёт на вход,
но результаты открыты в JSON-API сервиса https://results.fdsarr.ru: `/api/competitions/{id}/` (карточка),
`/api/competitions/{id}/schedule` (группы), `/api/competitions/{id}/groups/{gid}/results` (все пары группы:
место, клуб, город, тренеры, у каждого партнёра ФИО, класс и место внутри класса). Сертификат lkds.fdsarr.ru
истёк 28.05.2026 — туда ходим без проверки TLS. Запросы идут с паузой: сайт федерации, не наш.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

CALENDAR_URL = "https://lkds.fdsarr.ru/common/calendar"
RESULTS_API = "https://results.fdsarr.ru/api/competitions"
REQUEST_PAUSE_SEC = 0.15
TIMEOUT_SEC = 30
_RESULTS_LINK = re.compile(r"/common/calendar/competitions/(\d+)/results")


@dataclass(frozen=True)
class Athlete:
    last_name: str
    first_name: str
    patronymic: str = ""
    dance_class: str = ""
    class_position: int = 0
    class_total: int = 0
    points: float = 0.0
    athlete_id: int = 0

    @property
    def name(self) -> str:
        return f"{self.last_name} {self.first_name}".strip()


@dataclass(frozen=True)
class Competitor:
    number: int
    place: int
    club: str
    city: str
    coaches: list[str] = field(default_factory=list)
    athletes: list[Athlete] = field(default_factory=list)


@dataclass(frozen=True)
class GroupResults:
    group_id: int
    group_name: str
    group_number: int
    competitor_type: str
    competitors: list[Competitor] = field(default_factory=list)


@dataclass(frozen=True)
class Competition:
    id: int
    name: str
    city: str
    location: str
    start_date: str       # YYYY-MM-DD
    end_date: str
    is_completed: bool


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _date(v) -> str:
    return str(v or "")[:10]


def parse_calendar_ids(html: str) -> list[int]:
    """Номера турниров со ссылкой «Результаты» на странице календаря, без повторов, в порядке появления."""
    return list(dict.fromkeys(int(m) for m in _RESULTS_LINK.findall(html or "")))


def parse_competition(payload: dict) -> Competition:
    return Competition(id=_int(payload.get("id")), name=str(payload.get("name") or payload.get("title") or ""),
                       city=str(payload.get("city") or ""), location=str(payload.get("location") or ""),
                       start_date=_date(payload.get("startDate")), end_date=_date(payload.get("endDate")),
                       is_completed=bool(payload.get("isCompleted")))


def parse_schedule(payload: dict) -> list[tuple[int, str, int]]:
    """[(group_id, название группы, пар в группе)] по порядку программы турнира."""
    out = []
    for g in (payload or {}).get("groups") or []:
        out.append((_int(g.get("id")), str(g.get("name") or ""), _int(g.get("competitorsCount"))))
    return out


def parse_group_results(group_id: int, group_name: str, payload: dict) -> GroupResults:
    comps = []
    for c in (payload or {}).get("competitors") or []:
        athletes = []
        for a in c.get("athletes") or []:
            person = (a.get("athlete") or {})
            full = person.get("fullName") or {}
            pos = a.get("classPosition") or {}
            athletes.append(Athlete(
                last_name=str(full.get("lastName") or "").strip(), first_name=str(full.get("firstName") or "").strip(),
                patronymic=str(full.get("patronymic") or "").strip(), dance_class=str(a.get("class") or "").strip(),
                class_position=_int(pos.get("position")), class_total=_int(pos.get("total")),
                points=_float(a.get("points")), athlete_id=_int((person.get("link") or {}).get("athleteId")),
            ))
        comps.append(Competitor(number=_int(c.get("number")), place=_int(c.get("place")),
                                club=str(c.get("club") or "").strip(), city=str(c.get("city") or "").strip(),
                                coaches=[str(x).strip() for x in (c.get("coaches") or []) if str(x).strip()],
                                athletes=athletes))
    return GroupResults(group_id=group_id, group_name=str(payload.get("group") or group_name),
                        group_number=_int(payload.get("groupNumber")),
                        competitor_type=str(payload.get("competitorType") or ""), competitors=comps)


_ST = {"st", "standard", "ballroom", "ст", "стандарт", "европейская программа"}
_LA = {"la", "latin", "ла", "латина", "латиноамериканская программа"}


def program_of(group_name: str) -> str:
    """Программа по названию группы: «ВС. Юниоры-1, ST» → ST, «…, LA» → LA, двоеборье / Ten Dance → 10, иначе «»."""
    name = (group_name or "").strip()
    low = name.lower()
    if "двоебор" in low or "ten dance" in low or "10 танц" in low:
        return "10"
    tail = low.split(",")[-1].strip() if "," in low else ""
    words = set(low.replace(",", " ").split())
    if tail in _ST or words & {"st", "ballroom", "standard"}:
        return "ST"
    if tail in _LA or words & {"la", "latin"}:
        return "LA"
    return ""


class FtsarrClient:
    """HTTP-клиент: одна сессия aiohttp, пауза между запросами, календарь без проверки TLS."""

    def __init__(self, pause_sec: float = REQUEST_PAUSE_SEC, timeout_sec: float = TIMEOUT_SEC) -> None:
        self._pause = pause_sec
        self._timeout = timeout_sec
        self._session: Any = None          # aiohttp.ClientSession, создаётся при первом запросе

    async def _get(self, url: str, *, params=None, ssl=None, as_json: bool):
        import aiohttp
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._timeout),
                                                  headers={"User-Agent": "fokus-bot (results sync)"})
        if self._pause:
            await asyncio.sleep(self._pause)
        async with self._session.get(url, params=params, ssl=ssl) as resp:
            if resp.status == 404:
                return None
            resp.raise_for_status()
            return await resp.json(content_type=None) if as_json else await resp.text()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def calendar_ids(self, year: int, month: int, regions: list[str] | None = None) -> list[int]:
        params = [("filter_year", str(year)), ("filter_month[]", str(month))]
        params += [("regions[]", r) for r in (regions or [])]
        html = await self._get(CALENDAR_URL, params=params, ssl=False, as_json=False)   # просроченный сертификат
        ids = parse_calendar_ids(html or "")
        logger.info("ФТСАРР календарь %04d-%02d (регионы %s): турниров с результатами %d",
                    year, month, ",".join(regions or []) or "все", len(ids))
        return ids

    async def competition(self, competition_id: int) -> Competition | None:
        payload = await self._get(f"{RESULTS_API}/{competition_id}/", as_json=True)
        return parse_competition(payload) if payload else None

    async def schedule(self, competition_id: int) -> list[tuple[int, str, int]]:
        payload = await self._get(f"{RESULTS_API}/{competition_id}/schedule", as_json=True)
        return parse_schedule(payload or {})

    async def group_results(self, competition_id: int, group_id: int, group_name: str = "") -> GroupResults:
        payload = await self._get(f"{RESULTS_API}/{competition_id}/groups/{group_id}/results", as_json=True)
        return parse_group_results(group_id, group_name, payload or {})
