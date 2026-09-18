"""Синхронизация ХГ Яковлевой: сопоставление блока внешней таблицы с группой бота."""
from scripts.sync_yakovleva_attendance import GROUPS, _key, match_block, parse_blocks

SAD, NACH, BP = GROUPS["sad"], GROUPS["nach"], GROUPS["bp"]
ROSTERS = {SAD[0]: {_key("Титова Арина"), _key("Габбасова Юлия")}, NACH[0]: {_key("Махмасталь Василиса")}}


def test_title_wins_over_roster():
    assert match_block("художественная гимнастика южная битца (сад) вторник", ["Кто-то Новый"], ROSTERS) == SAD
    assert match_block("   художественная гимнастика бутово парк (школа) ", ["Титова Арина"], ROSTERS) == BP


def test_untitled_block_matches_by_roster_overlap():
    assert match_block("     17.00-18.00; 10.00-11.00", ["Махмасталь Василиса", "Новая Девочка"], ROSTERS) == NACH


def test_unknown_block_without_overlap_is_not_guessed():
    assert match_block("   новая площадка (школа) ", ["Гюльвердиева Амелия", "Бунова Милана"], ROSTERS) is None
    assert match_block("", [], ROSTERS) is None


def test_parse_blocks_reads_roster_and_marks():
    rows = [
        ["ХУДОЖЕСТВЕННАЯ ГИМНАСТИКА БУТОВО ПАРК (школа)", ""],
        ["№", "ФИО", "16.09", "18.09"],
        ["1", "Гюльвердиева Амелия", "П", ""],
        ["2", "Бунова Милана", "", ""],
        ["3", "Гуськова Полина 26", "П", "1"],
        ["", "", "", ""],
    ]
    [(titles, roster, marks)] = list(parse_blocks(rows, "2026-09"))
    assert "бутово парк" in titles
    assert roster == ["Гюльвердиева Амелия", "Бунова Милана", "Гуськова Полина"]
    assert marks == {16: [("Гюльвердиева Амелия", "П"), ("Гуськова Полина", "П")], 18: [("Гуськова Полина", "1")]}
