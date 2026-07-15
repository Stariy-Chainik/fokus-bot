"""Выгрузка всей БД (Google Sheets) в JSON — для переноса в другое хранилище.

Читает КАЖДУЮ вкладку таблицы как есть (строка 1 = заголовки) и пишет:
  db_export/<вкладка>.json  — список записей {колонка: значение-строка}
  db_export/_meta.json      — счётчики строк и заголовки (для валидации переноса)

Запуск:  .venv/bin/python scripts/export_db_json.py
Только чтение; структура и семантика полей — docs/DB_HANDOFF.md.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

from config.settings import settings  # noqa: E402
from bot.repositories.sheets_client import SheetsClient  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "db_export"


def _safe_name(title: str) -> str:
    return re.sub(r"[^\w\-]+", "_", title, flags=re.U).strip("_")


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    sc = SheetsClient(settings)
    sc._ensure_connected()
    meta: dict[str, dict] = {"exported_at": datetime.now().isoformat(timespec="seconds"),
                             "spreadsheet_id": settings.spreadsheet_id, "sheets": {}}

    for ws in sc._spreadsheet.worksheets():
        values = ws.get_all_values()
        header = values[0] if values else []
        records = [dict(zip(header, row)) for row in values[1:]]
        fname = _safe_name(ws.title) + ".json"
        (OUT_DIR / fname).write_text(
            json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8",
        )
        meta["sheets"][ws.title] = {"rows": len(records), "columns": header, "file": fname}
        print(f"  {ws.title}: {len(records)} строк → db_export/{fname}")

    (OUT_DIR / "_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8",
    )
    print(f"Готово: {len(meta['sheets'])} вкладок, метаданные в db_export/_meta.json")


if __name__ == "__main__":
    main()
