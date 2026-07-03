# fokus-bot → Web (Telegram Mini App): build-спецификация

> **Назначение.** Технический план для сборки веб-версии CRM. Домен и правила — в
> [MINIAPP_SPEC.md](MINIAPP_SPEC.md) (обязательно к прочтению, это источник правды по логике).
> Здесь — **стек, схема БД, аутентификация, доменный слой, API-контракт, структура и пошаговый план**.
>
> Формат рассчитан на то, чтобы отдать его кодовому агенту/разработчику и собирать приложение **по фазам**
> (см. §8). Не «сделай всё за раз», а фаза за фазой с проверкой.

---

## 1. Стек (выбран)

| Слой | Технология | Почему |
|---|---|---|
| Фронт + бэк | **Next.js 14 (App Router) + TypeScript** | один репозиторий: страницы (React) и API-роуты вместе; SSR; деплой в один клик |
| UI | **Tailwind CSS** + Telegram theme vars | лёгкие адаптивные экраны в стиле Telegram |
| Telegram | **`@telegram-apps/sdk`** (+ `@telegram-apps/sdk-react`) | initData, тема, кнопки Mini App |
| БД | **PostgreSQL** | транзакции снимают костыли Google Sheets (гонки, `|`-разделитель, лимиты) |
| ORM | **Prisma** | типобезопасная схема + миграции |
| Валидация | **Zod** | схемы запросов/ответов API |
| Тесты домена | **Vitest** | порт характеризующих тестов формул |
| Платежи | **ЮКасса SDK** + webhook | как в боте |

> Бэкенд бота остаётся на Python (aiogram) — они могут работать параллельно поверх **одной БД**
> (бот пишет/читает Postgres напрямую) на время миграции, либо бот выводится из эксплуатации после переноса.

---

## 2. Архитектура (зеркалит слоистость бота)

```
Текущий бот (Python)                 Веб (Next.js/TS)
─────────────────────                ─────────────────────
models/ (dataclasses)         →      prisma/schema.prisma (+ типы Prisma)
repositories/ (Sheets)        →      lib/db/*  (Prisma-запросы)
services/ (бизнес-логика)     →      lib/domain/* (чистые ф-ции) + app/api/* (оркестрация)
handlers/ (aiogram FSM)       →      app/api/* (REST) + app/(mini)/* (React-страницы)
keyboards/ (InlineKeyboard)   →      React-компоненты
middlewares/auth              →      lib/auth (валидация initData) + middleware.ts
```

**Ключевой принцип:** вся денежная/доменная логика — в `lib/domain/*` как **чистые функции** (без БД),
портированные из [billing_service.py](../bot/services/billing_service.py) / [visibility.py](../bot/services/visibility.py)
**вместе с тестами**. API-роуты только читают БД → зовут домен → пишут БД.

---

## 3. Схема БД (Prisma) — точный перенос модели

> Соответствует [bot/models/entities.py](../bot/models/entities.py). Читаемые ID (`TCH-xxxx`) сохранены как
> первичные ключи (§5 спеки). **Изменение против бота:** поле `Lesson.attendees` (CSV) нормализовано в
> таблицу `LessonAttendee` — чище и без парсинга; правило `amount=0 = абонемент` сохраняется.

