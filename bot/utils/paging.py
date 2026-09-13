"""Пагинация списков — одно место для «страница N из M».

`paginate()` режет список и считает границы; кнопки навигации строит
`bot.keyboards.common.nav_row` по объекту `Page`. Семантика сохранена по
существующим экранам: без `clamp` страница за пределами даёт пустой срез
(список учеников, занятия), с `clamp` — прижимается к последней (должники).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Page:
    items: list
    page: int      # 0-based
    pages: int     # всегда >= 1
    total: int
    size: int

    @property
    def has_prev(self) -> bool:
        return self.page > 0

    @property
    def has_next(self) -> bool:
        return (self.page + 1) * self.size < self.total

    @property
    def offset(self) -> int:
        """Индекс первого элемента страницы в полном списке (для нумерации строк)."""
        return self.page * self.size


def paginate(items: Sequence, page: int, size: int, *, clamp: bool = False) -> Page:
    total = len(items)
    pages = max(1, -(-total // size))
    if clamp:
        page = max(0, min(page, pages - 1))
    start = page * size
    return Page(list(items[start:start + size]), page, pages, total, size)
