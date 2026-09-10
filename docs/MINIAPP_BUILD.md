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
| Фронт + бэк | **Next.js 16 (App Router) + React 19 + TypeScript** | поддерживаемая ветка без известных уязвимостей по `npm audit`; страницы и API в одном репозитории |
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
портированные из [billing_service.py](../bot/services/billing_service.py),
[profit_service.py](../bot/services/profit_service.py) и [visibility.py](../bot/services/visibility.py)
**вместе с тестами**. API-роуты только читают БД → зовут домен → пишут БД.

---

## 3. Схема БД (Prisma) — перенос домена и целевая модель оплат

> Соответствует [bot/models/entities.py](../bot/models/entities.py). Читаемые ID (`TCH-xxxx`) сохранены как
> первичные ключи (§5 спеки). **Изменение против бота:** поле `Lesson.attendees` (CSV) нормализовано в
> таблицу `LessonAttendee` — чище и без парсинга; правило `amount=0 = абонемент` сохраняется.

```prisma
// prisma/schema.prisma
generator client { provider = "prisma-client-js" }
datasource db { provider = "postgresql"; url = env("DATABASE_URL") }

enum LessonType        { GROUP INDIVIDUAL }
enum AuthProvider      { TELEGRAM PHONE }
enum InvoiceStatus     { DRAFT PENDING PAID CANCELLED EXPIRED }
enum InvoiceScope      { CART SUBSCRIPTION ADMIN_PERIOD }
enum InvoiceItemType   { LESSON SUBSCRIPTION ADJUSTMENT }
enum CoverageStatus    { ACTIVE RELEASED }
enum RequestStatus     { PENDING APPROVED REJECTED }
enum GroupBillingMode  { NONE PER_VISIT SUBSCRIPTION }   // SUBSCRIPTION = абонемент, фикс ₽/мес
enum StudentGroupTier  { FULL SHORT }

model User {
  userId    String  @id           // USR-xxxx
  tgId      BigInt  @unique
  isAdmin   Boolean @default(false)
  teacherId String?
  teacher   Teacher? @relation(fields: [teacherId], references: [teacherId])
  authIdentities AuthIdentity[]
  confirmedInvoices Invoice[] @relation("ConfirmedInvoices")
}

model Teacher {
  teacherId      String  @id       // TCH-xxxx
  tgId           BigInt?
  name           String
  rateGroup      Int               // ₽ за 45 мин, групповое
  rateForTeacher Int               // ₽ за 45 мин, индивидуальное (в зарплату)
  rateForStudent Int               // ₽ за 45 мин, в счёт ученика
  users          User[]
  groups         TeacherGroup[]
  lessons        Lesson[]
  invoiceItems   InvoiceItem[]
}

model Client {
  clientId  String   @id           // CLT-xxxx
  name      String
  tgId      BigInt?
  phone     String?  @unique       // E.164: +79...; обычный web-вход по одноразовому коду
  createdAt DateTime @default(now())
  students  Student[]
  authIdentities AuthIdentity[]
  invoices  Invoice[]
  receipts  Receipt[]
}

// Вход через Telegram и/или подтверждённый телефон.
// providerSubject: tgId строкой для TELEGRAM, E.164-телефон для PHONE.
model AuthIdentity {
  authIdentityId  String       @id @default(cuid())
  provider        AuthProvider
  providerSubject String
  userId          String?
  user            User?        @relation(fields: [userId], references: [userId])
  clientId        String?
  client          Client?      @relation(fields: [clientId], references: [clientId])
  verifiedAt      DateTime
  createdAt       DateTime     @default(now())
  @@unique([provider, providerSubject])
  @@index([clientId])
  @@index([userId])
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
  invoices     Invoice[]
  invoiceItems InvoiceItem[]
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
  invoiceItems  InvoiceItem[]
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
  deletedAt   DateTime?             // target web: soft-delete сохраняет финансовый аудит
  attendees   LessonAttendee[]      // только для GROUP; замена CSV-поля attendees
  invoiceItems InvoiceItem[]
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

// Целевая модель Mini App/web: счёт-корзина с позициями.
// Lesson не получает поле paid; кабинет родителя получает computed paymentStatus из InvoiceItem.
model Invoice {
  invoiceId         String       @id      // INV-xxxxxx
  clientId          String
  client            Client       @relation(fields: [clientId], references: [clientId])
  studentId         String
  student           Student      @relation(fields: [studentId], references: [studentId])
  scope             InvoiceScope          // CART = выбранные начисления родителя
  amount            Int
  status            InvoiceStatus @default(DRAFT)
  expiresAt         DateTime?              // только DRAFT; затем EXPIRED
  paidAt            DateTime?
  confirmedByUserId String?
  confirmedBy       User?         @relation("ConfirmedInvoices", fields: [confirmedByUserId], references: [userId])
  paymentMethod     String?
  comment           String?
  createdAt         DateTime @default(now())
  updatedAt         DateTime @updatedAt
  items             InvoiceItem[]
  externalPayments  ExternalPayment[]
  receipts           Receipt[]
  @@index([studentId, status])
  @@index([clientId, status])
}

model InvoiceItem {
  invoiceItemId String          @id @default(cuid())
  invoiceId     String
  invoice       Invoice         @relation(fields: [invoiceId], references: [invoiceId], onDelete: Cascade)
  itemType      InvoiceItemType
  coverageKey   String          // LESSON:... | SUB:... | ADJ:{invoiceId}:{n}
  coverageStatus CoverageStatus @default(ACTIVE)
  studentId     String
  student       Student         @relation(fields: [studentId], references: [studentId])
  teacherId     String?
  teacher       Teacher?        @relation(fields: [teacherId], references: [teacherId])
  lessonId      String?         // itemType=LESSON
  lesson        Lesson?         @relation(fields: [lessonId], references: [lessonId])
  groupId       String?         // itemType=SUBSCRIPTION
  group         Group?          @relation(fields: [groupId], references: [groupId])
  periodMonth   String?         // SUBSCRIPTION/admin-period compatibility
  date          String?         // snapshot for display, "YYYY-MM-DD"
  description   String          // snapshot: "12.07 · Иванова · 60 мин"
  amount        Int             // ₽ snapshot; total Invoice.amount = sum(items.amount)
  createdAt     DateTime @default(now())
  @@index([studentId, lessonId])
  @@index([studentId, teacherId, periodMonth])
  @@index([coverageKey, coverageStatus])
}

// Внешний онлайн-платёж (ЮКасса) — для верификации webhook и сверки (§6.1).
// В боте не хранился (см. FOUND_BUGS.md B4) — в веб-версии обязателен.
model ExternalPayment {
  id          String   @id @default(cuid())
  provider    String   @default("yookassa")
  externalId  String   @unique          // payment.id ЮКассы
  invoiceId   String
  invoice     Invoice  @relation(fields: [invoiceId], references: [invoiceId])
  idempotencyKey String @unique          // тот же ключ передаётся провайдеру
  amount      Int                       // ₽; сверяется с webhook перед подтверждением
  status      String   @default("pending") // pending|succeeded|canceled (зеркало провайдера)
  createdAt   DateTime @default(now())
  updatedAt   DateTime @updatedAt
  @@index([invoiceId])
}

// Загруженный чек (реквизиты/СБП) — только для UI-состояния «ждёт подтверждения» (§6.1).
model Receipt {
  id          String   @id @default(cuid())
  invoiceId   String
  invoice     Invoice  @relation(fields: [invoiceId], references: [invoiceId])
  method      String                    // bank|sbp
  fileId      String                    // telegram file_id или путь в сторадже
  uploadedByClientId String
  uploadedBy  Client   @relation(fields: [uploadedByClientId], references: [clientId])
  uploadedAt  DateTime @default(now())
  @@index([invoiceId])
}

// Цена абонемента на конкретный месяц: ученик → группа → group.priceFull; 0 = не начислять.
model SubscriptionOverride {
  id          String  @id @default(cuid())
  groupId     String
  periodMonth String              // "YYYY-MM"
  studentId   String?             // null = вся группа
  scopeKey    String              // studentId или "*" для всей группы
  amount      Int
  createdAt   DateTime @default(now())
  @@unique([groupId, periodMonth, scopeKey])
}

// Ручной доход/расход месяца (экран «Прибыль»): турниры, аренда и т.п.
model FinanceEntry {
  entryId     String   @id        // FIN-XXXXXX
  periodMonth String               // "YYYY-MM"
  kind        String               // income | expense
  title       String
  amount      Int
  createdAt   DateTime @default(now())
  @@index([periodMonth])
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
  requestId      String   @id       // текущий бот: 8 hex-символов
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
> генерируются функцией «max+1» под advisory lock или в SERIALIZABLE-транзакции
> (обычная read-committed транзакция не защищает два параллельных `max+1`).
>
> `SubscriptionOverride.scopeKey` нужен из-за семантики PostgreSQL: составной `UNIQUE` допускает
> несколько строк с `studentId = NULL`. Приложение пишет `scopeKey = studentId` для ученика и `"*"`
> для всей группы, сохраняя ровно одно переопределение на область.
>
> `StudentPeriodPayment` — только исходная модель текущего бота, в целевой Prisma-схеме её нет.
> Миграция преобразует каждую строку в `Invoice(scope=ADMIN_PERIOD)`: фиксирует ровно тот список
> LESSON/SUBSCRIPTION-покрытий, который существовал на момент миграции, и сохраняет исходный totalAmount.
> Если восстановленные позиции не складываются в старый итог, разница записывается отдельным
> `InvoiceItem(type=ADJUSTMENT, coverageKey=ADJ:{invoiceId}:{n})`; она влияет на сумму/аудит, но не
> на статус занятия. Старый PAID становится PAID/ACTIVE; старый PENDING сохраняется для аудита как
> CANCELLED/RELEASED, а актуальные неоплаченные начисления родитель собирает заново. После переключения
> все новые оплаты пишутся только в `Invoice`.
>
> Prisma не описывает partial unique index, поэтому миграция добавляет его вручную:
> `CREATE UNIQUE INDEX invoice_item_active_coverage_uq ON "InvoiceItem" ("coverageKey")
> WHERE "coverageStatus" = 'ACTIVE';`. Создание/отмена/истечение счёта выполняются в транзакции:
> `PAID` сохраняет `ACTIVE` навсегда, а `CANCELLED/EXPIRED` переводит позиции в `RELEASED`.

### 3.1 Объём данных: решение по `lessons`

Замер продакшена (июль 2026): **1 273 занятия всего, темп ~424/мес ≈ 5 000/год**.

**Решение для Postgres: одна таблица `lessons`, навсегда, без архивирования и партиционирования.**
- 5 000 строк/год → 50 000 за 10 лет — для Postgres с индексами это пренебрежимо мало
  (партиционирование имеет смысл от десятков миллионов строк).
- Индексы под реальные запросы: `@@index([teacherId, date])` (занятия педагога/период) и
  `LessonAttendee.studentId` (групповые занятия ученика). При текущем объёме OR-поиск по четырём
  student-слотам допустим без отдельных индексов; добавить их после замера query plan, если объём вырастет.
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

`lib/domain/` — перенос [billing_service.py](../bot/services/billing_service.py), чистых DTO/функций из
[profit_service.py](../bot/services/profit_service.py) (не repository-orchestration класса) и
[visibility.py](../bot/services/visibility.py). **Перенести и тесты** (Vitest) из `tests/` — они эталон.
`ProfitSummary` / `TeacherProfitDetail` из Python-сервиса задают готовую форму данных для
`GET /profit?period=`; HTML-разметку из Telegram-хендлера в домен не переносить.

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
остатком первому; `amount=0=абонемент`; одно активное покрытие на `coverageKey`; «оплачено» вычисляется
по PAID-позиции, не хранится в занятии; частичная оплата допустима; блокировка периода + обход админом
и сдача с 25-го; запрет будущей даты; гард дублей соло; денормализация снапшотов имён.

---

## 5. Аутентификация Mini App

> **Решение 2026-09-06:** отдельный сайт с входом по телефону (phone OTP) исключён из плана —
> приложение работает **только как Telegram Mini App**. Единственный способ входа для всех ролей —
> проверенный Telegram `initData` (подпись + срок жизни). Модель `AuthChallenge` и phone-OTP-флоу
> из ранних версий этого документа не реализуются; `AuthIdentity` остаётся только с provider=TELEGRAM.

```ts
// lib/auth/verifyInitData.ts
import { createHmac, timingSafeEqual } from "crypto";

