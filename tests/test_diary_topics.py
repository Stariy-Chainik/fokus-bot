from bot.utils.diary_topics import topics_for_student, BALLROOM, RHYTHMIC


def test_ballroom_by_default():
    assert topics_for_student(["БП БТ Спортивная"]) == BALLROOM
    assert topics_for_student([]) == BALLROOM


def test_rhythmic_when_any_group_is_rg():
    assert topics_for_student(["ВБ ХГ Спортивная 🤸"]) == RHYTHMIC
    assert topics_for_student(["БП БТ Спортивная", "ХГ Индивидуальные — Яковлева"]) == RHYTHMIC


def test_lists_are_copies():
    t = topics_for_student([])
    t.append("x")
    assert "x" not in BALLROOM
