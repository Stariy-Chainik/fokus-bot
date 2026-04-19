# Архитектура fokus-bot

CRM-бот для танцевальной школы "ТСК ФОКУС" на aiogram 3 + Google Sheets.

---

## Стек

| Компонент | Технология |
|-----------|-----------|
| Бот-фреймворк | aiogram 3.13 |
| FSM storage | Redis (prod) / Memory (dev) |
| База данных | Google Sheets (gspread) |
| Конфиг | pydantic-settings (.env) |
| Деплой | Railway (webhook) / Polling (dev) |
| Python | 3.9+ |

---

## Структура проекта

```
bot/
  __main__.py              # Точка входа, DI, запуск polling/webhook
  handlers/
    common.py              # /start, выбор режима admin/teacher
    admin/
      teachers.py          # CRUD педагогов, ставки
      students.py          # CRUD учеников, партнёры
      salaries.py          # Просмотр зарплат
      bills.py             # Счета учеников, подтверждение оплаты
      branches.py          # Филиалы, группы, рассылка счетов
      edit_lesson.py       # Редактирование занятий педагога
      diagnostics.py       # Проверка целостности
    teacher/
      record_lesson.py     # Запись занятия (FSM)
      my_lessons.py        # Просмотр/удаление занятий
      my_stats.py          # Статистика за период
      my_groups.py         # Состав групп
      partners.py          # Пары и ученики
      submit_period.py     # Сдача периода
  keyboards/
    admin.py               # Inline-клавиатуры админа
    teacher.py             # Inline-клавиатуры педагога
    calendar.py            # Универсальный inline-календарь
    common.py              # Общие (выбор режима)
  middlewares/
    auth.py                # Загрузка User по tg_id
    dedup.py               # Дедупликация update-ов (TTL 60s)
  models/
    entities.py            # Dataclass-модели (User, Teacher, Student, ...)
    enums.py               # LessonType, PaymentStatus
  repositories/            # Google Sheets CRUD (BaseRepository с TTL-кешем 300s)
    sheets_client.py       # gspread-клиент
    base.py                # Базовый репозиторий
    user_repo.py
    teacher_repo.py
    student_repo.py
    lesson_repo.py
    payment_repo.py
    teacher_period_submission_repo.py
    branch_repo.py
    group_repo.py
    teacher_group_repo.py      # many-to-many педагог<->группа
  services/
    lesson_service.py      # Создание/удаление занятий
    billing_service.py     # Чистые функции: calc_earned, build_billing_rows
    payment_service.py     # On-demand счета, подтверждение оплаты
    diagnostics_service.py # Проверка целостности ссылок
    visibility.py          # Видимость педагог→ученик через группы
  states/
    admin_states.py        # FSM-состояния админских flow
    teacher_states.py      # FSM-состояния педагога
    lesson_states.py       # FSM записи занятия и сдачи периода
  utils/
    ids.py                 # Генераторы ID (TCH-0001, STU-0001, ...)
    dates.py               # Форматирование дат
    lesson_stats.py        # Форматирование статистики
config/
  settings.py              # pydantic Settings из .env
```

---

## Модели данных

```
User (users)
  user_id     USR-NNNN
  tg_id       int (Telegram ID)
  is_admin    bool
  teacher_id  TCH-NNNN | null

Teacher (teachers)
  teacher_id       TCH-NNNN
  tg_id            int | null
  name             "Фамилия Имя"
  rate_group       руб/45мин (групповое)
  rate_for_teacher руб/45мин (индивидуальное, зарплата)
  rate_for_student руб/45мин (индивидуальное, счёт ученику)

Student (students)
  student_id  STU-NNNN
  name        "Фамилия Имя"
  partner_id  STU-NNNN | null (двусторонняя связь)
  group_id    GRP-NNNN | "" (одна группа)

Branch (branches)
  branch_id   BRN-NNNN
  name        "Название филиала"

Group (groups)
  group_id    GRP-NNNN
  branch_id   BRN-NNNN
  name        "Пн/Ср 18:00 Начинающие"

Lesson (lessons)
  lesson_id       LES-NNNNNN
  teacher_id      TCH-NNNN
  type            GROUP | INDIVIDUAL
  student_1_id    STU-NNNN | null
  student_2_id    STU-NNNN | null (пара)
  date            YYYY-MM-DD
  duration_min    35 | 45 | 60 | 90
  attendees       "STU-0001,STU-0002,..." (для GROUP)
  group_id        GRP-NNNN | ""

TeacherPeriodSubmission (teacher_period_submissions)
  submission_id   SUB-NNNNNN
  teacher_id      TCH-NNNN
  period_month    YYYY-MM
  lessons_count   int
  total_earned    int

StudentPeriodPayment (student_period_payments)
  payment_id      PAY-NNNNNN
  student_id      STU-NNNN
  period_month    YYYY-MM
  teacher_id      TCH-NNNN (один счёт на педагога)
  total_amount    int
  status          PENDING | PAID
```

