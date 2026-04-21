"""
Одноразовая миграция: переносит `students.group_id` в лист `student_groups`.

Идемпотентна: повторный запуск не создаст дублей (проверяет по паре
student_id + group_id).

Использование:
    .venv/bin/python scripts/migrate_student_groups.py
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gspread
from google.oauth2.service_account import Credentials
from config.settings import settings


def get_sheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(
        settings.google_credentials_dict, scopes=scopes
    )
    return gspread.authorize(creds).open_by_key(settings.spreadsheet_id)


def main() -> None:
    sh = get_sheet()

    # Читаем текущий состав таблицы students.
    student_rows = sh.worksheet(settings.sheet_students).get_all_records()
    src_pairs: list[tuple[str, str, str]] = []  # (sid, gid, name)
    for r in student_rows:
        sid = str(r.get("student_id") or "").strip()
        gid = str(r.get("group_id") or "").strip()
        name = str(r.get("name") or "").strip()
        if sid and gid:
            src_pairs.append((sid, gid, name))

    # Читаем существующие student_groups (чтобы повторный запуск был no-op).
    try:
        ws = sh.worksheet(settings.sheet_student_groups)
    except gspread.exceptions.WorksheetNotFound:
        print(
            f"❌ Лист «{settings.sheet_student_groups}» не найден. "
            "Создайте его вручную с заголовками: student_id | group_id."
        )
        sys.exit(1)

    existing = {
        (str(r.get("student_id") or "").strip(), str(r.get("group_id") or "").strip())
        for r in ws.get_all_records()
    }

    to_add = [(sid, gid, name) for sid, gid, name in src_pairs if (sid, gid) not in existing]

    print(f"students с заполненным group_id: {len(src_pairs)}")
    print(f"уже в student_groups:            {len(existing)}")
    print(f"к добавлению:                    {len(to_add)}")

    if not to_add:
        print("✓ Ничего делать не нужно.")
        return

    rows = [[sid, gid] for sid, gid, _ in to_add]
    ws.append_rows(rows, value_input_option="RAW")

    print("\n--- Добавлено ---")
    for sid, gid, name in to_add:
        print(f"  {name:30s}  {sid}  →  {gid}")
    print(f"\n✓ Записей добавлено: {len(to_add)}")


if __name__ == "__main__":
    main()