```prisma
// prisma/schema.prisma
generator client { provider = "prisma-client-js" }
datasource db { provider = "postgresql"; url = env("DATABASE_URL") }

enum LessonType        { GROUP INDIVIDUAL }
enum PaymentStatus     { PENDING PAID }
enum RequestStatus     { PENDING APPROVED REJECTED }
enum GroupBillingMode  { NONE PER_VISIT SUBSCRIPTION }   // SUBSCRIPTION зарезервирован
enum StudentGroupTier  { FULL SHORT }

model User {
  userId    String  @id           // USR-xxxx
  tgId      BigInt  @unique
  isAdmin   Boolean @default(false)
  teacherId String?
  teacher   Teacher? @relation(fields: [teacherId], references: [teacherId])
}

model Teacher {
  teacherId      String  @id       // TCH-xxxx
  tgId           BigInt?
  name           String
  rateGroup      Int               // ₽ за 45 мин, групповое
  rateForTeacher Int               // ₽ за 45 мин, индивидуальное (в зарплату)
  rateForStudent Int               // ₽ за 45 мин, в счёт ученика
  canProxyFor    String[] @default([])  // вместо хардкода PROXY_BUTTONS (§10 спеки)
  canBill        Boolean  @default(false) // вместо BILLING_TEACHERS
  users          User[]
  groups         TeacherGroup[]
  lessons        Lesson[]
}

model Client {
  clientId  String   @id           // CLT-xxxx
  name      String
  tgId      BigInt?
  phone     String?
  createdAt DateTime @default(now())
  students  Student[]
}

model Student {
  studentId    String  @id         // STU-xxxx
  name         String
  partnerId    String? @unique
  partner      Student? @relation("Partners", fields: [partnerId], references: [studentId])
  partnerOf    Student? @relation("Partners")
  groupTier    StudentGroupTier @default(FULL)
  clientId     String?
  client       Client?  @relation(fields: [clientId], references: [clientId])
  parentTgIds  BigInt[] @default([])   // доступ в приложение; запрос: tgId = ANY(parentTgIds)
  groups       StudentGroup[]
}

model Branch {
  branchId  String  @id            // BRN-xxxx
  name      String
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
  groups    Group[]
}

model Group {
  groupId       String  @id        // GRP-xxxx
  branchId      String
  branch        Branch  @relation(fields: [branchId], references: [branchId])
  name          String
  sortOrder     Int     @default(0)
  billingMode   GroupBillingMode @default(NONE)
  priceShort    Int     @default(0)
  durationShort Int     @default(35)
  priceFull     Int     @default(0)
  durationFull  Int     @default(60)
  createdAt     DateTime @default(now())
  updatedAt     DateTime @updatedAt
  teachers      TeacherGroup[]
  students      StudentGroup[]
}

model TeacherGroup {
  teacherId String
  groupId   String
  teacher   Teacher @relation(fields: [teacherId], references: [teacherId])
  group     Group   @relation(fields: [groupId], references: [groupId])
  @@id([teacherId, groupId])
}

model StudentGroup {
  studentId String
  groupId   String
  student   Student @relation(fields: [studentId], references: [studentId])
  group     Group   @relation(fields: [groupId], references: [groupId])
  @@id([studentId, groupId])
}

model Lesson {
  lessonId    String     @id       // LES-xxxxxx
  teacherId   String
  teacher     Teacher    @relation(fields: [teacherId], references: [teacherId])
  teacherName String                // снапшот на момент записи (денормализация — намеренно)
  type        LessonType
  // INDIVIDUAL: 1–4 ученика в слотах; снапшоты имён
  student1Id  String?
  student1Name String?
  student2Id  String?
  student2Name String?
  student3Id  String?
  student3Name String?
  student4Id  String?
  student4Name String?
  date        String                // "YYYY-MM-DD" — строкой, period = date[:7] (без tz-багов)
  durationMin Int
  groupId     String?               // только для GROUP
  recordedAt  DateTime @default(now())
  updatedAt   DateTime @updatedAt
  attendees   LessonAttendee[]      // только для GROUP; замена CSV-поля attendees
  @@index([teacherId, date])
  @@index([date])                   // выборки по периоду (prefix "YYYY-MM")
  // «занятия ученика» = OR по student1..4Id + join LessonAttendee; при ~5k строк/год (§3.1)
  // отдельные индексы на слоты не нужны — объём мал.
}

// Нормализованная замена CSV-поля Lesson.attendees (только групповые).
model LessonAttendee {
  id          String  @id @default(cuid())
  lessonId    String
  lesson      Lesson  @relation(fields: [lessonId], references: [lessonId], onDelete: Cascade)
  studentId   String
  durationMin Int
  amount      Int                   // ₽-снапшот; 0 = абонемент (в счёт не входит)
  @@unique([lessonId, studentId])
  @@index([studentId])              // счёт ученика за период (join с Lesson по date)
}

model StudentPeriodPayment {
  paymentId       String   @id      // PAY-xxxxxx
  studentId       String
  studentName     String
  teacherId       String
  teacherName     String
  periodMonth     String            // "YYYY-MM"
  totalAmount     Int
  status          PaymentStatus @default(PENDING)
  paidAt          DateTime?
  confirmedByTgId BigInt?
  comment         String?
  createdAt       DateTime @default(now())
  updatedAt       DateTime @updatedAt
  @@unique([studentId, teacherId, periodMonth])   // один счёт на (student,teacher,period)
}

// Внешний онлайн-платёж (ЮКасса) — для верификации webhook и сверки (§6.1).
// В боте не хранился (см. FOUND_BUGS.md B4) — в веб-версии обязателен.
model ExternalPayment {
  id          String   @id @default(cuid())
  provider    String   @default("yookassa")
  externalId  String   @unique          // payment.id ЮКассы
  studentId   String
  periodMonth String                    // "YYYY-MM"
  amount      Int                       // ₽; сверяется с webhook перед подтверждением
  status      String   @default("pending") // pending|succeeded|canceled (зеркало провайдера)
  createdAt   DateTime @default(now())
  updatedAt   DateTime @updatedAt
  @@index([studentId, periodMonth])
}

// Загруженный чек (реквизиты/СБП) — только для UI-состояния «ждёт подтверждения» (§6.1).
model Receipt {
  id          String   @id @default(cuid())
  studentId   String
  periodMonth String
  method      String                    // bank|sbp
  fileId      String                    // telegram file_id или путь в сторадже
  uploadedBy  BigInt                    // tgId родителя
  uploadedAt  DateTime @default(now())
  @@index([studentId, periodMonth])
}

model TeacherPeriodSubmission {
  submissionId String   @id         // SUB-xxxxxx
  teacherId    String
  periodMonth  String               // "YYYY-MM"
  submittedAt  DateTime @default(now())
  lessonsCount Int
  totalEarned  Int
  @@unique([teacherId, periodMonth])  // наличие строки = период заблокирован
}

model StudentRequest {
  requestId      String   @id       // (req-...)
  teacherId      String
  teacherTgId    BigInt
  teacherName    String
  studentName    String
  groupId        String
  status         RequestStatus @default(PENDING)
  createdAt      DateTime @default(now())
  resolvedAt     DateTime?
  resolvedByTgId BigInt?
  adminMsgs      Json?              // [[chatId, messageId], ...]
}
```

