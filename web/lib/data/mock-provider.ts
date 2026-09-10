import type { DataProvider } from "@/lib/data/provider";
import { createInitialAdminState, createInitialParentState, createInitialTeacherState, DASHBOARDS, DEMO_USERS } from "@/lib/data/mock-data";
import { calculateTeacherEarned, DEMO_TODAY, lessonTitle, validateRecordLesson } from "@/lib/domain/teacher-lessons";
import type { AdminRequestStatus, AdminState, DemoInvoice, DemoPaymentResult, DemoSession, ParentState, RecordLessonInput, TeacherState, UserRole } from "@/lib/domain/types";

const SESSION_KEY = "fokus.demo.session.v1";
const PARENT_STATE_KEY = "fokus.demo.parent.v1";
const TEACHER_STATE_KEY = "fokus.demo.teacher.v1";
const ADMIN_STATE_KEY = "fokus.demo.admin.v1";
let memorySession: DemoSession | null = null;
let memoryParentState: ParentState = createInitialParentState();
let memoryTeacherState: TeacherState = createInitialTeacherState();
let memoryAdminState: AdminState = createInitialAdminState();

function readParentState(): ParentState {
  if (typeof window === "undefined") return memoryParentState;
  try {
    const raw = window.localStorage.getItem(PARENT_STATE_KEY);
    if (!raw) return memoryParentState;
    memoryParentState = JSON.parse(raw) as ParentState;
  } catch {
    // Keep the in-memory copy when storage is unavailable or damaged.
  }
  return memoryParentState;
}

function writeParentState(state: ParentState): ParentState {
  memoryParentState = state;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(PARENT_STATE_KEY, JSON.stringify(state));
    } catch {
      // The in-memory copy remains available for this browser session.
    }
  }
  return state;
}

function readTeacherState(): TeacherState {
  if (typeof window === "undefined") return memoryTeacherState;
  try {
    const raw = window.localStorage.getItem(TEACHER_STATE_KEY);
    if (!raw) return memoryTeacherState;
    memoryTeacherState = JSON.parse(raw) as TeacherState;
  } catch {
    // Keep the in-memory copy when storage is unavailable or damaged.
  }
  return memoryTeacherState;
}

function writeTeacherState(state: TeacherState): TeacherState {
  memoryTeacherState = state;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(TEACHER_STATE_KEY, JSON.stringify(state));
    } catch {
      // The in-memory copy remains available for this browser session.
    }
  }
  return state;
}

function readAdminState(): AdminState {
  if (typeof window === "undefined") return memoryAdminState;
  try {
    const raw = window.localStorage.getItem(ADMIN_STATE_KEY);
    if (!raw) return memoryAdminState;
    memoryAdminState = JSON.parse(raw) as AdminState;
  } catch {
    // Keep the in-memory copy when storage is unavailable or damaged.
  }
  return memoryAdminState;
}

function writeAdminState(state: AdminState): AdminState {
  memoryAdminState = state;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(ADMIN_STATE_KEY, JSON.stringify(state));
    } catch {
      // The in-memory copy remains available for this browser session.
    }
  }
  return state;
}

export class MockDataProvider implements DataProvider {
  async getSession(): Promise<DemoSession | null> {
    if (typeof window === "undefined") return memorySession;
    try {
      const raw = window.localStorage.getItem(SESSION_KEY);
      if (!raw) return memorySession;
      return JSON.parse(raw) as DemoSession;
    } catch {
      return memorySession;
    }
  }

