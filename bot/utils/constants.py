"""Общие UI-константы.

PAGE_SIZE — размер страницы для списков занятий (my_lessons, edit_lesson).
Список учеников использует собственный `_STUDENT_PAGE_SIZE` в
bot/keyboards/admin.py — это отдельный концерн, намеренно не объединён.
"""

PAGE_SIZE = 20

# Единица нормирования тарифов: ставки заданы «за 45 минут».
# earned = ставка × (duration_min / MINUTES_PER_UNIT). См. billing_service.
MINUTES_PER_UNIT = 45
