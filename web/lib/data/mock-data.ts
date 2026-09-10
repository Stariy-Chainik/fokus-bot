import type { AdminState, ParentState, RoleDashboard, TeacherState, UserRole } from "@/lib/domain/types";

export const DEMO_USERS: Record<UserRole, { actorId: string; displayName: string }> = {
  admin: { actorId: "USR-DEMO-ADMIN", displayName: "Анна, администратор" },
  teacher: { actorId: "USR-DEMO-TEACHER", displayName: "Мария, педагог" },
  client: { actorId: "CLT-DEMO-PARENT", displayName: "Елена, мама Алисы" }
};

export const DASHBOARDS: Record<UserRole, RoleDashboard> = {
  client: {
    eyebrow: "Кабинет родителя",
    title: "Добрый день, Елена",
    description: "Занятия Алисы и Максима, оплаты и ближайшие события — в одном месте.",
    metrics: [
      { label: "Ближайшее занятие", value: "Сегодня, 18:30", hint: "Бальные танцы · Южное Бутово" },
      { label: "К оплате", value: "2 100 ₽", hint: "3 занятия доступны для корзины" },
      { label: "В этом месяце", value: "8 занятий", hint: "5 уже оплачено" }
    ],
    nextActions: ["Открыть занятия", "Собрать корзину", "Посмотреть счета"]
  },
  teacher: {
    eyebrow: "Кабинет педагога",
    title: "Рабочий день Марии",
    description: "Записывайте занятия, следите за группами и готовьте период к сдаче.",
    metrics: [
      { label: "Сегодня", value: "4 занятия", hint: "Первое в 15:00" },
      { label: "Ученики", value: "26", hint: "В 3 активных группах" },
      { label: "Период", value: "Открыт", hint: "Сдача доступна с 25 июля" }
    ],
    nextActions: ["Записать занятие", "Мои группы", "Проверить период"]
  },
  admin: {
    eyebrow: "Панель администратора",
    title: "Школа сегодня",
    description: "Ключевые показатели, оплаты и задачи, требующие внимания.",
    metrics: [
      { label: "Занятия сегодня", value: "18", hint: "4 филиала и направления" },
      { label: "Ожидают оплаты", value: "12 600 ₽", hint: "7 счетов родителей" },
      { label: "Требуют внимания", value: "3", hint: "Чеки и заявки на ребёнка" }
    ],
    nextActions: ["Проверить оплаты", "Открыть должников", "Посмотреть прибыль"]
  }
};