> **Billing НЕ таблица** — счёт ученика и зарплата вычисляются на лету (§4). ID вида `PAY-000042`
> генерируются функцией «max+1» в транзакции (см. `lib/db/ids.ts`).

### 3.1 Объём данных: решение по `lessons`

Замер продакшена (июль 2026): **1 273 занятия всего, темп ~424/мес ≈ 5 000/год**.

**Решение для Postgres: одна таблица `lessons`, навсегда, без архивирования и партиционирования.**
- 5 000 строк/год → 50 000 за 10 лет — для Postgres с индексами это пренебрежимо мало
  (партиционирование имеет смысл от десятков миллионов строк).
- Индексы под реальные запросы: `@@index([teacherId, date])` (занятия педагога/период),
  добавить `@@index([student1Id, date])` и индекс по `LessonAttendee.studentId`
  (счёт ученика за период). Выборка по периоду — префикс `date LIKE 'YYYY-MM%'`, btree работает.
- **Историю не удалять и не «сворачивать»**: снапшоты имён делают старые строки самодостаточными,
  а счета/зарплаты вычисляются on-demand из занятий — усечение истории сломало бы пересчёт.
- UI всегда фильтрует по периоду/педагогу/ученику — экрана «все занятия за всё время» нет,
  поэтому и на фронт большие выборки не попадают.

