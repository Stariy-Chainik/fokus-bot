"""Идемпотентно добавляет payment_method в student_period_payments.

Использование:
    .venv/bin/python scripts/setup_payment_method_column.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gspread
from google.oauth2.service_account import Credentials

from config.settings import settings


EXPECTED_PREFIX = [
    "payment_id", "student_id", "student_name", "period_month", "total_amount",
    "status", "paid_at", "confirmed_by_tg_id", "comment", "created_at", "updated_at",
    "teacher_id", "teacher_name",
]


def main() -> None:
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(settings.google_credentials_dict, scopes=scopes)
    sh = gspread.authorize(creds).open_by_key(settings.spreadsheet_id)
    ws = sh.worksheet(settings.sheet_payments)
    header = ws.row_values(1)
    if header[:len(EXPECTED_PREFIX)] != EXPECTED_PREFIX:
        raise SystemExit(f"Заголовок {ws.title} отличается; изменения не внесены: {header}")
    if "payment_method" in header:
        idx = header.index("payment_method") + 1
        if idx != 14:
            raise SystemExit(f"payment_method найден в колонке {idx}, ожидалась 14")
        print("student_period_payments.payment_method: есть (колонка 14)")
        return
    ws.update_cell(1, 14, "payment_method")
    print("student_period_payments.payment_method: добавлена колонка 14")


if __name__ == "__main__":
    main()