export const INITIAL_PARENT_STATE: ParentState = {
  children: [
    { childId: "STU-DEMO-ALISA", name: "Алиса Волкова", shortName: "Алиса", color: "coral" },
    { childId: "STU-DEMO-MAX", name: "Максим Волков", shortName: "Максим", color: "gold" }
  ],
  cartKeys: [],
  invoices: [
    {
      invoiceId: "INV-DEMO-001",
      childId: "STU-DEMO-ALISA",
      createdAt: "2026-07-05T12:00:00Z",
      amount: 4_500,
      status: "PAID",
      method: "CARD",
      itemKeys: ["SUB:STU-DEMO-ALISA:GRP-DEMO-DANCE:2026-07"]
    },
    {
      invoiceId: "INV-DEMO-002",
      childId: "STU-DEMO-ALISA",
      createdAt: "2026-07-08T18:00:00Z",
      amount: 700,
      status: "PENDING",
      method: "SBP",
      itemKeys: ["LESSON:STU-DEMO-ALISA:LES-DEMO-008"]
    }
  ],
  payables: [
    {
      payableKey: "SUB:STU-DEMO-ALISA:GRP-DEMO-DANCE:2026-07",
      type: "SUBSCRIPTION",
      childId: "STU-DEMO-ALISA",
      title: "Абонемент на июль",
      subtitle: "Бальные танцы · Южное Бутово",
      date: "2026-07-01",
      periodMonth: "2026-07",
      amount: 4_500,
      status: "PAID"
    },
    {
      payableKey: "LESSON:STU-DEMO-ALISA:LES-DEMO-004",
      type: "LESSON",
      childId: "STU-DEMO-ALISA",
      title: "Индивидуальное занятие",
      subtitle: "Мария Иванова · 60 минут",
      date: "2026-07-04",
      periodMonth: "2026-07",
      amount: 700,
      status: "PAID"
    },
    {
      payableKey: "LESSON:STU-DEMO-ALISA:LES-DEMO-008",
      type: "LESSON",
      childId: "STU-DEMO-ALISA",
      title: "Индивидуальное занятие",
      subtitle: "Мария Иванова · 60 минут",
      date: "2026-07-08",
      periodMonth: "2026-07",
      amount: 700,
      status: "PENDING"
    },
    {
      payableKey: "LESSON:STU-DEMO-ALISA:LES-DEMO-010",
      type: "LESSON",
      childId: "STU-DEMO-ALISA",
      title: "Открытая практика",
      subtitle: "Группа Юниоры · 45 минут",
      date: "2026-07-10",
      periodMonth: "2026-07",
      amount: 0,
      status: "NOT_CHARGEABLE"
    },
    {
      payableKey: "LESSON:STU-DEMO-ALISA:LES-DEMO-012",
      type: "LESSON",
      childId: "STU-DEMO-ALISA",
      title: "Индивидуальное занятие",
      subtitle: "Мария Иванова · 60 минут",
      date: "2026-07-12",
      periodMonth: "2026-07",
      amount: 700,
      status: "UNPAID"
    },
    {
      payableKey: "LESSON:STU-DEMO-ALISA:LES-DEMO-015",
      type: "LESSON",
      childId: "STU-DEMO-ALISA",
      title: "Индивидуальное занятие",
      subtitle: "Мария Иванова · 60 минут",
      date: "2026-07-15",
      periodMonth: "2026-07",
      amount: 700,
      status: "UNPAID"
    },
    {
      payableKey: "LESSON:STU-DEMO-MAX:LES-DEMO-006",
      type: "LESSON",
      childId: "STU-DEMO-MAX",
      title: "Общая физическая подготовка",
      subtitle: "Детская группа · 45 минут",
      date: "2026-07-06",
      periodMonth: "2026-07",
      amount: 0,
      status: "NOT_CHARGEABLE"
    },
    {
      payableKey: "SUB:STU-DEMO-MAX:GRP-DEMO-KIDS:2026-07",
      type: "SUBSCRIPTION",
      childId: "STU-DEMO-MAX",
      title: "Абонемент на июль",
      subtitle: "Детская группа · Бутово Парк",
      date: "2026-07-01",
      periodMonth: "2026-07",
      amount: 3_900,
      status: "UNPAID"
    },
    {
      payableKey: "LESSON:STU-DEMO-MAX:LES-DEMO-013",
      type: "LESSON",
      childId: "STU-DEMO-MAX",
      title: "Групповое посещение",
      subtitle: "Спортивная группа · 60 минут",
      date: "2026-07-13",
      periodMonth: "2026-07",
      amount: 600,
      status: "UNPAID"
    },
    {
      payableKey: "LESSON:STU-DEMO-MAX:LES-DEMO-016",
      type: "LESSON",
      childId: "STU-DEMO-MAX",
      title: "Групповое посещение",
      subtitle: "Спортивная группа · 60 минут",
      date: "2026-07-16",
      periodMonth: "2026-07",
      amount: 600,
      status: "RESERVED"
    }
  ]
};

export function createInitialParentState(): ParentState {
  return JSON.parse(JSON.stringify(INITIAL_PARENT_STATE)) as ParentState;
}

export const INITIAL_TEACHER_STATE: TeacherState = {
  teacherId: "TCH-DEMO-MARIA",
  teacherName: "Мария Иванова",
  submittedPeriods: ["2026-06"],
  groups: [
    { groupId: "GRP-DEMO-JUNIOR", name: "Юниоры", branchName: "Южное Бутово", studentIds: ["STU-DEMO-ALISA", "STU-DEMO-DASHA", "STU-DEMO-MISHA"], color: "pink" },
    { groupId: "GRP-DEMO-SPORT", name: "Спортивная группа", branchName: "Бутово Парк", studentIds: ["STU-DEMO-MAX", "STU-DEMO-LEV", "STU-DEMO-SOFIA"], color: "lilac" },
    { groupId: "GRP-DEMO-START", name: "Начинающие", branchName: "Коммунарка", studentIds: ["STU-DEMO-DASHA", "STU-DEMO-LEV"], color: "yellow" }
  ],
  students: [
    { studentId: "STU-DEMO-ALISA", name: "Алиса Волкова", groupIds: ["GRP-DEMO-JUNIOR"] },
    { studentId: "STU-DEMO-DASHA", name: "Дарья Климова", groupIds: ["GRP-DEMO-JUNIOR", "GRP-DEMO-START"] },
    { studentId: "STU-DEMO-MISHA", name: "Михаил Орлов", groupIds: ["GRP-DEMO-JUNIOR"] },
    { studentId: "STU-DEMO-MAX", name: "Максим Волков", groupIds: ["GRP-DEMO-SPORT"] },
    { studentId: "STU-DEMO-LEV", name: "Лев Соколов", groupIds: ["GRP-DEMO-SPORT", "GRP-DEMO-START"] },
    { studentId: "STU-DEMO-SOFIA", name: "София Белова", groupIds: ["GRP-DEMO-SPORT"] }
  ],
  lessons: [
    { lessonId: "LES-T-DEMO-001", kind: "SHARED", title: "Совместное занятие", date: "2026-07-08", durationMin: 60, studentIds: ["STU-DEMO-DASHA", "STU-DEMO-MISHA"], earned: 1_467 },
    { lessonId: "LES-T-DEMO-002", kind: "GROUP", title: "Юниоры", date: "2026-07-09", durationMin: 60, groupId: "GRP-DEMO-JUNIOR", studentIds: [], earned: 1_200 },
    { lessonId: "LES-T-DEMO-003", kind: "SOLOIST", title: "Сольное занятие", date: "2026-07-10", durationMin: 60, studentIds: ["STU-DEMO-ALISA"], earned: 1_467 },
    { lessonId: "LES-T-DEMO-004", kind: "PAIR", title: "Парное занятие", date: "2026-07-10", durationMin: 60, studentIds: ["STU-DEMO-DASHA", "STU-DEMO-MISHA"], earned: 1_467 },
    { lessonId: "LES-T-DEMO-005", kind: "GROUP", title: "Спортивная группа", date: "2026-07-11", durationMin: 45, groupId: "GRP-DEMO-SPORT", studentIds: [], earned: 900 }
  ]
};