### Связи

```
Branch 1───* Group
Group  1───* Student (через student.group_id)
Group  *───* Teacher (через teacher_groups)
Teacher *───* Student (деривативно: student.group_id ∈ teacher.groups)
Student 1───1 Student (partner_id, двусторонний)
Teacher 1───* Lesson
Lesson  *───1 Student (student_1)
Lesson  *───1 Student (student_2, опционально)
Lesson  *───1 Group (для GROUP-занятий)
Teacher 1───* TeacherPeriodSubmission
Student 1───* StudentPeriodPayment
```

---

## Middleware pipeline

```
Telegram Update
  |
  v
DedupUpdateMiddleware      -- отсекает дубли (TTL 60s)
  |
  v
AuthMiddleware             -- загружает User из users по tg_id
  |
  v
Router (common / admin / teacher)
  |
  v
Handler (получает user, repos, services через DI)
```

---

## DI (Dependency Injection)

Все зависимости регистрируются в `__main__.py` через `dp[key] = value`:

```
Repositories:  user_repo, teacher_repo, student_repo,
               lesson_repo, payment_repo, submission_repo,
               branch_repo, group_repo, teacher_group_repo,
               student_request_repo

Services:      lesson_service, payment_service, diagnostics_service,
               visibility (TeacherVisibilityService)

Other:         student_requests (in-memory dict)
```

aiogram автоматически инжектит их в хендлеры по имени параметра.

---

## Flow: /start (авторизация)

```
Пользователь -> /start
  |
  AuthMiddleware загружает User по tg_id
  |
  User найден?
  +-- Нет -> создаётся User (USR-NNNN)
  |          Если tg_id совпадает с педагогом -> привязка teacher_id
  |          Если user - администратор -> показать admin menu
  |          Иначе -> "Добро пожаловать!"
  |
  +-- Да, is_admin + teacher_id -> "Выбери режим" [Администратор] [Педагог]
  +-- Да, is_admin only        -> admin menu
  +-- Да, teacher_id only      -> teacher menu
  +-- Да, ни то ни другое      -> "Вы зарегистрированы"
```

---

## Flow: Главное меню Администратора

```
[Администратор]
  |
  +-- [Педагоги]                -> teachers.py
  |     +-- Список педагогов (со статусом сдачи 🟢/🔴)
  |     +-- Добавить педагога (FSM: tg_id -> имя -> 3 ставки)
  |     +-- Удалить педагога
  |     +-- Изменить ставки
  |
  +-- [Ученики]                 -> students.py
  |     +-- Список/поиск учеников (* = все)
  |     +-- Добавить ученика (FSM: имя -> филиал -> группа)
  |     +-- Удалить ученика
  |     +-- Все пары
  |     +-- Все солисты
  |     +-- Карточка ученика -> партнёр (назначить/убрать)
  |
  +-- [Зарплаты]                -> salaries.py
  |     +-- Выбор педагога -> период -> расчёт из lessons
  |        (earned = rate_for_teacher * duration / 45)
  |        Статус: "Сдан педагогом" / "Открыт"
  |
  +-- [Счёт ученика за период]  -> bills.py
  |     +-- Период -> Филиал -> Группа -> Ученик -> Карточка счёта
  |        Карточка: по каждому педагогу — сумма, детализация, статус
  |        (amount = rate_for_student * duration / 45)
  |        Кнопка "Отправить родителю" (проверка сдачи всех педагогов)
  |
  +-- [Подтвердить оплату]      -> bills.py
  |     +-- Период -> Филиал -> Группа -> Ученик -> Список счетов
  |        Нажатие на неоплаченный -> подтверждение -> статус PAID
  |
  +-- [Редактировать занятие]   -> edit_lesson.py
  |     +-- Педагог -> Фильтр даты -> Список занятий -> Карточка
  |        (Сегодня / Вчера / Календарь / Месяц / Все)
  |
  +-- [Филиалы и группы]        -> branches.py
  |     +-- CRUD филиалов
  |     +-- CRUD групп (в филиале)
  |     +-- Педагоги группы (чекбоксы)
  |     +-- Ученики группы (чекбоксы)
  |     +-- Разослать счета группе (проверка сдачи всех педагогов)
  |
  +-- [Диагностика]             -> diagnostics.py
        +-- Проверка целостности: занятия с удалёнными педагогами/учениками
```

