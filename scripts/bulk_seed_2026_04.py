"""
Одноразовый скрипт массового заполнения базы по итогам анализа расписания
2026-04-13. Идемпотентен: повторный запуск не создаёт дублей.

Использование:
    .venv/bin/python scripts/bulk_seed_2026_04.py --dry-run
    .venv/bin/python scripts/bulk_seed_2026_04.py --apply
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gspread
from google.oauth2.service_account import Credentials
from config.settings import settings
from bot.utils import generate_student_id

NEW_STUDENTS = [
    "Ким Алина", "Фисюнова Алиса", "Шабанова Алиса", "Будиловская Ярослава",
    "Карслян Бэлла", "Грибенюк Вера", "Кретова Дарина", "Мадатова Ира",
    "Имаметдинова Камилла", "Бухтоярова Кристина", "Вихрова Лиза",
    "Бискуп Маша", "Черба Руслана", "Прудникова Варя", "Прудникова Дарья",
    "Бущук Злата", "Шеремет Алина", "Вихров Гордей", "Кюркчу Лена",
    "Доронина Лера", "Мещерякова Настя", "Мадатов Коля", "Осипова Соня",
    "Васькин Саша", "Дикая Эмилия", "Зотов Антон", "Татьяна ПроЭм",
    "Александрова Альбина", "Мадатов Петя", "Асликян Амелия",
]

# teacher_id -> [student_name, ...]
BINDINGS = {
    "TCH-0001": [  # Река Станислав (СА)
        "Зотов Антон", "Полтавцев Егор", "Мадатова Ира", "Имаметдинова Камилла",
        "Сухова Екатерина", "Мадатов Коля", "Байгильдин Мирон", "Васькин Саша",
        "Дикая Эмилия", "Осипова Соня", "Татаркина София",
    ],
    "TCH-0002": [  # Клецова Ангелина
        "Ким Алина", "Фисюнова Алиса", "Шабанова Алиса", "Асликян Амелия",
        "Зотов Антон", "Будиловская Ярослава", "Карслян Бэлла", "Грибенюк Вера",
        "Кретова Дарина", "Полтавцев Егор", "Мадатова Ира", "Сухова Екатерина",
        "Мадатов Коля", "Осипова Соня", "Бухтоярова Кристина", "Вихрова Лиза",
        "Бискуп Маша", "Байгильдин Мирон", "Черба Руслана", "Татаркина София",
    ],
    "TCH-0003": [  # Лобачев Иван
        "Ким Алина", "Будиловская Ярослава", "Кретова Дарина", "Прудникова Дарья",
        "Мадатов Коля", "Байгильдин Мирон", "Осипова Соня", "Татаркина София",
    ],
    "TCH-0004": [  # Пахомова Юлия
        "Мадатова Ира", "Вихрова Лиза", "Байгильдин Мирон", "Мадатов Петя",
        "Татаркина София",
    ],
    "TCH-0005": [  # Никишин Влад
        "Прудникова Варя", "Грибенюк Вера", "Прудникова Дарья", "Полтавцев Егор",
        "Бущук Злата", "Сухова Екатерина", "Татьяна ПроЭм",
    ],
    "TCH-0006": [  # Лобачева Ксения
        "Черба Руслана", "Васькин Саша", "Дикая Эмилия",
    ],
    "TCH-0007": [  # Хуснутдинов Назар
        "Ким Алина", "Шабанова Алиса", "Зотов Антон", "Будиловская Ярослава",
        "Бущук Злата", "Мадатова Ира", "Имаметдинова Камилла", "Мадатов Коля",
        "Байгильдин Мирон", "Черба Руслана", "Васькин Саша", "Дикая Эмилия",
        "Осипова Соня", "Татаркина София",
    ],
    "TCH-0008": [  # Криворчук Валерия (Лера)
        "Полтавцев Егор", "Сухова Екатерина", "Мадатов Коля", "Осипова Соня",
    ],
    "TCH-0009": [  # Контарева Елизавета
        "Шеремет Алина", "Александрова Альбина", "Карслян Бэлла", "Прудникова Варя",
        "Грибенюк Вера", "Вихров Гордей", "Бущук Злата", "Бухтоярова Кристина",
        "Кюркчу Лена", "Доронина Лера", "Вихрова Лиза", "Бискуп Маша",
        "Мещерякова Настя", "Черба Руслана",
    ],
}

# Пары: (student_name_a, student_name_b)
PAIRS = [
    ("Мадатов Коля", "Осипова Соня"),
    ("Васькин Саша", "Дикая Эмилия"),
    ("Полтавцев Егор", "Сухова Екатерина"),
]


def get_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(
        settings.google_credentials_dict, scopes=scopes
    )
    return gspread.authorize(creds).open_by_key(settings.spreadsheet_id)


def main(apply: bool):
    sh = get_client()
    students_ws = sh.worksheet("students")
    ts_ws = sh.worksheet("teacher_students")

    students = students_ws.get_all_records()
    name_to_id = {r["name"]: str(r["student_id"]) for r in students}
    existing_ids = list(name_to_id.values())

    # === 1. Новые ученики ===
    to_add_students = [n for n in NEW_STUDENTS if n not in name_to_id]
    new_rows = []
    for name in to_add_students:
        sid = generate_student_id(existing_ids)
        existing_ids.append(sid)
        name_to_id[name] = sid
        new_rows.append([sid, name, ""])
    print(f"[students] добавить: {len(new_rows)} (всего в списке: {len(NEW_STUDENTS)})")
    for r in new_rows:
        print(f"  + {r[0]}  {r[1]}")

    if apply and new_rows:
        students_ws.append_rows(new_rows, value_input_option="USER_ENTERED")
        print(f"[students] записано {len(new_rows)} строк")

    # === 2. teacher_students связки ===
    existing_pairs = {
        (str(r["teacher_id"]), str(r["student_id"]))
        for r in ts_ws.get_all_records()
    }
    new_bindings = []
    missing_students = []
    for tid, names in BINDINGS.items():
        for name in names:
            sid = name_to_id.get(name)
            if not sid:
                missing_students.append((tid, name))
                continue
            if (tid, sid) not in existing_pairs:
                new_bindings.append([tid, sid])

    print(f"\n[teacher_students] добавить: {len(new_bindings)}; пропущено (уже есть): {sum(len(v) for v in BINDINGS.values()) - len(new_bindings) - len(missing_students)}")
    if missing_students:
        print("  ВНИМАНИЕ — ученики не найдены:")
        for tid, name in missing_students:
            print(f"    {tid} -> {name}")
        return

    if apply and new_bindings:
        ts_ws.append_rows(new_bindings, value_input_option="USER_ENTERED")
        print(f"[teacher_students] записано {len(new_bindings)} строк")

    # === 3. Пары ===
    print(f"\n[pairs] установить: {len(PAIRS)}")
    if apply:
        # Перечитываем students, чтобы узнать актуальные индексы строк
        all_rows = students_ws.get_all_values()
        header = all_rows[0]
        sid_col = header.index("student_id") + 1
        partner_col = header.index("partner_id") + 1
        sid_to_row = {row[sid_col - 1]: idx + 1 for idx, row in enumerate(all_rows[1:], start=1)}

        for a_name, b_name in PAIRS:
            a_id = name_to_id[a_name]
            b_id = name_to_id[b_name]
            students_ws.update_cell(sid_to_row[a_id], partner_col, b_id)
            students_ws.update_cell(sid_to_row[b_id], partner_col, a_id)
            print(f"  ✓ {a_name} ({a_id}) ↔ {b_name} ({b_id})")
    else:
        for a_name, b_name in PAIRS:
            print(f"  ↔ {a_name} ({name_to_id.get(a_name, '?')}) ↔ {b_name} ({name_to_id.get(b_name, '?')})")

    print("\nГотово." if apply else "\nDRY-RUN — изменений в таблице нет.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = p.parse_args()
    main(apply=args.apply)
