export type UserRole = "admin" | "teacher" | "client";
export type AuthMethod = "demo" | "telegram" | "phone";

export interface DemoSession {
  actorId: string;
  displayName: string;
  role: UserRole;
  authMethod: AuthMethod;
}

export interface DashboardMetric {
  label: string;
  value: string;
  hint: string;
}

export interface RoleDashboard {
  eyebrow: string;
  title: string;
  description: string;
  metrics: DashboardMetric[];
  nextActions: string[];
}

export type PaymentDisplayStatus = "NOT_CHARGEABLE" | "UNPAID" | "RESERVED" | "PENDING" | "PAID";
export type PayableType = "LESSON" | "SUBSCRIPTION";
export type DemoPaymentResult = "PAID" | "PENDING" | "CANCELLED";

export interface Child {
  childId: string;
  name: string;
  shortName: string;
  color: "coral" | "gold";
}

export interface PayableItem {
  payableKey: string;
  type: PayableType;
  childId: string;
  title: string;
  subtitle: string;
  date: string;
  periodMonth: string;
  amount: number;
  status: PaymentDisplayStatus;
}

export interface DemoInvoice {
  invoiceId: string;
  childId: string;
  createdAt: string;
  amount: number;
  status: "PENDING" | "PAID" | "CANCELLED";
  method: "CARD" | "SBP" | "BANK" | "CASH";
  itemKeys: string[];
}

export interface ParentState {
  children: Child[];
  payables: PayableItem[];
  cartKeys: string[];
  invoices: DemoInvoice[];
}

export type TeacherLessonKind = "GROUP" | "PAIR" | "SOLOIST" | "SHARED";

export interface TeacherStudent {
  studentId: string;
  name: string;
  groupIds: string[];
}

export interface TeacherGroup {
  groupId: string;
  name: string;
  branchName: string;
  studentIds: string[];
  color: "pink" | "lilac" | "yellow";
}

export interface TeacherLesson {
  lessonId: string;
  kind: TeacherLessonKind;
  title: string;
  date: string;
  durationMin: number;
  groupId?: string;
  studentIds: string[];
  earned: number;
}

export interface RecordLessonInput {
  kind: TeacherLessonKind;
  date: string;
  durationMin: number;
  groupId?: string;
  studentIds: string[];
}

export interface TeacherState {
  teacherId: string;
  teacherName: string;
  groups: TeacherGroup[];
  students: TeacherStudent[];
  lessons: TeacherLesson[];
  submittedPeriods: string[];
}

export type AdminPaymentStatus = "PENDING" | "PAID" | "CANCELLED";
export type AdminRequestStatus = "PENDING" | "APPROVED" | "REJECTED";

export interface AdminStudent {
  studentId: string;
  name: string;
  groupName: string;
  parentName: string;
  balance: number;
}

export interface AdminTeacher {
  teacherId: string;
  name: string;
  groupNames: string[];
  lessonsThisMonth: number;
  earned: number;
  periodStatus: "OPEN" | "SUBMITTED";
}

export interface AdminPayment {
  invoiceId: string;
  parentName: string;
  childName: string;
  amount: number;
  method: DemoInvoice["method"];
  status: AdminPaymentStatus;
  createdAt: string;
}

export interface AdminChildRequest {
  requestId: string;
  parentName: string;
  childName: string;
  createdAt: string;
  status: AdminRequestStatus;
}

export interface AdminFinanceEntry {
  entryId: string;
  title: string;
  type: "INCOME" | "EXPENSE";
  amount: number;
}

export interface AdminState {
  students: AdminStudent[];
  teachers: AdminTeacher[];
  payments: AdminPayment[];
  childRequests: AdminChildRequest[];
  financeEntries: AdminFinanceEntry[];
}
