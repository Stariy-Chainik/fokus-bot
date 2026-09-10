from bot.repositories.client_repo import _row_to_client
from bot.repositories.student_repo import _row_to_student


def test_client_max_id_parsed():
    row = {"client_id": "CLT-1", "name": "Мама", "tg_id": "1", "created_at": "", "phone": "", "email": ""}
    assert _row_to_client(row).max_id is None
    row["max_id"] = 4242.0
    assert _row_to_client(row).max_id == 4242


def test_student_parent_max_ids_parsed():
    base = {"student_id": "STU-1", "name": "Иванов", "parent_tg_ids": "1|2"}
    assert _row_to_student(base).parent_max_ids == []
    assert _row_to_student({**base, "parent_max_ids": "7|8"}).parent_max_ids == [7, 8]
    assert _row_to_student({**base, "parent_max_ids": "7|8"}).parent_addrs == [("tg", 1), ("tg", 2), ("max", 7), ("max", 8)]