**Текущий бот на Google Sheets (до миграции): ничего не делать сейчас.** Болевой порог полного
чтения листа (`get_all_values`) — ориентировочно 15–20 тыс. строк, при текущем темпе это ~3 года.
Если миграция задержится и чтение замедлится — плейбук: годовые архивные листы (`lessons_2025`),
активный лист держит только текущий год. ⚠️ При архивировании учесть генератор ID
(`LES-xxxxxx` = max+1 по активному листу): после переноса строк максимум «обнулится» и новые ID
столкнутся с архивными — нужно либо сканировать оба листа, либо хранить счётчик отдельно.

---

## 4. Доменный слой (порт чистых функций + тесты)

`lib/domain/` — перенос [billing_service.py](../bot/services/billing_service.py) и
[visibility.py](../bot/services/visibility.py). **Перенести и тесты** (Vitest) из `tests/` — они эталон.

```ts
// lib/domain/billing.ts
export const MINUTES_PER_UNIT = 45;

// ⚠️ Python round() — банковское округление. Аргумент здесь всегда x/45 и НЕ бывает ровно .5,
// поэтому Math.round совпадает. Если появятся дробные цены/другой делитель — вернуть banker's round.
export function calcEarned(type: "group" | "individual", durationMin: number, t: Teacher): number {
  const rate = type === "group" ? t.rateGroup : t.rateForTeacher;
  return Math.round((rate * durationMin) / MINUTES_PER_UNIT);
}

// Индивидуальное: base делится ПОРОВНУ, целый остаток — первому участнику.
export function individualStudentAmounts(
  rateForStudent: number, durationMin: number, participantIds: string[]
): Record<string, number> {
  const base = Math.round((rateForStudent * durationMin) / MINUTES_PER_UNIT);
  const n = participantIds.length;
  const per = Math.floor(base / n);
  const remainder = base - per * n;
  const out: Record<string, number> = {};
  participantIds.forEach((sid, i) => { out[sid] = per + (i === 0 ? remainder : 0); });
  return out;
}

// Групповое per_visit: суммы = снапшоты из LessonAttendee, где amount > 0 (0 = абонемент).
```

```ts
// lib/domain/visibility.ts
// Педагог видит ученика ⇔ (teacher_groups ∩ student_groups) ≠ ∅.  Прямой связи teacher↔student НЕТ.
export function isVisible(teacherGroupIds: Set<string>, studentGroupIds: string[]): boolean {
  return studentGroupIds.some((g) => teacherGroupIds.has(g));
}
```

**Правила, которые нельзя переизобретать** (полный список — §12 спеки): формула 45 мин; деление поровну с
остатком первому; `amount=0=абонемент`; один инвойс на `(student,teacher,period)`; «оплачено» вычисляется по
`(period,teacher)`, не хранится; блокировка периода + обход админом + сдача с 25-го; запрет будущей даты;
гард дублей соло; денормализация снапшотов имён.

---

## 5. Аутентификация Telegram Mini App

Критично для безопасности. Клиент шлёт `initData` (подписанная строка от Telegram); сервер её **проверяет**.

```ts
// lib/auth/verifyInitData.ts
import { createHmac } from "crypto";

export function verifyInitData(initData: string, botToken: string, maxAgeSec = 86400) {
  const params = new URLSearchParams(initData);
  const hash = params.get("hash"); params.delete("hash");
  const dataCheckString = [...params.entries()]
    .map(([k, v]) => `${k}=${v}`).sort().join("\n");
  const secret = createHmac("sha256", "WebAppData").update(botToken).digest();
  const computed = createHmac("sha256", secret).update(dataCheckString).digest("hex");
  if (computed !== hash) throw new Error("bad initData signature");
  const authDate = Number(params.get("auth_date"));
  if (Date.now() / 1000 - authDate > maxAgeSec) throw new Error("initData expired");
  return JSON.parse(params.get("user")!) as { id: number; first_name: string; username?: string };
}
```

