import type { AdminRequestStatus, AdminState, DemoInvoice, DemoPaymentResult, DemoSession, ParentState, RecordLessonInput, RoleDashboard, TeacherState, UserRole } from "@/lib/domain/types";

export interface DataProvider {
  getSession(): Promise<DemoSession | null>;
  loginAs(role: UserRole): Promise<DemoSession>;
  logout(): Promise<void>;
  getDashboard(role: UserRole): Promise<RoleDashboard>;
  getParentState(): Promise<ParentState>;
  setCartKeys(keys: string[]): Promise<ParentState>;
  checkoutCart(method: DemoInvoice["method"], result: DemoPaymentResult): Promise<ParentState>;
  /** Оплата уже выставленного счёта из экрана «Счета» (будущая ЮКасса/СБП). */
  payInvoice(invoiceId: string): Promise<ParentState>;
  getTeacherState(): Promise<TeacherState>;
  recordTeacherLesson(input: RecordLessonInput): Promise<TeacherState>;
  submitTeacherPeriod(periodMonth: string): Promise<TeacherState>;
  getAdminState(): Promise<AdminState>;
  confirmAdminPayment(invoiceId: string): Promise<AdminState>;
  resolveAdminChildRequest(requestId: string, status: Exclude<AdminRequestStatus, "PENDING">): Promise<AdminState>;
  reset(): Promise<void>;
}
