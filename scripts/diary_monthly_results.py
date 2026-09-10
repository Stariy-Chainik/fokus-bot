"""Итоги месяца по рейтингу спортсменов: рассылка спортсменам и педагогам.

Запускается таймером 1-го числа (fokus-diary-results.timer) — подводит итоги
ПРОШЛОГО месяца. Явный аргумент YYYY-MM — подвести итоги указанного месяца.

Использование:
    .venv/bin/python scripts/diary_monthly_results.py [YYYY-MM] [--dry-run]
"""
from __future__ import annotations
import asyncio
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402

from config.settings import settings  # noqa: E402
from bot.repositories import (  # noqa: E402
    SheetsClient, StudentRepository, StudentGroupRepository, GroupRepository,
    TeacherRepository, TeacherGroupRepository, TrainingEntryRepository, AthleteTaskRepository,
)
from bot.services import DiaryService, TeacherVisibilityService  # noqa: E402
from bot.services.diary_service import place_icon  # noqa: E402
from bot.utils.dates import display_period, last_periods  # noqa: E402
from bot.utils.diary_format import minutes_human  # noqa: E402
from bot.utils.notify import notify  # noqa: E402


def _prev_month() -> str:
    return last_periods(2)[1]


async def main(period: str, dry_run: bool) -> None:
    sc = SheetsClient(settings)
    student_repo = StudentRepository(sc, settings.sheet_students)
    student_group_repo = StudentGroupRepository(sc, settings.sheet_student_groups)
    group_repo = GroupRepository(sc, settings.sheet_groups)
    teacher_repo = TeacherRepository(sc, settings.sheet_teachers)
    teacher_group_repo = TeacherGroupRepository(sc, settings.sheet_teacher_groups)
    visibility = TeacherVisibilityService(student_repo, teacher_group_repo, student_group_repo)
    diary = DiaryService(
        TrainingEntryRepository(sc, settings.sheet_training_entries),
        AthleteTaskRepository(sc, settings.sheet_athlete_tasks),
        student_repo, student_group_repo, group_repo, visibility,
    )
    athletes = await diary.linked_athletes()
    rows = await diary.leaderboard(period)
    active = [r for r in rows if r.sessions]
    if not active:
        print(f"{period}: тренировок нет — рассылка не нужна")
        return

    top = "\n".join(
        f"{place_icon(r.place)} {r.name} — {r.points} оч. ({minutes_human(r.minutes)}"
        + (f", ⭐{r.avg_grade}" if r.avg_grade is not None else "") + ")"
        for r in active[:10]
    )
    header = f"🏆 <b>Итоги месяца · {display_period(period)}</b>\n<i>очки = минуты × оценка педагога</i>\n\n{top}"
    by_id = {r.student_id: r for r in rows}

    sent = 0
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        for s in athletes:
            r = by_id.get(s.student_id)
            mine = (f"\n\nВаш результат: <b>{r.place} место</b> из {len(active)}, {r.points} оч."
                    if r and r.sessions else "\n\nВ этом месяце записей не было — начните новый месяц с тренировки!")
            text = header + mine
            if dry_run:
                print(f"→ {s.name} ({s.athlete_tg_id}): {text[-80:]!r}")
            else:
                sent += await notify(bot, [s.athlete_tg_id], text)
        # Педагогам — тем, у кого есть спортсмены
        athlete_ids = {s.student_id for s in athletes}
        for t in await teacher_repo.get_all():
            if not t.tg_id:
                continue
            mine = {s.student_id for s in await visibility.students_for_teacher(t.teacher_id)} & athlete_ids
            if not mine:
                continue
            if dry_run:
                print(f"→ педагог {t.name} ({t.tg_id})")
            else:
                sent += await notify(bot, [t.tg_id], header)
    finally:
        await bot.session.close()
    print(f"{period}: активных {len(active)}, отправлено {sent}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    asyncio.run(main(args[0] if args else _prev_month(), "--dry-run" in sys.argv))
