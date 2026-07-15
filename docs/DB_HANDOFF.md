# База данных fokus-bot — handoff для переноса

> **Назначение.** Самодостаточное описание БД для другой системы/нейросети: полная структура,
> семантика, конвенции и порядок переноса. Прочитав только этот файл, можно корректно выгрузить
> данные и воспроизвести их в любом хранилище. Снапшот схем снят с живой таблицы 2026-07-15.

---

## 1. Что это за БД

- **Хранилище:** один Google Spreadsheet (ID — в env `SPREADSHEET_ID`), доступ через
  service-account (`GOOGLE_CREDENTIALS_JSON`). Библиотека — gspread.
- **Одна вкладка = одна таблица.** Строка 1 — заголовки (имена колонок), данные со строки 2.
- Все значения хранятся как текст ячеек; типы — по договорённости (см. конвенции §4).
- **Готовая выгрузка:** `scripts/export_db_json.py` → папка `db_export/` (JSON на каждую вкладку
  + `_meta.json` со счётчиками). Запуск: `.venv/bin/python scripts/export_db_json.py`.

## 2. Снапшот вкладок (2026-07-15)

| Вкладка | Строк | Статус |
|---|---|---|
| `users` | 13 | активна |
| `teachers` | 11 | активна |
| `students` | 163 | активна (есть 2 legacy-колонки) |
| `clients` | 0 | активна (пока пусто) |
| `student_groups` | 272 | активна |
| `teacher_groups` | 49 | активна |
| `branches` | 3 | активна |
| `groups` | 17 | активна |
| `lessons` | 1276 | активна, главная таблица |
| `teacher_period_submissions` | 14 | активна |
| `student_period_payments` | 162 | активна |
| `subscription_overrides` | 0 | активна (новая) |
| `finance_entries` | 0 | активна (новая) |
| `student_requests` | 17 | активна |
| `billing` | 0 | **виртуальная — НЕ переносить** (см. §5) |
| `teacher_students(архив)` | 328 | **legacy-архив — НЕ переносить** |
| `client_invite_codes` | 1 | **legacy, кодом не используется — НЕ переносить** |

## 3. Схемы таблиц (колонки в точном порядке листа)

Обозначения: `PK` — первичный ключ, `FK→` — ссылка по соглашению (внешних ключей в Sheets нет),
`∅` — пустая строка означает NULL.

### users — доступ в бот (админы и педагоги)
| колонка | тип | семантика |
|---|---|---|
| user_id | PK, `USR-XXXX` | |
| tg_id | int | Telegram ID |
| is_admin | bool | истина = `true`/`1`/`yes` (без регистра); всё прочее = false |
| teacher_id | FK→teachers ∅ | если задан — пользователь педагог (может быть и админом одновременно) |

Роль клиента (родителя) в `users` НЕ хранится — клиент определяется по `students.parent_tg_ids`.

### teachers — педагоги и ставки
| колонка | тип | семантика |
|---|---|---|
| teacher_id | PK, `TCH-XXXX` | |
| tg_id | int ∅ | |
| name | str | |
| rate_group | int | ₽ за 45 мин, групповое занятие (зарплата) |
| rate_for_teacher | int | ₽ за 45 мин, индивидуальное (зарплата) |
| rate_for_student | int | ₽ за 45 мин, ставка для счёта ученику |

### students — ученики
| колонка | тип | семантика |
|---|---|---|
| student_id | PK, `STU-XXXX` | |
| name | str | **фамилия всегда первая** («Иванова Мария») |
| partner_id | FK→students ∅ | пара; связь **симметричная** (у обоих взаимные ссылки) |
| group_id | — | **LEGACY, кодом игнорируется** (членство → `student_groups`); не переносить |
| group_tier | enum ∅ | `full` (default) \| `short` — тариф в PER_VISIT-группах с dual-pricing |
| client_id | FK→clients ∅ | кто платит |
| parent_tg_ids | int-список ∅ | Telegram ID родителей с доступом; **разделитель `\|`** (старые строки могут быть через `,`) |
| kindergarten_group | — | **LEGACY, кодом игнорируется**; не переносить |

### clients — плательщики (родители как сущность)
| client_id `CLT-XXXX` PK | name | tg_id int ∅ | created_at datetime | phone str ∅ (нормализуется к `+7…`) |

