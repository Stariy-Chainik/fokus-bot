from bot.repositories.student_repo import _row_to_student


def _row(**over):
    base = {"student_id": "STU-0001", "name": "Иванов Иван", "partner_id": "", "group_id": "",
            "group_tier": "full", "client_id": "", "parent_tg_ids": "111|222"}
    base.update(over)
    return base


def test_athlete_tg_id_absent_or_blank_is_none():
    assert _row_to_student(_row()).athlete_tg_id is None
    assert _row_to_student(_row(athlete_tg_id="")).athlete_tg_id is None
    assert _row_to_student(_row(athlete_tg_id=0)).athlete_tg_id is None


def test_athlete_tg_id_parsed_from_int_float_and_str():
    assert _row_to_student(_row(athlete_tg_id=826576855)).athlete_tg_id == 826576855
    assert _row_to_student(_row(athlete_tg_id=826576855.0)).athlete_tg_id == 826576855
    assert _row_to_student(_row(athlete_tg_id="826576855")).athlete_tg_id == 826576855
    assert _row_to_student(_row()).parent_tg_ids == [111, 222]