export function createInitialTeacherState(): TeacherState {
  return JSON.parse(JSON.stringify(INITIAL_TEACHER_STATE)) as TeacherState;
}

export const INITIAL_ADMIN_STATE: AdminState = {
  students: [
    { studentId: "STU-DEMO-ALISA", name: "Алиса Волкова", groupName: "Юниоры", parentName: "Елена Волкова", balance: 1_400 },
    { studentId: "STU-DEMO-MAX", name: "Максим Волков", groupName: "Спортивная группа", parentName: "Елена Волкова", balance: 4_500 },
    { studentId: "STU-DEMO-DASHA", name: "Дарья Климова", groupName: "Начинающие", parentName: "Ольга Климова", balance: 0 },
    { studentId: "STU-DEMO-MISHA", name: "Михаил Орлов", groupName: "Юниоры", parentName: "Ирина Орлова", balance: 700 },
    { studentId: "STU-DEMO-LEV", name: "Лев Соколов", groupName: "Спортивная группа", parentName: "Антон Соколов", balance: 3_900 },
    { studentId: "STU-DEMO-SOFIA", name: "София Белова", groupName: "Спортивная группа", parentName: "Наталья Белова", balance: 2_100 }
  ],
  teachers: [
    { teacherId: "TCH-DEMO-MARIA", name: "Мария Иванова", groupNames: ["Юниоры", "Спортивная группа"], lessonsThisMonth: 24, earned: 31_800, periodStatus: "OPEN" },
    { teacherId: "TCH-DEMO-OLGA", name: "Ольга Смирнова", groupNames: ["Начинающие"], lessonsThisMonth: 18, earned: 22_500, periodStatus: "OPEN" },
    { teacherId: "TCH-DEMO-ALEX", name: "Алексей Петров", groupNames: ["Латина", "Соло"], lessonsThisMonth: 21, earned: 28_400, periodStatus: "SUBMITTED" }
  ],
  payments: [
    { invoiceId: "INV-DEMO-011", parentName: "Елена Волкова", childName: "Алиса Волкова", amount: 700, method: "SBP", status: "PENDING", createdAt: "2026-07-11T12:20:00Z" },
    { invoiceId: "INV-DEMO-010", parentName: "Наталья Белова", childName: "София Белова", amount: 2_100, method: "BANK", status: "PENDING", createdAt: "2026-07-11T09:10:00Z" },
    { invoiceId: "INV-DEMO-009", parentName: "Ольга Климова", childName: "Дарья Климова", amount: 3_900, method: "CARD", status: "PAID", createdAt: "2026-07-10T18:30:00Z" },
    { invoiceId: "INV-DEMO-008", parentName: "Ирина Орлова", childName: "Михаил Орлов", amount: 700, method: "CASH", status: "PAID", createdAt: "2026-07-10T15:00:00Z" }
  ],
  childRequests: [
    { requestId: "REQ-DEMO-001", parentName: "Светлана Морозова", childName: "Анна Морозова", createdAt: "2026-07-11T10:00:00Z", status: "PENDING" },
    { requestId: "REQ-DEMO-002", parentName: "Дмитрий Фролов", childName: "Егор Фролов", createdAt: "2026-07-10T16:40:00Z", status: "PENDING" }
  ],
  financeEntries: [
    { entryId: "FIN-DEMO-001", title: "Абонементы и занятия", type: "INCOME", amount: 184_600 },
    { entryId: "FIN-DEMO-002", title: "Турнир", type: "INCOME", amount: 32_000 },
    { entryId: "FIN-DEMO-003", title: "Зарплата педагогов", type: "EXPENSE", amount: 82_700 },
    { entryId: "FIN-DEMO-004", title: "Аренда залов", type: "EXPENSE", amount: 54_000 }
  ]
};

export function createInitialAdminState(): AdminState {
  return JSON.parse(JSON.stringify(INITIAL_ADMIN_STATE)) as AdminState;
}
