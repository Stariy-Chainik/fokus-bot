"""
Read-only аудит перед удалением сущности teacher_students.

Сверяет каждую строку teacher_students с правилом
«ученик видим педагогу ⇔ student.group_id ∈ teacher_groups[teacher_id]»
и печатает расхождения.

Использование:
    .venv/bin/python scripts/audit_teacher_students.py
"""
from __future__ import annotations
import sys
from collections import defaultdict
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

    teachers = {
        str(r["teacher_id"]): r["name"]
        for r in sh.worksheet(settings.sheet_teachers).get_all_records()
    }
    students = {
        str(r["student_id"]): {"name": r["name"], "group_id": str(r.get("group_id") or "")}
        for r in sh.worksheet(settings.sheet_students).get_all_records()
    }
    groups = {
        str(r["group_id"]): r["name"]
        for r in sh.worksheet(settings.sheet_groups).get_all_records()
    }
    tg_rows = sh.worksheet(settings.sheet_teacher_groups).get_all_records()
    teacher_groups: dict[str, set[str]] = defaultdict(set)
    for r in tg_rows:
        teacher_groups[str(r["teacher_id"])].add(str(r["group_id"]))

    ts_rows = sh.worksheet(settings.sheet_teacher_students).get_all_records()

    total = len(ts_rows)
    ok = 0
    missing_teacher: list[tuple[str, str]] = []
    missing_student: list[tuple[str, str]] = []
    student_no_group: list[tuple[str, str]] = []
    mismatch: list[tuple[str, str, str, str, str]] = []  # (tid, tname, sid, sname, gname)

    for r in ts_rows:
        tid = str(r["teacher_id"])
        sid = str(r["student_id"])
        tname = teachers.get(tid)
        if tname is None:
            missing_teacher.append((tid, sid))
            continue
        s = students.get(sid)
        if s is None:
            missing_student.append((tid, sid))
            continue
        gid = s["group_id"]
        if not gid:
            student_no_group.append((tid, sid))
            continue
        if gid in teacher_groups.get(tid, set()):
            ok += 1
        else:
            mismatch.append((tid, tname, sid, s["name"], groups.get(gid, f"(?{gid})")))

    print(f"=== Аудит teacher_students (всего строк: {total}) ===\n")
    print(f"✓ валидных (педагог прикреплён к группе ученика):  {ok}")
    print(f"✗ расхождений (группа ученика не в группах педагога): {len(mismatch)}")
    print(f"⚠ ученик без group_id:                            {len(student_no_group)}")
    print(f"⚠ педагог не найден в teachers:                   {len(missing_teacher)}")
    print(f"⚠ ученик не найден в students:                    {len(missing_student)}")

    if mismatch:
        print("\n--- Расхождения (педагог не прикреплён к группе ученика) ---")
        by_teacher: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for tid, tname, sid, sname, gname in mismatch:
            by_teacher[f"{tid}  {tname}"].append((sid, sname, gname))
        for teacher, items in sorted(by_teacher.items()):
            print(f"\n  {teacher}  ({len(items)}):")
            for sid, sname, gname in sorted(items, key=lambda x: x[1]):
                print(f"    - {sname}  →  группа: {gname}  [{sid}]")

    if student_no_group:
        print("\n--- Ученики без group_id ---")
        for tid, sid in student_no_group:
            sname = students.get(sid, {}).get("name", "?")
            tname = teachers.get(tid, "?")
            print(f"  {tname} [{tid}]  →  {sname} [{sid}]")

    if missing_teacher:
        print("\n--- Строки с неизвестным teacher_id ---")
        for tid, sid in missing_teacher:
            print(f"  teacher_id={tid}  student_id={sid}")

    if missing_student:
        print("\n--- Строки с неизвестным student_id ---")
        for tid, sid in missing_student:
            print(f"  teacher_id={tid}  student_id={sid}")


if __name__ == "__main__":
    main()
