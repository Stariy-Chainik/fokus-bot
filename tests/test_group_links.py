"""Токены ссылок-приглашений в группу (bot/utils/group_links.py)."""
from bot.utils.group_links import (
    build_start_payload, group_link_token, parse_start_payload,
)

SECRET = "test-secret"


def test_payload_roundtrip():
    payload = build_start_payload("GRP-0017", SECRET)
    assert parse_start_payload(payload, SECRET) == "GRP-0017"


def test_payload_format_telegram_safe():
    # Telegram допускает в start-параметре только [A-Za-z0-9_-], до 64 символов.
    payload = build_start_payload("GRP-0017", SECRET)
    assert len(payload) <= 64
    assert all(c.isalnum() or c in "_-" for c in payload)


def test_token_is_stable():
    assert group_link_token("GRP-0017", SECRET) == group_link_token("GRP-0017", SECRET)


def test_token_differs_per_group():
    assert group_link_token("GRP-0017", SECRET) != group_link_token("GRP-0018", SECRET)


def test_token_differs_per_secret():
    # Смена секрета отзывает все старые ссылки.
    assert group_link_token("GRP-0017", "a") != group_link_token("GRP-0017", "b")


def test_tampered_group_id_rejected():
    payload = build_start_payload("GRP-0017", SECRET)
    tampered = payload.replace("GRP-0017", "GRP-0018")
    assert parse_start_payload(tampered, SECRET) is None


def test_wrong_token_rejected():
    assert parse_start_payload("g_GRP-0017_000000", SECRET) is None


def test_garbage_rejected():
    for bad in ("", "g_", "x_GRP-0017_abc123", "g_GRP-0017", "hello", "g__abc123"):
        assert parse_start_payload(bad, SECRET) is None


def test_token_case_insensitive():
    # Пользователь мог получить ссылку с токеном в верхнем регистре (пересланную/набранную).
    payload = build_start_payload("GRP-0017", SECRET)
    prefix, gid, token = payload.split("_")
    assert parse_start_payload(f"{prefix}_{gid}_{token.upper()}", SECRET) == "GRP-0017"


def test_athlete_payload_roundtrip_and_isolated_from_group_tokens():
    from bot.utils.group_links import build_athlete_payload, parse_athlete_payload
    payload = build_athlete_payload("STU-0193", SECRET)
    assert payload.startswith("a_STU-0193_") and len(payload) <= 64
    assert parse_athlete_payload(payload, SECRET) == "STU-0193"
    assert parse_athlete_payload(payload, "other") is None
    assert parse_start_payload(payload, SECRET) is None          # не групповая ссылка
    assert parse_athlete_payload(build_start_payload("GRP-0017", SECRET), SECRET) is None