---

## Flow: Главное меню Педагога

```
[Педагог]
  |
  +-- [Отметить занятие]        -> record_lesson.py (FSM)
  |     (см. подробный flow ниже)
  |
  +-- [Мои занятия]             -> my_lessons.py
  |     +-- Добавить (-> record_lesson)
  |     +-- Посмотреть (фильтр даты -> список -> карточка)
  |     +-- Удалить (фильтр даты -> список без сданных -> удаление)
  |
  +-- [Мои группы]              -> my_groups.py
  |     +-- Список групп педагога (по филиалам)
  |     +-- Карточка группы -> редактировать состав учеников
  |
  +-- [Мои ученики]             -> partners.py
  |     +-- Солисты (добавить/убрать ученика из своего списка)
  |     +-- Пары (создать пару / разбить пару)
  |     +-- Карточка ученика/пары
  |
  +-- [Статистика]              -> my_stats.py
  |     +-- Период -> сводка (всего, групповых, индивидуальных, earned)
  |
  +-- [Сдать период]            -> submit_period.py
        +-- Текущий месяц или выбор другого
        +-- Превью: кол-во занятий, earned
        +-- Подтверждение -> запись TeacherPeriodSubmission
        +-- После сдачи: занятия периода заблокированы (нельзя удалить)
```

---

## Flow: Запись занятия (FSM)

```
[Отметить занятие]
  |
  v
Выбор даты
  Сегодня / Вчера / Календарь
  |
  v
Выбор типа
  [Групповое] [В паре] [Солист]
  |
  v
Выбор длительности
  [35] [45] [60] [90] мин
  |
  +-- Групповое:
  |     Если группы в разных филиалах -> выбор филиала
  |     Выбор группы -> "Отметить присутствующих?"
  |     Да -> чекбоксы учеников группы -> подтвердить
  |     Нет -> занятие без отметки
  |     => 1 занятие (type=GROUP, group_id, attendees=CSV)
  |
  +-- В паре:
  |     Мульти-выбор пар из списка педагога
  |     (показываются только ученики с партнёром)
  |     => N занятий (type=INDIVIDUAL, student_1 + student_2)
  |
  +-- Солист:
        Мульти-выбор учеников (чекбоксы, "Выбрать всех")
        => N занятий (type=INDIVIDUAL, student_1 only)
```

---

## Flow: Счёт ученика (on-demand billing)

```
Админ: Счёт ученика за период
  |
  v
Выбор периода (6 месяцев назад)
  |
  v
Выбор филиала (или "Без группы")
  |
  v
Выбор группы
  |
  v
Выбор ученика
  |
  v
On-demand расчёт из таблицы lessons:
  1. Найти все INDIVIDUAL-занятия ученика за период
  2. Сгруппировать по teacher_id
  3. Для каждого: amount = rate_for_student * duration / 45
  4. Проверить submissions -> статус педагога
  |
  v
Карточка счёта:
  Педагог 1 — 1200 руб. — Период не сдан / Ожидает / Оплачен
    12.04 | 45 мин | 600 руб.
    14.04 | 45 мин | 600 руб.
  Педагог 2 — 800 руб. — ...
  Итого: 2000 руб.
  |
  [Отправить родителю] -> проверка: все ли педагоги сдали период
    Не все -> popup "Не сдан: Иванов, Петров"
    Все сдали -> создание invoices (заглушка отправки)
```

---

