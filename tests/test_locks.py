"""Тесты для InProgressGuard — единого локера от двойного клика."""
from bot.utils.locks import InProgressGuard


def test_set_compatible_api():
    g = InProgressGuard()
    assert "k" not in g
    g.add("k")
    assert "k" in g
    g.discard("k")
    assert "k" not in g


def test_discard_missing_key_is_noop():
    g = InProgressGuard()
    g.discard("absent")  # не бросает
    assert "absent" not in g


def test_add_is_idempotent():
    g = InProgressGuard()
    g.add("k")
    g.add("k")
    g.discard("k")
    assert "k" not in g


def test_hold_context_manager_releases():
    g = InProgressGuard()
    with g.hold("k"):
        assert "k" in g
    assert "k" not in g


def test_hold_releases_on_exception():
    g = InProgressGuard()
    try:
        with g.hold("k"):
            assert "k" in g
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert "k" not in g
