"""Дневник спортсмена: статистика и рейтинг (чистые функции)."""
from bot.models import TrainingEntry
from bot.services.diary_service import (
    compute_stats, compute_leaderboard, entry_points, place_icon, UNGRADED_GRADE,
)


def _e(sid="STU-1", date="2026-09-05", minutes=60, topics=None, grade=None, eid="TE-000001"):
    return TrainingEntry(
        entry_id=eid, student_id=sid, date=date, minutes=minutes,
        topics=topics if topics is not None else ["Самба"], grade=grade,
    )


def test_entry_points_minutes_times_grade():
    assert entry_points(_e(minutes=60, grade=5)) == 300
    assert entry_points(_e(minutes=60, grade=1)) == 60
    assert entry_points(_e(minutes=60, grade=None)) == 60 * UNGRADED_GRADE


def test_entry_points_topic_share_splits_minutes():
    e = _e(minutes=60, topics=["Самба", "Румба"], grade=4)
    assert entry_points(e, "Самба") == 120       # 30 мин × 4
    assert entry_points(e, "Танго") == 0
    assert entry_points(e) == 240


def test_compute_stats():
    st = compute_stats([
        _e(minutes=60, topics=["Самба", "Румба"], grade=4),
        _e(minutes=30, topics=["Самба"], grade=None, eid="TE-000002"),
    ])
    assert st.sessions == 2
    assert st.total_minutes == 90
    assert st.by_topic == {"Самба": 60, "Румба": 30}
    assert st.graded == 1
    assert st.avg_grade == 4.0
    assert st.points == 240 + 90


def test_compute_stats_empty():
    st = compute_stats([])
    assert st.sessions == 0 and st.avg_grade is None and st.by_topic == {}


def test_leaderboard_quality_beats_quantity():
    names = {"STU-1": "Иванов", "STU-2": "Петров", "STU-3": "Сидоров"}
    entries = [
        _e("STU-1", minutes=120, grade=2),   # 240
        _e("STU-2", minutes=60, grade=5),    # 300
    ]
    rows = compute_leaderboard(entries, names)
    assert [(r.name, r.points, r.place) for r in rows] == [
        ("Петров", 300, 1), ("Иванов", 240, 2), ("Сидоров", 0, 3),
    ]
    assert rows[0].minutes == 60 and rows[0].sessions == 1 and rows[0].avg_grade == 5.0


def test_leaderboard_dense_places_for_ties():
    names = {"STU-1": "Б", "STU-2": "А", "STU-3": "В"}
    entries = [_e("STU-1", minutes=60, grade=3), _e("STU-2", minutes=60, grade=3),
               _e("STU-3", minutes=30, grade=3)]
    rows = compute_leaderboard(entries, names)
    assert [(r.name, r.place) for r in rows] == [("А", 1), ("Б", 1), ("В", 2)]


def test_leaderboard_by_topic():
    names = {"STU-1": "Иванов", "STU-2": "Петров"}
    entries = [
        _e("STU-1", minutes=60, topics=["Самба", "Румба"], grade=5),  # Самба: 30×5=150
        _e("STU-2", minutes=40, topics=["Самба"], grade=5),           # Самба: 200
    ]
    rows = compute_leaderboard(entries, names, topic="Самба")
    assert [(r.name, r.points, r.minutes) for r in rows] == [("Петров", 200, 40), ("Иванов", 150, 30)]
    rows = compute_leaderboard(entries, names, topic="Румба")
    assert [(r.name, r.points, r.sessions) for r in rows] == [("Иванов", 150, 1), ("Петров", 0, 0)]


def test_leaderboard_ignores_unknown_students():
    rows = compute_leaderboard([_e("STU-9", grade=5)], {"STU-1": "Иванов"})
    assert [(r.name, r.points) for r in rows] == [("Иванов", 0)]


def test_place_icon():
    assert place_icon(1) == "🥇" and place_icon(3) == "🥉" and place_icon(4) == "4."
