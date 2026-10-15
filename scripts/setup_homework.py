"""Идемпотентно добавляет в лист `athlete_tasks` колонки домашних заданий группе (11–15).

    .venv/bin/python scripts/setup_homework.py            # показать, что будет сделано
    .venv/bin/python scripts/setup_homework.py --apply    # добавить колонки

batch_id — номер выдачи (общий у всех учеников, HW-…), group_id — группа, grade / grade_comment / graded_at —
оценка педагога за выполнение (решение владельца 15.10.2026). Запускать до выкладки кода: бот пишет 15 колонок.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env.prod", override=True)
load_dotenv(".env")

from bot.repositories.sheets_client import SheetsClient  # noqa: E402
from config.settings import settings  # noqa: E402

BASE = ["task_id", "student_id", "teacher_id", "exercise", "minutes", "comment", "source", "created_at",
        "status", "closed_at"]
COLUMNS = ["batch_id", "group_id", "grade", "grade_comment", "graded_at"]


def main(apply: bool) -> None:
    ws = SheetsClient(settings).get_worksheet(settings.sheet_athlete_tasks)
    header = ws.row_values(1)
    if header[:len(BASE)] != BASE:
        print(f"Неожиданные заголовки листа: {header} — проверьте вручную")
        return
    missing = [c for c in COLUMNS if c not in header]
    if not missing:
        print("Колонки уже есть — делать нечего")
        return
    if header[len(BASE):] != COLUMNS[:len(header) - len(BASE)]:
        print(f"После {len(BASE)} колонок ожидалось {COLUMNS}, а там {header[len(BASE):]} — проверьте вручную")
        return
    start = len(header) + 1
    print(f"Лист «{settings.sheet_athlete_tasks}»: добавим {missing} начиная с колонки №{start}")
    if not apply:
        print("Это предпросмотр. Повторите с --apply, чтобы применить.")
        return
    need = len(BASE) + len(COLUMNS)
    if ws.col_count < need:
        ws.add_cols(need - ws.col_count)
    ws.update([missing], f"{chr(ord('A') + start - 1)}1")
    print("готово:", ws.row_values(1))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    main(parser.parse_args().apply)
