"""Характеризующие тесты для парсера/сериализатора attendees.

Фиксируют ТЕКУЩЕЕ поведение bot/utils/attendees.py — эталон для рефакторинга.
"""
from bot.models import Group
from bot.models.enums import GroupBillingMode
from bot.utils.attendees import (
    AttendeeEntry, parse_attendees, serialize_attendees, attendee_ids,
    build_group_attendees_csv,
)


def test_parse_empty_returns_empty_list():
    assert parse_attendees("") == []
    assert parse_attendees(None) == []


def test_parse_old_format_no_amount():
    # Старый формат (только id) → длительность = default, amount = 0.
    out = parse_attendees("STU-0001,STU-0002", default_duration=45)
    assert out == [
        AttendeeEntry("STU-0001", 45, 0),
        AttendeeEntry("STU-0002", 45, 0),
    ]


def test_parse_extended_format():
    out = parse_attendees("STU-0001:60:850,STU-0002:35:500")
    assert out == [
        AttendeeEntry("STU-0001", 60, 850),
        AttendeeEntry("STU-0002", 35, 500),
    ]


def test_parse_strips_spaces_and_skips_empty_tokens():
    out = parse_attendees("  STU-1:60:700 , , STU-2:60:700 ,")
    assert out == [
        AttendeeEntry("STU-1", 60, 700),
        AttendeeEntry("STU-2", 60, 700),
    ]


def test_parse_malformed_duration_and_amount_fall_back():
    # Нечисловые duration/amount → default_duration и 0 соответственно.
    out = parse_attendees("STU-1:bad:xyz", default_duration=60)
    assert out == [AttendeeEntry("STU-1", 60, 0)]


def test_parse_empty_duration_and_amount_slots():
    # "STU-1::" → пустые слоты → default_duration и 0.
    out = parse_attendees("STU-1::", default_duration=90)
    assert out == [AttendeeEntry("STU-1", 90, 0)]


def test_parse_token_without_id_is_skipped():
    out = parse_attendees(":60:700,STU-2:60:700")
    assert out == [AttendeeEntry("STU-2", 60, 700)]


def test_serialize_always_extended_format():
    entries = [AttendeeEntry("STU-1", 60, 700), AttendeeEntry("STU-2", 35, 500)]
    assert serialize_attendees(entries) == "STU-1:60:700,STU-2:35:500"


def test_roundtrip_old_format_normalizes_to_extended():
    parsed = parse_attendees("STU-1,STU-2", default_duration=45)
    assert serialize_attendees(parsed) == "STU-1:45:0,STU-2:45:0"


def test_attendee_ids_returns_only_ids():
    assert attendee_ids("STU-1:60:700,STU-2:35:500") == ["STU-1", "STU-2"]
    assert attendee_ids("STU-1,STU-2") == ["STU-1", "STU-2"]
    assert attendee_ids(None) == []


# ─── build_group_attendees_csv (снапшот на момент записи занятия) ────────────

def _per_visit_group(**over):
    base = dict(
        group_id="GRP-0017", branch_id="BRN-001", name="БП Спортивная",
        billing_mode=GroupBillingMode.PER_VISIT,
        price_short=500, duration_short=35, price_full=700, duration_full=60,
    )
    base.update(over)
    return Group(**base)


def test_build_per_visit_snapshot_full_and_short():
    csv = build_group_attendees_csv(
        _per_visit_group(),
        ["STU-1", "STU-2"],
        {"STU-2": "short"},  # STU-1 без записи в tiers → default FULL
    )
    assert csv == "STU-1:60:700,STU-2:35:500"


def test_build_none_mode_group_writes_old_csv():
    group = _per_visit_group(billing_mode=GroupBillingMode.NONE)
    assert build_group_attendees_csv(group, ["STU-1", "STU-2"], {}) == "STU-1,STU-2"


def test_build_missing_group_writes_old_csv():
    # Группа не найдена (битая ссылка) — старый формат, как и раньше.
    assert build_group_attendees_csv(None, ["STU-1"], {}) == "STU-1"


def test_build_empty_attendees_returns_none():
    assert build_group_attendees_csv(_per_visit_group(), [], {}) is None
    assert build_group_attendees_csv(None, [], {}) is None


def test_build_unknown_tier_value_falls_back_to_full():
    csv = build_group_attendees_csv(_per_visit_group(), ["STU-1"], {"STU-1": "garbage"})
    assert csv == "STU-1:60:700"