**Резолв роли** после проверки: `tgId` →
1. `User` с этим `tgId` и `isAdmin` → **admin** (+teacher, если `teacherId`);
2. `User`/`Teacher` с `teacherId` → **teacher**;
3. есть `Student`, где `tgId = ANY(parentTgIds)` → **client**;
4. иначе — гость (экран регистрации клиента).

Сессия: подписанный JWT-cookie с `{tgId, role, teacherId?}`; `middleware.ts` защищает `/api/*` и страницы.
Каждый чувствительный эндпоинт **дополнительно** перепроверяет право (видимость группы, `canBill` и т.д.).

---

## 6. API-контракт (REST, по ресурсам)

Все под `/api`, JSON, роль проверяется на сервере. Ниже — карта (не весь I/O; ключевые — детально).

**Auth**
- `POST /api/auth/telegram` `{ initData }` → `{ role, teacherId? }` (ставит cookie).

**Teachers** (admin)
- `GET /teachers` · `GET /teachers/:id` (карточка: ставки, группы, история сдач) · `POST /teachers`
- `PATCH /teachers/:id/rates` · `PATCH /teachers/:id/groups` · `DELETE /teachers/:id`
- `POST /teachers/:id/reopen-period` `{ periodMonth }` (удаляет submission)

**Students** (admin; teacher — только видимые)
- `GET /students?q=&page=` · `GET /students/:id` (карточка-DTO: группы/партнёр/клиент/тариф)
- `POST /students` · `PATCH /students/:id` (rename) · `DELETE /students/:id`
- `PATCH /students/:id/partner` `{ partnerId|null }` (симметрично, транзакция)
- `PATCH /students/:id/tier` · `PATCH /students/:id/client` `{ clientId|null }`

**Branches / Groups** (admin)
- CRUD `/branches`, `/groups`; `PATCH /groups/:id/billing`; `POST|DELETE /groups/:id/teachers`,
  `/groups/:id/students`; `POST /groups/:id/send-bills` `{ periodMonth }`.

**Lessons** — центральный эндпоинт записи:
- `POST /lessons` — тело зависит от `kind`:
  ```jsonc
  // kind=group    → одно GROUP-занятие (+attendees со снапшотом для per_visit)
  { "kind":"group","groupId":"GRP-0017","date":"2026-07-01","durationMin":60,
    "attendees":[{"studentId":"STU-1","tier":"full"}] }
  // kind=pair     → по одной INDIVIDUAL-записи на пару
  // kind=soloist  → по одной INDIVIDUAL-записи на солиста
  // kind=shared   → одно INDIVIDUAL на 2–4 солистов (слоты сортируются по studentId)
  { "kind":"shared","date":"2026-07-01","durationMin":60,"studentIds":["STU-2","STU-3"] }
  ```
  Сервер: проверяет `date ≤ today`; гард дублей соло; блокировку периода (если не admin); пишет снапшоты имён.
- `GET /lessons?teacherId=&studentId=&period=&date=` (фильтры) · `DELETE /lessons/:id` (блокировка/обход).

**Periods** (teacher)
- `GET /periods/:teacherId/:month/preview` → `{ lessons, totalEarned }` (сумма `calcEarned`).
- `POST /periods/submit` `{ teacherId, periodMonth }` (только с 25-го; создаёт submission-замок).

