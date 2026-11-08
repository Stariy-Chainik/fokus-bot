"""Танцевальный класс и спортивный разряд ученика бальных танцев (решение владельца 08.11.2026).

У спортсмена-бальника есть цели — следующий класс, разряд, — поэтому в карточке хранятся текущий
танцевальный класс (Н-2 … M) и спортивный разряд (юношеские и взрослые, КМС, МС, МСМК, ЗМС):
`students.dance_class` (кол. 13) и `students.sport_rank` (кол. 14), `scripts/setup_dance_rank.py`.
Поля есть только у учеников бальных направлений — групп с «БТ» в названии (`bill_format.direction_of`
→ «бальные танцы»); у гимнасток и современных танцев блока нет. Ученик ушёл из бальной группы с уже
присвоенным классом — блок остаётся, чтобы его было видно и можно было стереть. Значения — только из списков
ниже, пусто — «не присвоен»: по ним потом можно фильтровать состав и считать статистику. Правят администратор
и педагог своих групп, видят спортсмен (дневник) и родитель (дневник ребёнка).
"""
from __future__ import annotations

from bot.utils.bill_format import direction_of

BALLROOM = "бальные танцы"

# Классы ФТСАРР: массовый спорт Н-2 … Н-6 (по числу танцев), затем спортивные E … M.
DANCE_CLASSES: list[str] = ["Н-2", "Н-3", "Н-4", "Н-5", "Н-6", "E", "D", "C", "B", "A", "S", "M"]
# Разряды ЕВСК по танцевальному спорту.
SPORT_RANKS: list[str] = ["3 юношеский", "2 юношеский", "1 юношеский", "3 взрослый", "2 взрослый", "1 взрослый",
                          "КМС", "МС", "МСМК", "ЗМС"]

_LATIN = str.maketrans("АВСЕМНавсемн", "ABCEMHabcemh")   # кириллица, похожая на латиницу, — в латиницу


def is_ballroom_group(group) -> bool:
    return group is not None and direction_of(group.name or "") == BALLROOM


async def in_ballroom_group(student_id: str, student_group_repo, groups: dict) -> bool:
    """Занимается ли ученик сейчас в бальной группе (groups — {group_id: Group})."""
    gids = await student_group_repo.get_groups_for_student(student_id)
    return any(is_ballroom_group(groups.get(g)) for g in gids)


def has_profile(student) -> bool:
    return bool(student.dance_class or student.sport_rank)


def profile(student, ballroom: bool) -> dict:
    """Блок карточки: у бальников и у тех, кому класс или разряд уже присвоен."""
    return {"ballroom": ballroom or has_profile(student), "danceClass": student.dance_class,
            "sportRank": student.sport_rank, "classes": DANCE_CLASSES, "ranks": SPORT_RANKS}


def clean_class(raw) -> str | None:
    """Класс из формы: «e», «Е» (кириллица), «н-3» → «E», «E», «Н-3»; пусто — не присвоен; чужое — None."""
    return _pick(raw, DANCE_CLASSES)


def clean_rank(raw) -> str | None:
    return _pick(raw, SPORT_RANKS)


def _pick(raw, options: list[str]) -> str | None:
    value = " ".join(str(raw or "").split())
    if not value:
        return ""
    key = _norm(value)
    return next((o for o in options if _norm(o) == key), None)


def _norm(s: str) -> str:
    return s.translate(_LATIN).upper().replace("Ё", "Е")


async def apply_update(student_repo, student, body: dict, actor: int, who: str) -> tuple[int, dict]:
    """PUT {danceClass?, sportRank?} → (статус, ответ). Поле, которого нет в запросе, не меняется."""
    dance_class = clean_class(body.get("danceClass")) if "danceClass" in body else None
    sport_rank = clean_rank(body.get("sportRank")) if "sportRank" in body else None
    if ("danceClass" in body and dance_class is None) or ("sportRank" in body and sport_rank is None):
        return 400, {"error": "bad_value", "message": "Выберите класс и разряд из списка"}
    ok = await student_repo.update_dance_profile(student.student_id, dance_class=dance_class, sport_rank=sport_rank,
                                                 actor=actor, who=who)
    if not ok:
        return 404, {"error": "not_found"}
    return 200, {"ok": True, "danceClass": student.dance_class if dance_class is None else dance_class,
                 "sportRank": student.sport_rank if sport_rank is None else sport_rank}
