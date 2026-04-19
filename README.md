# Бот учёта занятий — Школа «Фокус»

Telegram-бот для ведения учёта занятий, расчёта зарплат педагогов и счетов учеников.

---

## Стек

| Компонент | Версия |
|-----------|--------|
| Python | 3.12+ |
| aiogram | 3.13 |
| gspread | 6.1 |
| pydantic-settings | 2.5 |
| Railway | деплой |

---

## Быстрый старт

```bash
# 1. Клонировать репо и создать venv
python3 -m venv .venv && source .venv/bin/activate

# 2. Установить зависимости
pip install -r requirements.txt

# 3. Создать .env (см. .env.example)
cp .env.example .env
# отредактировать .env

# 4. Запустить
python -m bot
```

---

## ENV-переменные

| Переменная | Обязательная | Описание |
|---|---|---|
| `BOT_TOKEN` | ✅ | Токен Telegram-бота (от @BotFather) |
| `GOOGLE_CREDENTIALS_JSON` | ✅ | JSON сервисного аккаунта Google (вся строка) |
| `SPREADSHEET_ID` | ✅ | ID Google Spreadsheet |
| `SHEET_USERS` | — | Имя листа users (по умолчанию: `users`) |
| `SHEET_TEACHERS` | — | Имя листа teachers (по умолчанию: `teachers`) |
| `SHEET_STUDENTS` | — | Имя листа students (по умолчанию: `students`) |
| `SHEET_LESSONS` | — | Имя листа lessons |
| `SHEET_BILLING` | — | Имя листа billing |
| `SHEET_PAYMENTS` | — | Имя листа student_period_payments |

---

## Настройка Google Sheets

1. Создать Google Spreadsheet.
2. Создать сервисный аккаунт в Google Cloud Console → скачать JSON.
3. Дать сервисному аккаунту доступ к таблице (Editor).
4. Содержимое JSON вставить в `GOOGLE_CREDENTIALS_JSON` как одну строку.

### Структура листов

#### users
| user_id | tg_id | is_admin | teacher_id |
|---------|-------|----------|------------|
| USR-0001 | 123456789 | true | TCH-0001 |

#### teachers
| teacher_id | tg_id | name | rate_group | rate_for_teacher | rate_for_student |
|---|---|---|---|---|---|
| TCH-0001 | 123456789 | Иванова А.П. | 500 | 800 | 1000 |

#### students
| student_id | name |
|---|---|
| STU-0001 | Петров Иван |

#### lessons
| lesson_id | teacher_id | teacher_name | type | student_1_id | student_1_name | student_2_id | student_2_name | date | duration_min | earned | recorded_at | updated_at |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| LES-000001 | TCH-0001 | Иванова | individual | STU-0001 | Петров | | | 2024-03-01 | 60 | 1067 | 2024-03-01 12:00:00 | 2024-03-01 12:00:00 |

#### billing
| billing_id | lesson_id | student_id | student_name | teacher_id | teacher_name | date | duration_min | amount | period_month | payment_id | created_at | updated_at |
|---|---|---|---|---|---|---|---|---|---|---|---|---|

#### student_period_payments
| payment_id | student_id | student_name | period_month | total_amount | status | paid_at | confirmed_by_tg_id | comment | created_at | updated_at |
|---|---|---|---|---|---|---|---|---|---|---|

---

## Структура проекта

```
fokus-bot/
├── bot/
│   ├── __main__.py          # точка входа, DI-контейнер
│   ├── handlers/
│   │   ├── common.py        # /start, выбор режима
│   │   ├── admin/           # педагоги, ученики, зарплаты, счета, диагностика
│   │   └── teacher/         # отметить занятие, мои занятия, статистика
│   ├── services/
│   │   ├── lesson_service.py    # создание и удаление занятий
│   │   ├── billing_service.py   # расчёт и создание billing
│   │   ├── payment_service.py   # оплата периода
│   │   └── diagnostics_service.py  # диагностика и пересборка
│   ├── repositories/        # CRUD-обёртки над Google Sheets листами
│   ├── keyboards/           # inline-клавиатуры
│   ├── states/              # FSM-состояния
│   ├── models/              # dataclass-модели и enum
│   ├── middlewares/         # AuthMiddleware
│   └── utils/               # генераторы ID, форматирование дат
├── config/
│   └── settings.py          # pydantic-settings конфиг из .env
├── tests/
├── .env.example
├── requirements.txt
└── Procfile                 # для Railway
```

---

## Формулы расчёта

### Начисление педагогу
```
earned = ставка × (duration_min / 45)

group     → rate_group
individual → rate_for_teacher
```
Количество учеников не влияет на earned.

### Счёт ученика
```
# Один ученик:
amount = rate_for_student × (duration_min / 45)

# Пара:
total = rate_for_student × (duration_min / 45)
amount_1 = ceil(total / 2)   # первый получает лишний рубль при нечётной сумме
amount_2 = total - amount_1  # сумма amount_1 + amount_2 == total точно
```

---

## Роли

| Условие | Поведение /start |
|---|---|
| is_admin=true + teacher_id | Выбор режима: Администратор / Педагог |
| is_admin=true, нет teacher_id | Сразу меню администратора |
| is_admin=false + teacher_id | Сразу меню педагога |
| Нет записи в users | «Вы не зарегистрированы» |

---

## Деплой на Railway

1. Создать новый проект на railway.app.
2. Подключить репозиторий.
3. Добавить все env-переменные в настройки сервиса.
4. Railway подхватит `Procfile` и запустит `python -m bot`.

---

## ID-форматы

| Сущность | Формат | Пример |
|---|---|---|
| Педагог | TCH-XXXX | TCH-0001 |
| Ученик | STU-XXXX | STU-0042 |
| Занятие | LES-XXXXXX | LES-000123 |
| Billing | BIL-XXXXXX | BIL-000456 |
| Оплата | PAY-XXXXXX | PAY-000001 |