### student_groups / teacher_groups — членство (many-to-many)
| student_groups: student_id FK, group_id FK | teacher_groups: teacher_id FK, group_id FK |
Уникальность пары — по соглашению. **Видимость педагог↔ученик = пересечение этих таблиц**
(прямой связи педагог-ученик нет).

### branches — филиалы
| branch_id `BRN-XXXX` PK | name | created_at | updated_at |

### groups — группы
| колонка | тип | семантика |
|---|---|---|
| group_id | PK, `GRP-XXXX` | |
| branch_id | FK→branches | |
| name | str | |
| created_at / updated_at | datetime | |
| sort_order | int | сортировка в списках |
| billing_mode | enum | `none` (бесплатно/вне системы) \| `per_visit` (по посещениям) \| `subscription` (фикс ₽/мес) |
| price_short / duration_short | int | короткий тариф per_visit (default 35 мин); только детсадовские группы |
| price_full / duration_full | int | полный тариф per_visit (60 мин) **ИЛИ цена абонемента ₽/мес при `subscription`** |

### lessons — занятия (главная таблица)
| колонка | тип | семантика |
|---|---|---|
| lesson_id | PK, `LES-XXXXXX` | |
| teacher_id | FK→teachers | владелец занятия |
| teacher_name | str | **снапшот имени на момент записи** (денормализация намеренная) |
| type | enum | `group` \| `individual` |
| student_1_id/_name … student_4_id/_name | FK ∅ / str ∅ | только для `individual`: 1–4 участника (пары/соло/микрогруппы); имена — снапшоты |
| date | `YYYY-MM-DD` | период = `date[:7]` |
| duration_min | int | 30/35/45/60/90 |
| earned | int | **всегда 0 — НЕ источник зарплаты** (зарплата вычисляется, §5) |
| recorded_at / updated_at | `YYYY-MM-DD HH:MM:SS` | |
| attendees | str ∅ | только для `group` — присутствующие, **два формата** (см. §4.3) |
| group_id | FK→groups ∅ | только для `group` |

### teacher_period_submissions — сдача периода (замок)
| submission_id `SUB-XXXXXX` PK | teacher_id FK | period_month `YYYY-MM` | submitted_at | lessons_count int | total_earned int |
Наличие строки = педагог сдал месяц и не может менять свои занятия в нём. Удаление строки = переоткрытие.

### student_period_payments — счета/оплаты
| колонка | тип | семантика |
|---|---|---|
| payment_id | PK, `PAY-XXXXXX` | |
| student_id | FK→students | |
| student_name | str | снапшот |
| period_month | `YYYY-MM` | |
| total_amount | int | сумма счёта на момент выставления/оплаты |
| status | enum | `pending` \| `paid` |
| paid_at | datetime ∅ | |
| confirmed_by_tg_id | int ∅ | 0 = подтверждено webhook'ом ЮКассы |
| comment | str ∅ | |
| created_at / updated_at | datetime | |
| teacher_id | str | FK→teachers **ЛИБО синтетический ключ `SUB:{group_id}`** — абонементное начисление группы (не педагог!) |
| teacher_name | str | для `SUB:` — «Абонемент «{группа}»» |

Уникальность: **один счёт на (student_id, teacher_id, period_month)** — по соглашению.

### subscription_overrides — цена абонемента на конкретный месяц
| group_id FK | period_month `YYYY-MM` | student_id FK ∅ (пусто = вся группа) | amount int (0 = не начислять) | created_at |
Приоритет при начислении: **ученик → группа → groups.price_full**. Upsert по (group_id, period, student_id).

### finance_entries — ручные доходы/расходы месяца (экран «Прибыль»)
| entry_id `FIN-XXXXXX` PK | period_month `YYYY-MM` | kind `income`\|`expense` | title str | amount int | created_at |

### student_requests — заявки педагогов на новых учеников
| request_id PK | teacher_id FK | teacher_tg_id int | teacher_name | student_name | group_id FK ∅ | status `pending`\|`approved`\|`rejected` | created_at | resolved_at ∅ | resolved_by_tg_id ∅ | admin_msgs_json (JSON `[[chat_id, message_id],…]`) |

## 4. Конвенции хранения (критично для парсинга)

1. **ID** — читаемые строки `PREFIX-число с паддингом`: `TCH-4`, `STU-4`, `LES-6`, `PAY-6`, `SUB-6`,
   `BRN-4`, `GRP-4`, `USR-4`, `CLT-4`, `FIN-6` (цифра = ширина). Генерация: max существующего + 1.