  async loginAs(role: UserRole): Promise<DemoSession> {
    const user = DEMO_USERS[role];
    const session: DemoSession = { ...user, role, authMethod: "demo" };
    memorySession = session;
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(SESSION_KEY, JSON.stringify(session));
      } catch {
        // Private/embedded browsers may block storage; in-memory demo still works.
      }
    }
    return session;
  }

  async logout(): Promise<void> {
    memorySession = null;
    if (typeof window !== "undefined") {
      try {
        window.localStorage.removeItem(SESSION_KEY);
      } catch {
        // Nothing else to clear when browser storage is unavailable.
      }
    }
  }

  async getDashboard(role: UserRole) {
    return DASHBOARDS[role];
  }

  async getParentState(): Promise<ParentState> {
    return readParentState();
  }

  async setCartKeys(keys: string[]): Promise<ParentState> {
    const state = readParentState();
    const selectable = new Set(state.payables.filter((item) => item.status === "UNPAID").map((item) => item.payableKey));
    return writeParentState({ ...state, cartKeys: [...new Set(keys)].filter((key) => selectable.has(key)) });
  }

  async checkoutCart(method: DemoInvoice["method"], result: DemoPaymentResult): Promise<ParentState> {
    const state = readParentState();
    const selected = state.payables.filter((item) => state.cartKeys.includes(item.payableKey) && item.status === "UNPAID");
    if (!selected.length) return state;

    const childIds = [...new Set(selected.map((item) => item.childId))];
    const nextNumber = state.invoices.length + 1;
    const status = result === "CANCELLED" ? "CANCELLED" : result;
    const invoices: DemoInvoice[] = childIds.map((childId, index) => {
      const childItems = selected.filter((item) => item.childId === childId);
      return {
        invoiceId: `INV-DEMO-${String(nextNumber + index).padStart(3, "0")}`,
        childId,
        createdAt: new Date().toISOString(),
        amount: childItems.reduce((sum, item) => sum + item.amount, 0),
        status,
        method,
        itemKeys: childItems.map((item) => item.payableKey)
      };
    });

    const selectedKeys = new Set(selected.map((item) => item.payableKey));
    const payables = state.payables.map((item) => {
      if (!selectedKeys.has(item.payableKey) || result === "CANCELLED") return item;
      return { ...item, status: result };
    });

    return writeParentState({ ...state, payables, invoices: [...invoices, ...state.invoices], cartKeys: [] });
  }

  async payInvoice(invoiceId: string): Promise<ParentState> {
    const state = readParentState();
    const invoice = state.invoices.find((item) => item.invoiceId === invoiceId);
    if (!invoice) throw new Error("Счёт не найден");
    if (invoice.status === "PAID") return state;

    const itemKeys = new Set(invoice.itemKeys);
    const invoices = state.invoices.map((item) =>
      item.invoiceId === invoiceId ? { ...item, status: "PAID" as const, method: "SBP" as const } : item
    );
    const payables = state.payables.map((item) =>
      itemKeys.has(item.payableKey) ? { ...item, status: "PAID" as const } : item
    );
    return writeParentState({ ...state, invoices, payables });
  }

  async getTeacherState(): Promise<TeacherState> {
    return readTeacherState();
  }

  async recordTeacherLesson(input: RecordLessonInput): Promise<TeacherState> {
    const state = readTeacherState();
    const validationError = validateRecordLesson(state, input);
    if (validationError) throw new Error(validationError);
    const lesson = {
      lessonId: `LES-T-DEMO-${String(state.lessons.length + 1).padStart(3, "0")}`,
      kind: input.kind,
      title: lessonTitle(input, state),
      date: input.date,
      durationMin: input.durationMin,
      groupId: input.groupId,
      studentIds: [...input.studentIds].sort(),
      earned: calculateTeacherEarned(input.kind, input.durationMin)
    };
    return writeTeacherState({ ...state, lessons: [lesson, ...state.lessons] });
  }

  async submitTeacherPeriod(periodMonth: string): Promise<TeacherState> {
    const state = readTeacherState();
    if (state.submittedPeriods.includes(periodMonth)) return state;
    if (periodMonth === DEMO_TODAY.slice(0, 7) && Number(DEMO_TODAY.slice(8, 10)) < 25) {
      throw new Error("Сдать текущий период можно только с 25-го числа");
    }
    return writeTeacherState({ ...state, submittedPeriods: [...state.submittedPeriods, periodMonth] });
  }

  async getAdminState(): Promise<AdminState> {
    return readAdminState();
  }

  async confirmAdminPayment(invoiceId: string): Promise<AdminState> {
    const state = readAdminState();
    const payment = state.payments.find((item) => item.invoiceId === invoiceId);
    if (!payment) throw new Error("Счёт не найден");
    if (payment.status !== "PENDING") return state;
    const payments = state.payments.map((item) => item.invoiceId === invoiceId ? { ...item, status: "PAID" as const } : item);
    const students = state.students.map((student) => student.name === payment.childName ? { ...student, balance: Math.max(0, student.balance - payment.amount) } : student);
    return writeAdminState({ ...state, payments, students });
  }

  async resolveAdminChildRequest(requestId: string, status: Exclude<AdminRequestStatus, "PENDING">): Promise<AdminState> {
    const state = readAdminState();
    if (!state.childRequests.some((request) => request.requestId === requestId)) throw new Error("Заявка не найдена");
    const childRequests = state.childRequests.map((request) => request.requestId === requestId ? { ...request, status } : request);
    return writeAdminState({ ...state, childRequests });
  }

  async reset(): Promise<void> {
    memorySession = null;
    memoryParentState = createInitialParentState();
    memoryTeacherState = createInitialTeacherState();
    memoryAdminState = createInitialAdminState();
    if (typeof window !== "undefined") {
      try {
        window.localStorage.clear();
      } catch {
        // In-memory state is already reset.
      }
    }
  }
}
