#!/usr/bin/env python3
"""Ночной снапшот всех листов Google Sheets в сжатый JSON (.json.gz).

Читает все листы одним `values_batch_get` (не тратит квоту API), пишет атомарно
с правами 0600 и хранит последние `BACKUP_KEEP` архивов (по умолчанию 30 дней).
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _a1_sheet(title: str) -> str:
    escaped = title.replace("'", "''")
    return f"'{escaped}'"


def export_spreadsheet(spreadsheet: Any, *, spreadsheet_id: str = "") -> dict[str, Any]:
    """Выгрузить все листы таблицы в словарь `{title: [[cell, ...], ...]}`."""
    worksheets = list(spreadsheet.worksheets())
    titles = [ws.title for ws in worksheets]
    sheets: dict[str, list[list[str]]] = {}

    if titles and hasattr(spreadsheet, "values_batch_get"):
        ranges = [_a1_sheet(t) for t in titles]
        resp = spreadsheet.values_batch_get(ranges)
        value_ranges = resp.get("valueRanges") if isinstance(resp, dict) else None
        if isinstance(value_ranges, list) and len(value_ranges) == len(titles):
            for title, vr in zip(titles, value_ranges, strict=True):
                raw = vr.get("values") if isinstance(vr, dict) else None
                sheets[title] = [list(map(str, row)) for row in (raw or [])]
        else:
            for ws in worksheets:
                sheets[ws.title] = [list(map(str, row)) for row in ws.get_all_values()]
    else:
        for ws in worksheets:
            sheets[ws.title] = [list(map(str, row)) for row in ws.get_all_values()]

    total_rows = sum(len(rows) for rows in sheets.values())
    return {
        "spreadsheetId": spreadsheet_id or getattr(spreadsheet, "id", ""),
        "exportedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sheetCount": len(sheets),
        "totalRows": total_rows,
        "sheets": sheets,
    }


def write_backup(
    payload: dict[str, Any],
    backup_dir: Path,
    *,
    keep: int = 30,
    stamp: str | None = None,
) -> Path:
    """Атомарно записать `.json.gz` и удалить старые архивы сверх `keep`."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)

    ts = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = backup_dir / f"sheets-{ts}.json.gz"
    tmp_path = backup_dir / f".sheets-{ts}.json.gz.tmp"

    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(tmp_path, "wb", compresslevel=6) as fh:
        fh.write(raw)
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(out_path)

    if keep > 0:
        archives = sorted(
            backup_dir.glob("sheets-*.json.gz"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in archives[keep:]:
            old.unlink(missing_ok=True)

    return out_path


def main() -> int:
    import gspread
    from google.oauth2.service_account import Credentials

    from bot.repositories.sheets_client import SCOPES
    from config.settings import settings

    backup_dir = Path(os.environ.get("BACKUP_DIR", "/var/backups/fokus-bot"))
    keep = int(os.environ.get("BACKUP_KEEP", "30"))

    creds = Credentials.from_service_account_info(settings.google_credentials_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(settings.spreadsheet_id)

    payload = export_spreadsheet(spreadsheet, spreadsheet_id=settings.spreadsheet_id)
    out = write_backup(payload, backup_dir, keep=keep)
    size_kb = out.stat().st_size / 1024
    print(
        f"✓ sheets backup: {out} "
        f"({payload['sheetCount']} sheets, {payload['totalRows']} rows, {size_kb:.1f} KB)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