**Bills / Payments**
- `GET /students/:id/bills/:period` → счёт, сгруппированный по педагогам (`compute`, on-demand).
- `POST /payments/invoices` `{ studentId, periodMonth }` → get-or-create инвойсы (обновляет только не-PAID).
- `POST /payments/:id/confirm` (одиночный) · `POST /periods/:studentId/:month/confirm` (все PENDING→PAID + фискализация).
- `POST /payments/yookassa` `{ studentId, periodMonth }` → `{ confirmationUrl, externalPaymentId }`.
  **Сумму клиент НЕ передаёт** — сервер вычисляет её из неоплаченных инвойсов (§6.1).
- `POST /webhooks/yookassa` — с обязательной перепроверкой через API ЮКассы (§6.1).
- `GET /payments/external/:id/status` → `{ status }` — кнопка «проверить ещё раз» (§6.1).

**Прочее** (admin): `GET /salaries?period=`, `GET /profit?period=`, `GET /requests` +
`POST /requests/:id/approve|reject`, `GET /diagnostics`.

**Client**: `GET /me/children`, `GET /me/lessons?...`, `GET /me/bills?...`, `POST /me/add-child` (заявка),
`POST /me/pay/...`.

### 6.1 Проверка оплат на стороне клиента (решение)

**Принцип: фронт никогда не решает, оплачено ли, — он только отображает статус с сервера.**
`PAID` ставится ровно двумя путями: (а) верифицированный webhook ЮКассы, (б) подтверждение
админа (наличные / реквизиты / СБП). Всё остальное — отображение.

**Поток онлайн-оплаты (ЮКасса):**
```
1. Клиент жмёт «Оплатить» → POST /payments/yookassa { studentId, periodMonth }
   Сервер: проверяет initData-сессию и что studentId принадлежит родителю (tgId ∈ parentTgIds);
   ВЫЧИСЛЯЕТ сумму из PENDING-инвойсов (от клиента сумму не принимает);
   создаёт платёж в ЮКассе (idempotency key = uuid) и СОХРАНЯЕТ ExternalPayment
   (externalId ЮКассы + studentId + periodMonth + amount); отдаёт confirmationUrl.
2. Фронт открывает confirmationUrl; после оплаты пользователь возвращается по return_url.
3. Фронт показывает «Проверяем оплату…» и поллит GET /students/:id/bills/:period
   каждые 2–3 с, до ~90 с. (Поллинг, не WebSocket/SSE: сессии Mini App короткие,
   лёгкий запрос раз в 2–3 с в течение минуты — простейшее надёжное решение.)
4. Параллельно приходит webhook: сервер НЕ верит телу запроса — берёт object.id,
   запрашивает статус у API ЮКассы (GET /payments/{id}) и подтверждает период только
   при реальном `succeeded` и совпадении суммы с ExternalPayment. Повторные webhook
   безопасны: confirm_period идемпотентен (PAID пропускается).
5. Поллинг видит PAID → фронт показывает ✅.
6. Если за 90 с статус не сменился: «Платёж обрабатывается, статус обновится автоматически»
   + кнопка «Проверить ещё раз» → GET /payments/external/:id/status (сервер сам опрашивает
   ЮКассу по сохранённому externalId — страховка от потерянного webhook).
```

**Ручные способы (наличные / реквизиты / СБП):** статус остаётся PENDING до подтверждения
админом (как в боте). Для UX хранить факт загрузки чека (модель `Receipt`: studentId, period,
fileId, uploadedAt) и показывать промежуточное состояние «чек отправлен, ждёт подтверждения» —
домен (PENDING/PAID) не меняется, это чисто отображение.

**✅ на занятиях** — как в боте: вычисляется на сервере (`(period, teacher)` имеет PAID-инвойс),
фронт только рендерит.

**Чек-лист безопасности платежей:**
- сумма — только серверный расчёт; от клиента не принимается;
- принадлежность ученика родителю проверяется на каждом платёжном эндпоинте;
- webhook перепроверяется через API ЮКассы (в боте уже реализовано —
  [payments.py: process_yookassa_event](../bot/handlers/client/payments.py), см. историю в
  [FOUND_BUGS.md B4](FOUND_BUGS.md); в веб-версии портировать 1-в-1);
