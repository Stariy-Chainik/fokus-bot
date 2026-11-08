"""Идемпотентно приводит лист учеников к колонкам бальных танцев 13–18:
`class_st`, `class_la`, `class_solo_st`, `class_solo_la`, `sport_rank`, `ftsarr_id`.

    .venv/bin/python scripts/setup_dance_rank.py            # показать, что будет сделано
    .venv/bin/python scripts/setup_dance_rank.py --apply    # применить

Парные классы ФТСАРР по стандарту и латине, классы соло (у солисток), спортивный разряд и номер классификационной
книжки (решение владельца 08.11.2026: «четыре класса плюс разряд»). Первая версия 08.11.2026 добавляла две колонки
`dance_class` / `sport_rank` — если они ещё пустые, скрипт переименует их в `class_st` / `class_la` и допишет остальные.
См. bot/services/dance_rank.py.
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

BEFORE = 12
COLUMNS = ["class_st", "class_la", "class_solo_st", "class_solo_la", "sport_rank", "ftsarr_id"]
LEGACY = ["dance_class", "sport_rank"]


def main(apply: bool) -> None:
    ws = SheetsClient(settings).get_worksheet(settings.sheet_students)
    header = ws.row_values(1)
    tail = header[BEFORE:]
    if tail == COLUMNS:
        print("Колонки 13–18 уже на месте — делать нечего")
        return
    renames: dict[int, str] = {}
    if tail == LEGACY:                                   # первая версия: переименовать, если пусто
        for i, name in enumerate(LEGACY, start=BEFORE + 1):
            values = [v for v in ws.col_values(i)[1:] if str(v).strip()]
            if values:
                print(f"В колонке №{i} «{name}» уже есть {len(values)} значений — переименовать нельзя, проверьте лист вручную")
                return
            renames[i] = COLUMNS[i - BEFORE - 1]
        tail = list(COLUMNS[:2])
    elif tail:
        print(f"После 12-й колонки неожиданные заголовки {tail} — проверьте лист вручную")
        return
    missing = COLUMNS[len(tail):]
    print(f"Лист «{settings.sheet_students}»: {len(header)} колонок."
          + (f" Переименуем {', '.join(f'№{i} → {n}' for i, n in renames.items())}." if renames else "")
          + (f" Добавим {', '.join(missing)} как №{BEFORE + len(tail) + 1}–{BEFORE + len(COLUMNS)}." if missing else ""))
    if not apply:
        print("Это предпросмотр. Повторите с --apply, чтобы применить.")
        return
    need = BEFORE + len(COLUMNS)
    if ws.col_count < need:                     # у листа может не быть свободных колонок в сетке
        ws.add_cols(need - ws.col_count)
    for i, name in renames.items():
        ws.update_cell(1, i, name)
    for i, name in enumerate(missing, start=BEFORE + len(tail) + 1):
        ws.update_cell(1, i, name)
    print("готово")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    main(parser.parse_args().apply)
