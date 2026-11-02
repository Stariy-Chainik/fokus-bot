"""Идемпотентно добавляет в лист `athlete_tasks` колонку 16 `video_file_ids` — видео выполнения задания.

    .venv/bin/python scripts/setup_homework_video.py            # показать, что будет сделано
    .venv/bin/python scripts/setup_homework_video.py --apply    # добавить колонку

Спортсмен присылает ролик в чат бота, файл хранит Telegram; в ячейке — ссылки `kind:file_id` через `|`
(решение владельца 02.11.2026). Запускать до выкладки кода: бот пишет 16 колонок.
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
        "status", "closed_at", "batch_id", "group_id", "grade", "grade_comment", "graded_at"]
COLUMN = "video_file_ids"


def main(apply: bool) -> None:
    ws = SheetsClient(settings).get_worksheet(settings.sheet_athlete_tasks)
    header = ws.row_values(1)
    if header[:len(BASE)] != BASE:
        print(f"Неожиданные заголовки листа: {header} — сначала scripts/setup_homework.py, потом проверьте вручную")
        return
    if COLUMN in header:
        print("Колонка уже есть — делать нечего")
        return
    if len(header) != len(BASE):
        print(f"После {len(BASE)} колонок ожидалось пусто, а там {header[len(BASE):]} — проверьте вручную")
        return
    col = len(BASE) + 1
    print(f"Лист «{settings.sheet_athlete_tasks}»: добавим «{COLUMN}» в колонку №{col}")
    if not apply:
        print("Это предпросмотр. Повторите с --apply, чтобы применить.")
        return
    if ws.col_count < col:
        ws.add_cols(col - ws.col_count)
    ws.update([[COLUMN]], f"{chr(ord('A') + col - 1)}1")
    print("готово:", ws.row_values(1))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    main(parser.parse_args().apply)