export function verifyInitData(initData: string, botToken: string, maxAgeSec = 86400) {
  const params = new URLSearchParams(initData);
  const hash = params.get("hash"); params.delete("hash");
  const dataCheckString = [...params.entries()]
    .map(([k, v]) => `${k}=${v}`).sort().join("\n");
  const secret = createHmac("sha256", "WebAppData").update(botToken).digest();
  const computed = createHmac("sha256", secret).update(dataCheckString).digest("hex");
  if (!hash) throw new Error("missing initData signature");
  const actual = Buffer.from(hash, "hex");
  const expected = Buffer.from(computed, "hex");
  if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
    throw new Error("bad initData signature");
  }
  const authDate = Number(params.get("auth_date"));
  const ageSec = Date.now() / 1000 - authDate;
  if (!Number.isFinite(authDate) || ageSec < -30 || ageSec > maxAgeSec) {
    throw new Error("bad or expired auth_date");
  }
  const rawUser = params.get("user");
  if (!rawUser) throw new Error("missing user");
  return JSON.parse(rawUser) as { id: number; first_name: string; username?: string };
}
```

**Резолв роли Telegram** после проверки: `tgId` →
1. `User` с этим `tgId` и `isAdmin` → **admin** (+teacher, если `teacherId`);
2. `User`/`Teacher` с `teacherId` → **teacher**;
3. есть `Client.tgId` или `Student`, где `tgId = ANY(parentTgIds)` → найти единый `Client`, создать при
   необходимости `AuthIdentity(TELEGRAM)` и выдать **client**-сессию с `clientId`;
4. иначе — гость (экран регистрации клиента).

Если найденные дети ошибочно относятся к разным `Client`, доступ не объединяется автоматически:
создаётся диагностическая ошибка для администратора. После миграции каждое клиентское действие
проверяет `student.clientId == session.clientId`; `parentTgIds` остаётся только источником миграции/бота.

**Телефонный вход клиента:**

- `POST /api/auth/phone/request` принимает нормализуемый телефон, всегда возвращает одинаковый ответ
  (не раскрывает, зарегистрирован ли клиент), применяет rate limit по IP и телефону;
- сервер генерирует криптографически стойкий одноразовый код, хранит только его hash с TTL 5 минут,
  максимум 5 попыток; после успешной проверки challenge удаляется;
- `POST /api/auth/phone/verify` находит ровно один `Client` по E.164-телефону, создаёт/обновляет
  `AuthIdentity(provider=PHONE)` и выдаёт сессию клиента;
- неизвестный телефон не получает доступ к детям и после успешной проверки видит инструкцию обратиться
  к администратору; администратор сначала привязывает E.164-телефон к существующему `Client`;
- привязать Telegram к уже существующему клиенту можно только из авторизованной сессии клиента или
  через подтверждённый администратором запрос — совпадения имени недостаточно.

Сессия: подписанная короткоживущая `HttpOnly + Secure + SameSite=Lax` cookie с
`{actorId, role, clientId?, teacherId?, authMethod}`; внутри не требуется `tgId`. Logout удаляет cookie,
а повторная аутентификация выпускает новую. `middleware.ts` защищает `/api/*` и страницы.
Каждый чувствительный эндпоинт **дополнительно** перепроверяет право (роль, видимость группы, принадлежность ученика).

---

## 6. API-контракт (REST, по ресурсам)

Все под `/api`, JSON, роль проверяется на сервере. Ниже — карта (не весь I/O; ключевые — детально).

**Auth**
- `POST /api/auth/telegram` `{ initData }` → `{ role, teacherId? }` (ставит cookie).
- `POST /api/auth/phone/request` `{ phone }` → нейтральный ответ; rate limit.
- `POST /api/auth/phone/verify` `{ phone, code }` → `{ role:"client" }` (ставит cookie).
- `POST /api/auth/logout` → удаляет текущую session-cookie.

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
- `GET /lessons?teacherId=&studentId=&period=&date=` (фильтры) · `DELETE /lessons/:id` (soft-delete).
  Если начисление в DRAFT, транзакция удаляет позицию/пересчитывает счёт или отменяет пустой счёт;
  при PENDING/PAID удаление блокируется до отмены платежа или оформленной админом коррекции/возврата.

**Periods** (teacher)
- `GET /periods/:teacherId/:month/preview` → `{ lessons, totalEarned }` (сумма `calcEarned`).
- `POST /periods/submit` `{ teacherId, periodMonth }` (только с 25-го; создаёт submission-замок).

**Bills / Payments**
- `GET /students/:id/bills/:period` → счёт, сгруппированный по педагогам (`compute`, on-demand).
- `GET /me/payables?studentId=&from=&to=` → универсальные начисления `LESSON|SUBSCRIPTION` с
  `coverageKey`, суммой и вычисленным статусом.
- `POST /me/invoices/cart` `{ studentId, payableKeys: [...] }` → транзакционно создаёт DRAFT `Invoice`
  с `InvoiceItem[]` и резервирует покрытия partial unique index. **Сумму клиент НЕ передаёт**.
  Один Invoice относится к одному ученику; экран «все дети» группирует выбор по детям и создаёт
  отдельный счёт/платёж для каждого ребёнка.
- `POST /invoices/:invoiceId/confirm` (admin) → подтверждает только этот PENDING-счёт + фискализация.
- `POST /invoices/:invoiceId/cancel` → владелец отменяет только DRAFT и освобождает позиции.
- `POST /invoices/:invoiceId/reject` (admin) → отклоняет PENDING ручной оплаты и освобождает позиции.
- `POST /payments/yookassa` `{ invoiceId }` → `{ confirmationUrl, externalPaymentId }`.
  Требует `Idempotency-Key`; переводит DRAFT→PENDING и берёт сумму из `Invoice.amount` (§6.1).
- `POST /webhooks/yookassa` — с обязательной перепроверкой через API ЮКассы (§6.1).
- `GET /payments/external/:id/status` → `{ status }` — кнопка «проверить ещё раз» (§6.1).

**Прочее** (admin): `GET /salaries?period=`, `GET /profit?period=`, `GET /requests` +
`POST /requests/:id/approve|reject`, `GET /diagnostics`.

**Client**: `GET /me/children`, `GET /me/lessons?...`, `GET /me/bills?...`, `POST /me/add-child` (заявка).

`GET /me/lessons?...` возвращает занятия уже с рассчитанным статусом оплаты:
```json
{
  "lessonId": "LES-001234",
  "date": "2026-07-12",
  "teacherName": "Иванова",
  "durationMin": 60,
  "amount": 700,
  "paymentStatus": "PAID",
  "coverageKey": "LESSON:STU-0001:LES-001234",
  "selectableForPayment": false
}
```
`paymentStatus` — computed поле API, не колонка в `Lesson`: `NOT_CHARGEABLE`, `UNPAID`, `RESERVED`,
`PENDING` или `PAID`. Выбирать можно только `UNPAID`. `RESERVED` означает DRAFT другого/текущего счёта,
`PENDING` — платёж или чек ожидает завершения. Для `SUBSCRIPTION` API `/me/payables` возвращает одну
месячную карточку начисления; отдельные занятия этой группы ссылаются на тот же subscription coverage.

### 6.1 Проверка оплат на стороне клиента (решение)

**Принцип: фронт никогда не решает, оплачено ли, — он только отображает статус с сервера.**
`PAID` ставится ровно двумя путями: (а) верифицированный webhook ЮКассы, (б) подтверждение
админа (наличные / реквизиты / СБП). Всё остальное — отображение.

**Поток онлайн-оплаты (ЮКасса):**
```
1. Клиент на экране «Занятия» отмечает неоплаченные начисления из `/me/payables`. Это локальная корзина.
   Быстрые действия «выбрать неделю/месяц» отмечают подходящие уроки и один раз каждый попавший
   в диапазон месячный абонемент.
2. Клиент жмёт «Перейти к оплате» → POST /me/invoices/cart { studentId, payableKeys }.
   Сервер: проверяет Telegram-сессию (initData) и принадлежность studentId текущему Client;
   проверяет актуальность всех coverageKey и отсутствие другого ACTIVE-покрытия;
   ВЫЧИСЛЯЕТ сумму каждой позиции из доменных правил (от клиента сумму не принимает);
   в одной транзакции создаёт DRAFT Invoice(scope=CART) + InvoiceItem[]; конфликт уникальности
   возвращает 409 и свежий список начислений.
3. Клиент жмёт «Оплатить» → POST /payments/yookassa { invoiceId }.
   Сервер: снова проверяет владельца invoiceId и статус DRAFT, берёт сумму из Invoice.amount;
   по обязательному Idempotency-Key создаёт или возвращает тот же платёж, переводит Invoice в PENDING
   и СОХРАНЯЕТ ExternalPayment
   (externalId ЮКассы + invoiceId + amount); отдаёт confirmationUrl.
4. Фронт открывает confirmationUrl; после оплаты пользователь возвращается по return_url.
5. Фронт показывает «Проверяем оплату…» и поллит GET /me/invoices/:invoiceId
   каждые 2–3 с, до ~90 с. (Поллинг, не WebSocket/SSE: сессии Mini App короткие,
   лёгкий запрос раз в 2–3 с в течение минуты — простейшее надёжное решение.)
6. Параллельно приходит webhook: сервер НЕ верит телу запроса — берёт object.id,
   запрашивает статус у API ЮКассы (GET /payments/{id}) и подтверждает invoice только
   при реальном `succeeded` и совпадении суммы с ExternalPayment/Invoice. Повторные webhook
   безопасны: PAID-инвойс повторно не меняется. PENDING онлайн становится CANCELLED только после
   подтверждённого статуса `canceled` у провайдера.
7. Поллинг видит PAID → фронт показывает ✅ у всех занятий, которые покрыты InvoiceItem.
8. Если за 90 с статус не сменился: «Платёж обрабатывается, статус обновится автоматически»
   + кнопка «Проверить ещё раз» → GET /payments/external/:id/status (сервер сам опрашивает
   ЮКассу по сохранённому externalId — страховка от потерянного webhook).
```

**Ручные способы (наличные / реквизиты / СБП):** выбор метода переводит DRAFT→PENDING; статус остаётся
PENDING до `POST /invoices/:invoiceId/confirm` администратором. Подтверждается только этот invoiceId,
не все счета месяца. `Receipt(invoiceId, fileId, uploadedAt)` даёт состояние «чек отправлен».

**Жизненный цикл:** DRAFT без выбранного способа оплаты истекает, например, через 30 минут; PENDING
онлайн не истекает до синхронизации с провайдером, а PENDING ручного способа — до решения администратора.
Фоновая задача переводит просроченный DRAFT в EXPIRED и атомарно освобождает его покрытия. Пользователь
может отменить DRAFT; админ может отклонить PENDING ручной оплаты; PAID неизменяем.
CANCELLED/EXPIRED позиции получают `coverageStatus=RELEASED`.

**✅ на занятиях** — вычисляется на сервере, фронт только рендерит. В текущем боте правило простое:
`(period, teacher)` имеет PAID-инвойс. В Mini App/web правило шире: занятие оплачено, если существует
PAID `InvoiceItem`, который его покрывает:
- `itemType=LESSON`: `invoiceItem.lessonId == lesson.lessonId` и `invoice.status == PAID`;
- `itemType=SUBSCRIPTION`: оплаченный абонемент покрывает группу/месяц по правилам абонемента.

**Чек-лист безопасности платежей:**
- сумма — только серверный расчёт; от клиента не принимается;
- принадлежность ученика родителю проверяется на каждом платёжном эндпоинте;
- один ACTIVE `coverageKey` обеспечивается partial unique index, а не только предварительной проверкой;
- webhook перепроверяется через API ЮКассы (в боте уже реализовано —
  [payments.py: process_yookassa_event](../bot/handlers/client/payments.py), см. историю в
  [FOUND_BUGS.md B4](FOUND_BUGS.md); в веб-версии портировать 1-в-1);
- обязательный уникальный idempotency key при создании платежа; идемпотентное подтверждение;
- `ExternalPayment` хранит внешний id → возможна сверка и ручная перепроверка.

---

## 7. Структура проекта

```
web/
  prisma/schema.prisma
  lib/
    domain/   billing.ts  profit.ts  subscriptions.ts  debt.ts
              visibility.ts  attendees.ts  periods.ts               # чистые функции + *.test.ts
    db/       client.ts  ids.ts  lessons.ts  payments.ts  students.ts …
    auth/     verifyInitData.ts  phoneOtp.ts  session.ts  requireRole.ts
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
  `@telegram-apps/sdk`. Критерий: приложение открывается внутри Telegram (Mini App); отдельный сайт не поддерживаем.
- **Фаза 1 — БД.** Внести `schema.prisma` из §3, `prisma migrate`. Критерий: миграция применяется, типы генерятся.
- **Фаза 2 — домен + тесты.** Портировать `lib/domain/*` (§4) и **перенести характеризующие тесты** из
  `tests/test_billing_service.py`, `test_profit_service.py`, `test_subscription_billing.py`,
  `test_debt_map.py`, `test_attendees.py`, `test_visibility.py`.
  Критерий: Vitest зелёный; суммы `ProfitSummary` совпадают с Python на одинаковых входных данных.
- **Фаза 3 — auth.** `verifyInitData` + сессия + резолв роли (§5) + `middleware.ts`. Только Telegram
  `initData` — phone OTP исключён (решение 2026-09-06). Критерий: подпись/срок жизни проверяются,
  три роли резолвятся, чужие эндпоинты недоступны.
- **Фаза 4 — API (чтение).** Списки/карточки: teachers, students, groups, lessons, bills-compute. Критерий:
  цифры счёта/зарплаты совпадают с ботом на тех же данных.
- **Фаза 5 — API (запись).** Запись занятий (4 kind), сдача/переоткрытие периода, инвойсы, подтверждение
  оплаты — всё в транзакциях (заменяют локеры бота). Обязательные integration-тесты: два параллельных
  cart-запроса на один coverageKey (один получает 409); EXPIRED/CANCELLED освобождает позицию;
  PAID не освобождает; ручное подтверждение меняет только один invoiceId; повторный Idempotency-Key и
  повторный webhook не создают вторую оплату. Критерий: правила §6/§8 спеки соблюдены.
- **Фаза 6 — UI по ролям.** Экраны из §9 спеки; навигация в стиле Telegram; тема из SDK.
- **Фаза 7 — платежи.** ЮКасса + webhook + (опц.) CloudKassir-фискализация.
- **Фаза 8 — миграция данных + деплой.** Экспорт из Google Sheets → Postgres (сохранить оба формата
  attendees → `LessonAttendee`, снапшоты имён, читаемые ID); преобразовать `StudentPeriodPayment` в
  `Invoice/InvoiceItem`; создать TELEGRAM `AuthIdentity` и проверить, что каждый parentTgId резолвится
  ровно в один Client. Деплой (Vercel/Fly + Neon/managed PG).

---

## 9. Не-договорные инварианты (свериться перед релизом)

Всё из §12 [MINIAPP_SPEC.md](MINIAPP_SPEC.md#12-воспроизведение-в-mini-app-что-сохранить-что-переосмыслить).
Кратко: формулы денег; `amount=0=абонемент`; корзина из универсальных начислений; один ACTIVE
`coverageKey`; частичная оплата допустима; подтверждается только конкретный invoiceId; «оплачено» —
вычисление, не поле; блокировка периода (+обход админом, сдача с 25-го); видимость через пересечение групп;
гард дублей соло; запрет будущей даты; денормализация имён; Client≠Student; вход только через Telegram `initData`.

---

*Пара «[MINIAPP_SPEC.md](MINIAPP_SPEC.md) (что и по каким правилам) + этот файл (чем и как)» — самодостаточный
комплект для сборки веб-приложения. Стек: Next.js + TypeScript + PostgreSQL + Prisma.*
