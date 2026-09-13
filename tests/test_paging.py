"""bot/utils/paging + keyboards/common.nav_row."""
from bot.keyboards.common import nav_row
from bot.utils.paging import Page, paginate


def test_paginate_without_clamp_keeps_old_slicing_semantics():
    pg = paginate(list(range(45)), 0, 20)
    assert (pg.items, pg.page, pg.pages, pg.total, pg.has_prev, pg.has_next, pg.offset) == (
        list(range(20)), 0, 3, 45, False, True, 0)
    pg = paginate(list(range(45)), 2, 20)
    assert (pg.items, pg.has_prev, pg.has_next, pg.offset) == (list(range(40, 45)), True, False, 40)
    # страница за пределами — пустой срез, как раньше у списков учеников и занятий
    pg = paginate(list(range(45)), 7, 20)
    assert (pg.items, pg.page, pg.pages, pg.has_prev, pg.has_next) == ([], 7, 3, True, False)
    assert paginate([], 0, 20) == Page([], 0, 1, 0, 20)


def test_paginate_with_clamp_pins_to_last_page():
    pg = paginate(list(range(30)), 9, 25, clamp=True)
    assert (pg.items, pg.page, pg.pages) == (list(range(25, 30)), 1, 2)
    pg = paginate(list(range(30)), -3, 25, clamp=True)
    assert (pg.page, pg.items) == (0, list(range(25)))
    assert paginate([], 5, 25, clamp=True) == Page([], 0, 1, 0, 25)


def _cells(row):
    return [(b.text, b.callback_data) for b in row]


def test_nav_row_variants():
    mid = paginate(list(range(60)), 1, 25, clamp=True)
    assert _cells(nav_row(mid, lambda n: f"spage:{n}")) == [("← Пред.", "spage:0"), ("След. →", "spage:2")]
    assert _cells(nav_row(mid, lambda n: f"debtors:p:{n}", prev_text="«", next_text="»", counter=True)) == [
        ("«", "debtors:p:0"), ("2/3", "noop"), ("»", "debtors:p:2")]
    first = paginate(list(range(60)), 0, 25)
    assert _cells(nav_row(first, lambda n: f"x:{n}")) == [("След. →", "x:1")]
    last = paginate(list(range(60)), 2, 25)
    assert _cells(nav_row(last, lambda n: f"x:{n}")) == [("← Пред.", "x:1")]
    assert nav_row(paginate([1, 2], 0, 25), lambda n: f"x:{n}") == []
