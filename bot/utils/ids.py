"""
Генераторы ID в читаемом формате.
Каждый генератор принимает уже существующий максимальный номер (int)
и возвращает следующий ID в виде строки.
"""


def _next_id(prefix: str, width: int, existing_ids: list[str]) -> str:
    """Находит максимальный номер среди existing_ids и возвращает следующий."""
    max_num = 0
    for eid in existing_ids:
        if eid.startswith(prefix + "-"):
            try:
                num = int(eid[len(prefix) + 1:])
                if num > max_num:
                    max_num = num
            except ValueError:
                pass
    return f"{prefix}-{str(max_num + 1).zfill(width)}"


def generate_teacher_id(existing: list[str]) -> str:
    return _next_id("TCH", 4, existing)


def generate_student_id(existing: list[str]) -> str:
    return _next_id("STU", 4, existing)


def generate_lesson_id(existing: list[str]) -> str:
    return _next_id("LES", 6, existing)


def generate_billing_id(existing: list[str]) -> str:
    return _next_id("BIL", 6, existing)


def generate_payment_id(existing: list[str]) -> str:
    return _next_id("PAY", 6, existing)