2. **Разделитель списков — `|`, НЕ запятая** (`students.parent_tg_ids`): русская локаль Google Sheets
   превращает `"123,456"` в число `123.456`. Старые строки могут содержать `,` — принимать оба.
3. **`lessons.attendees` — два формата в одном поле** (разделитель записей — запятая):
   - старый: `STU-0001,STU-0002` — только id; длительность = duration_min занятия, сумма = 0;
   - новый: `STU-0001:60:700,STU-0002:35:500` — `id:минуты:₽-снапшот цены на момент записи`.
   - **`amount = 0` = «абонементщик»** (не биллится); > 0 = per-visit начисление.
4. Дата `YYYY-MM-DD`, период `YYYY-MM`, datetime `YYYY-MM-DD HH:MM:SS` (локальное время сервера).
5. Пустая ячейка = NULL. Booleans: `true`/`1`/`yes` (регистронезависимо) = истина.
6. Снапшоты имён (`teacher_name`, `student_N_name`) — исторические, **не** обновляются при переименовании.

## 5. Что ВЫЧИСЛЯЕТСЯ, а не хранится (не ищите этого в данных)

| Величина | Как считается |
|---|---|
| Зарплата педагога | `round(rate × duration_min / 45)`; rate = rate_group (group) / rate_for_teacher (individual). Поле `lessons.earned` всегда 0 |
| Счёт ученика за индивидуальное | `round(rate_for_student × duration_min / 45)` делится поровну между участниками, целый остаток — первому |
| Счёт за per_visit-группу | суммы-снапшоты из `attendees` (amount > 0) |
| Абонемент | группа `subscription` + ≥1 занятие группы в месяце → каждому участнику цена месяца (с учётом overrides); в счетах ключ `SUB:{group_id}` |
| «Занятие оплачено ✅» | существует `student_period_payments` со status=paid для (student, teacher занятия, месяц). У занятия нет поля оплаты |
| Долг | начислено (по формулам выше) − paid-счета, начиная с периода `DEBTORS_SINCE_PERIOD` (env; на проде `2026-07` — раньше оплаты не фиксировались в системе!) |
| Вкладка `billing` | пустая по определению — «строки счёта» всегда вычисляются on-demand |

## 6. Связи (текстовый ER)

```
branches 1─* groups 1─* lessons(group)          users *─1 teachers (опционально)
groups *─* teachers (teacher_groups)            clients 1─* students
groups *─* students (student_groups)            students 1─1 students (partner_id, симметрично)
teachers 1─* lessons                            lessons(individual) *─студенты в слотах 1..4
teachers 1─* teacher_period_submissions         students 1─* student_period_payments
groups 1─* subscription_overrides               (payments.teacher_id: TCH-… или SUB:GRP-…)
```

## 7. Порядок переноса

1. **Выгрузить:** `scripts/export_db_json.py` → `db_export/*.json` (сырые записи как в листах).
2. **Не переносить:** `billing`, `teacher_students(архив)`, `client_invite_codes`,
   колонки `students.group_id` и `students.kindergarten_group`.
3. **Преобразовать:**
   - `parent_tg_ids` → массив int (split по `|`, fallback `,`);
   - `attendees` → дочерняя таблица `(lesson_id, student_id, duration_min, amount)`,
     старый формат: amount=0, duration = duration_min занятия;
   - `payments.teacher_id` вида `SUB:GRP-XXXX` — сохранить как есть либо разнести на
     (kind=subscription, group_id) — это НЕ ссылка на teachers;
   - пустые строки → NULL; `is_admin` → bool по правилу §4.5.
4. **Сохранить обязательно:** читаемые ID, снапшоты имён, оба смысла attendees,
   уникальность (student, teacher, period) в payments.
5. **Готовая целевая SQL-схема** (PostgreSQL/Prisma, с уже разложенными attendees) —
   [MINIAPP_BUILD.md §3](MINIAPP_BUILD.md). Бизнес-правила поверх данных — [BUSINESS_RULES.md](BUSINESS_RULES.md),
   полная доменная спека — [MINIAPP_SPEC.md](MINIAPP_SPEC.md).
6. **Валидация переноса:** сверить счётчики строк со снапшотом §2 (или свежим `_meta.json`);
   контрольные суммы: Σ`total_amount` по payments, Σ`duration_min` по lessons, число занятий
   по (teacher, period) — до и после должны совпасть.
