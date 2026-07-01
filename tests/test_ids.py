"""Характеризующие тесты для генераторов ID (bot/utils/ids.py)."""
from bot.utils.ids import (
    generate_teacher_id, generate_student_id, generate_lesson_id,
    generate_payment_id, generate_group_id, generate_client_id,
)


def test_first_id_when_empty():
    assert generate_teacher_id([]) == "TCH-0001"
    assert generate_lesson_id([]) == "LES-000001"
    assert generate_client_id([]) == "CLT-0001"


def test_next_id_takes_max_not_count():
    assert generate_teacher_id(["TCH-0001", "TCH-0003"]) == "TCH-0004"
    assert generate_student_id(["STU-0009"]) == "STU-0010"


def test_malformed_ids_ignored():
    assert generate_student_id(["STU-0009", "garbage", "STU-xx"]) == "STU-0010"


def test_only_matching_prefix_counted():
    # Чужие префиксы не влияют на нумерацию.
    assert generate_group_id(["GRP-0002", "TCH-0099", "STU-0500"]) == "GRP-0003"


def test_zero_padding_width():
    assert generate_payment_id(["PAY-000042"]) == "PAY-000043"