## Flow: Зарплата педагога

```
Админ: Зарплаты -> Педагог -> Период
  |
  v
Из lessons: все занятия педагога за период
  earned = sum(rate_for_teacher * duration / 45) для INDIVIDUAL
         + sum(rate_group * duration / 45)       для GROUP
  |
  v
Карточка зарплаты:
  Всего: 15 занятий
  Групповых: 10 (по 800 руб.)
  Индивидуальных: 5 (по 1200 руб.)
  Итого: 14 000 руб.
  Статус: Сдан педагогом / Открыт
```

---

## Flow: Сдача периода

```
Педагог: Сдать период
  |
  v
Текущий месяц (или выбор другого)
  |
  v
Превью:
  Занятий: 15
  Заработано: 14 000 руб.
  |
  [Подтвердить]
  |
  v
Создаётся TeacherPeriodSubmission
  -> занятия за этот период блокируются (нельзя удалить)
  -> в списке педагогов у админа появляется 🟢
  -> при просмотре счёта: статус "период сдан"
```

---

## Flow: Подтверждение оплаты

```
Админ: Подтвердить оплату
  |
  Период -> Филиал -> Группа -> Ученик
  |
  v
Список счетов по педагогам:
  ⏳ Иванов — 1200 руб.    (нажми для подтверждения)
  ✅ Петров — 800 руб.      (уже оплачен)
  |
  v
Нажатие на неоплаченный -> "Подтвердить оплату?"
  |
  [Подтвердить]
  -> статус PAID, сохраняется paid_at и confirmed_by_tg_id
```

---

## Flow: Филиалы и группы

```
Админ: Филиалы и группы
  |
  v
Список филиалов:
  🏢 Центральный
  🏢 На Ленина
  [Создать филиал]
  |
  v (нажатие на филиал)
Карточка филиала:
  Группы:
    💃 Пн/Ср 18:00 Начинающие
    💃 Вт/Чт 19:00 Продолжающие
  [Создать группу]
  [Переименовать]
  [Удалить] (только если нет групп)
  |
  v (нажатие на группу)
Карточка группы:
  Педагоги: Иванов, Петров
  Ученики:
    * Сидорова
    * Козлов
  [Педагоги группы] -> чекбоксы
  [Ученики группы] -> чекбоксы
  [Разослать счета группе] -> проверка submissions
  [Переименовать]
  [Удалить]
```

---

## Расчёт стоимости (формулы)

### Зарплата педагога (earned)
```
earned = rate * duration_min / 45

rate = rate_group       (если type == GROUP)
       rate_for_teacher (если type == INDIVIDUAL)
```

### Счёт ученику (amount)
```
amount = rate_for_student * duration_min / 45
```
Только для INDIVIDUAL-занятий. GROUP не выставляется в счёт.

Для пар: сумма делится на двоих (первый получает нечётный рубль).

---

## Google Sheets (11 вкладок)

| Вкладка | Ключ | Описание |
|---------|------|----------|
| users | user_id | Telegram-аккаунты, привязка admin/teacher |
| teachers | teacher_id | Педагоги и ставки |
| students | student_id | Ученики, group_id, partner_id |
| lessons | lesson_id | Все записанные занятия |
| billing | - | Не используется (legacy, on-demand) |
| student_period_payments | payment_id | Счета учеников |
| teacher_period_submissions | submission_id | Записи сдачи периода |
| branches | branch_id | Филиалы |
| groups | group_id | Тренировочные группы |
| teacher_groups | teacher_id + group_id | Связь педагог-группа |

Кеш: BaseRepository хранит кеш всех строк с TTL 300 секунд.

---

## Блокировки и защиты

| Защита | Где | Как |
|--------|-----|-----|
| Дубли update | DedupMiddleware | TTL 60s по (chat_id, message_id) |
| Двойной клик "Подтвердить оплату" | bills.py | `_confirming_in_progress: set` |
| Двойной клик "Отправить родителю" | bills.py | `_sending_in_progress: set` |
| Двойной клик "Разослать группе" | branches.py | `_group_send_in_progress: set` |
| Удаление занятия из сданного периода | my_lessons.py | Проверка submissions, `PermissionError` |
| Админ может удалить любое | lesson_service | `bypass_period_lock=True` |
