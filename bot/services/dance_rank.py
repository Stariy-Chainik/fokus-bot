"""Классы ФТСАРР, спортивный разряд и номер книжки ученика бальных танцев (решение владельца 08.11.2026).

У спортсмена-бальника есть цели — следующий класс, разряд, — поэтому в карточке хранится то же, что в базе ФТСАРР:
**парный** класс по стандарту и латине (`class_st`, `class_la` — в открытом поиске базы это «St / La»), **класс соло**
по стандарту и латине (`class_solo_st`, `class_solo_la` — у солисток, виден только в кабинете клуба; у Бущук Златы
парный D / D, соло C / C), спортивный разряд или звание (`sport_rank`) и номер классификационной книжки
(`ftsarr_id` — по нему потом сверка с базой). Колонки 13–18 листа `students`, `scripts/setup_dance_rank.py`.
Поля есть только у учеников бальных направлений — групп с «БТ» в названии (`bill_format.direction_of`
→ «бальные танцы»); у гимнасток и современных танцев блока нет. Ученик ушёл из бальной группы с уже
присвоенным классом — блок остаётся, чтобы его было видно и можно было стереть. Классы и разряды — только из
списков ниже, пусто — «не присвоен»: по ним потом можно фильтровать состав и считать статистику. Правят
администратор и педагог своих групп, видят спортсмен (дневник) и родитель (дневник ребёнка).
"""
from __future__ import annotations

from bot.utils.bill_format import direction_of

BALLROOM = "бальные танцы"

# Классы ФТСАРР: массовый спорт Н-2 … Н-6 (по числу танцев), затем спортивные E … M.
DANCE_CLASSES: list[str] = ["Н-2", "Н-3", "Н-4", "Н-5", "Н-6", "E", "D", "C", "B", "A", "S", "M"]
# Разряды ЕВСК по танцевальному спорту.
SPORT_RANKS: list[str] = ["3 юношеский", "2 юношеский", "1 юношеский", "3 взрослый", "2 взрослый", "1 взрослый",
                          "КМС", "МС", "МСМК", "ЗМС"]
# поле API (camelCase) → поле модели/колонка листа
CLASS_FIELDS = {"classSt": "class_st", "classLa": "class_la", "classSoloSt": "class_solo_st", "classSoloLa": "class_solo_la"}
FIELDS = {**CLASS_FIELDS, "sportRank": "sport_rank", "ftsarrId": "ftsarr_id"}

# кириллица, похожая на латиницу, — в латиницу; латинская N → H, чтобы «N-3» из ИС «Начинающие» совпало с «Н-3»
_LATIN = str.maketrans("АВСЕМНавсемнNn", "ABCEMHabcemhHh")


def is_ballroom_group(group) -> bool:
    return group is not None and direction_of(group.name or "") == BALLROOM


async def in_ballroom_group(student_id: str, student_group_repo, groups: dict) -> bool:
    """Занимается ли ученик сейчас в бальной группе (groups — {group_id: Group})."""
    gids = await student_group_repo.get_groups_for_student(student_id)
    return any(is_ballroom_group(groups.get(g)) for g in gids)


def has_profile(student) -> bool:
    return any(getattr(student, f, "") for f in FIELDS.values())


def profile(student, ballroom: bool) -> dict:
    """Блок карточки: у бальников и у тех, кому класс, разряд или номер книжки уже присвоены."""
    return {"ballroom": ballroom or has_profile(student),
            **{api: getattr(student, attr, "") for api, attr in FIELDS.items()},
            "classes": DANCE_CLASSES, "ranks": SPORT_RANKS}


def clean_class(raw) -> str | None:
    """Класс из формы: «e», «Е» (кириллица), «н-3» → «E», «E», «Н-3»; пусто — не присвоен; чужое — None."""
    return _pick(raw, DANCE_CLASSES)


def clean_rank(raw) -> str | None:
    return _pick(raw, SPORT_RANKS)


def clean_ftsarr_id(raw) -> str | None:
    """Номер книжки ФТСАРР — только цифры (в базе шестизначный); пусто — нет; иное — None."""
    value = "".join(str(raw or "").split())
    return value if value == "" or (value.isdigit() and len(value) <= 8) else None


def _pick(raw, options: list[str]) -> str | None:
    value = " ".join(str(raw or "").split())
    if not value:
        return ""
    key = _norm(value)
    return next((o for o in options if _norm(o) == key), None)


def _norm(s: str) -> str:
    return s.translate(_LATIN).upper().replace("Ё", "Е")


async def apply_update(student_repo, student, body: dict, actor: int, who: str) -> tuple[int, dict]:
    """PUT {classSt?, classLa?, classSoloSt?, classSoloLa?, sportRank?, ftsarrId?} → (статус, ответ).
    Поле, которого нет в запросе, не меняется; значение не из списка — 400 `bad_value`."""
    cleaned: dict[str, str] = {}
    for api, attr in FIELDS.items():
        if api not in body:
            continue
        clean = clean_rank if api == "sportRank" else clean_ftsarr_id if api == "ftsarrId" else clean_class
        value = clean(body.get(api))
        if value is None:
            return 400, {"error": "bad_value", "field": api,
                         "message": "Номер книжки — только цифры" if api == "ftsarrId" else "Выберите класс и разряд из списка"}
        cleaned[attr] = value
    ok = await student_repo.update_dance_profile(student.student_id, actor=actor, who=who, **cleaned)
    if not ok:
        return 404, {"error": "not_found"}
    return 200, {"ok": True, **{api: cleaned.get(attr, getattr(student, attr, "")) for api, attr in FIELDS.items()}}
