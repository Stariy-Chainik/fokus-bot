"""Общие UI-константы.

PAGE_SIZE — размер страницы списков занятий (my_lessons, edit_lesson),
STUDENT_PAGE_SIZE — поискового списка учеников, DEBTORS_PAGE_SIZE — экрана «Должники».
Резка списков и навигация — bot/utils/paging.py + bot/keyboards/common.nav_row.
"""

PAGE_SIZE = 20
STUDENT_PAGE_SIZE = 20
DEBTORS_PAGE_SIZE = 25

# Единица нормирования тарифов: ставки заданы «за 45 минут».
# earned = ставка × (duration_min / MINUTES_PER_UNIT). См. billing_service.
MINUTES_PER_UNIT = 45