- idempotency key при создании платежа; идемпотентное подтверждение;
- `ExternalPayment` хранит внешний id → возможна сверка и ручная перепроверка.

---

## 7. Структура проекта

```
web/
  prisma/schema.prisma
  lib/
    domain/   billing.ts  visibility.ts  attendees.ts  periods.ts   # чистые функции + *.test.ts
    db/       client.ts  ids.ts  lessons.ts  payments.ts  students.ts …
    auth/     verifyInitData.ts  session.ts  requireRole.ts
  app/
    api/      teachers/  students/  lessons/  periods/  payments/  webhooks/ …/route.ts
    (mini)/   layout.tsx
              admin/     teachers/  students/  groups/  bills/  salaries/  profit/
              teacher/   record/  lessons/  groups/  submit/  stats/
              client/    lessons/  bills/  pay/
    middleware.ts
  components/  (кнопки, списки, карточки — аналоги keyboards/*)
  tests/       (Vitest — порт tests/*.py)
```

---

## 8. План сборки по фазам (делать сверху вниз, каждую — проверять)

- **Фаза 0 — каркас.** `create-next-app` (TS, App Router, Tailwind), Prisma init, Postgres (Docker/Neon),
  `@telegram-apps/sdk`. Критерий: пустое приложение открывается в Telegram, `initData` доходит до сервера.
- **Фаза 1 — БД.** Внести `schema.prisma` из §3, `prisma migrate`. Критерий: миграция применяется, типы генерятся.
- **Фаза 2 — домен + тесты.** Портировать `lib/domain/*` (§4) и **перенести характеризующие тесты** из
  `tests/test_billing_service.py`, `test_attendees.py`, `test_visibility.py`. Критерий: Vitest зелёный.
- **Фаза 3 — auth.** `verifyInitData` + сессия + резолв роли (§5) + `middleware.ts`. Критерий: три роли
  корректно определяются; чужие эндпоинты закрыты.
- **Фаза 4 — API (чтение).** Списки/карточки: teachers, students, groups, lessons, bills-compute. Критерий:
  цифры счёта/зарплаты совпадают с ботом на тех же данных.
- **Фаза 5 — API (запись).** Запись занятий (4 kind), сдача/переоткрытие периода, инвойсы, подтверждение
  оплаты — всё в транзакциях (заменяют локеры бота). Критерий: правила §6/§8 спеки соблюдены.
- **Фаза 6 — UI по ролям.** Экраны из §9 спеки; навигация в стиле Telegram; тема из SDK.
- **Фаза 7 — платежи.** ЮКасса + webhook + (опц.) CloudKassir-фискализация.
- **Фаза 8 — миграция данных + деплой.** Экспорт из Google Sheets → Postgres (сохранить оба формата
  attendees → `LessonAttendee`, снапшоты имён, читаемые ID). Деплой (Vercel/Fly + Neon/managed PG).

---

## 9. Не-договорные инварианты (свериться перед релизом)

Всё из §12 [MINIAPP_SPEC.md](MINIAPP_SPEC.md#12-воспроизведение-в-mini-app-что-сохранить-что-переосмыслить).
Кратко: формулы денег; `amount=0=абонемент`; один инвойс на `(student,teacher,period)` и обновление только
не-PAID; «оплачено» — вычисление, не поле; блокировка периода (+обход админом, сдача с 25-го); видимость через
пересечение групп; гард дублей соло; запрет будущей даты; денормализация имён; Client≠Student и порядок
регистрации детей.

---

*Пара «[MINIAPP_SPEC.md](MINIAPP_SPEC.md) (что и по каким правилам) + этот файл (чем и как)» — самодостаточный
комплект для сборки веб-приложения. Стек: Next.js + TypeScript + PostgreSQL + Prisma.*
