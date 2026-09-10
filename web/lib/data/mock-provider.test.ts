import { beforeEach, describe, expect, it } from "vitest";
import { MockDataProvider } from "./mock-provider";

describe("MockDataProvider", () => {
  const provider = new MockDataProvider();

  beforeEach(async () => {
    await provider.reset();
  });

  it("creates and clears a demo client session", async () => {
    const session = await provider.loginAs("client");

    expect(session.role).toBe("client");
    expect(session.authMethod).toBe("demo");
    expect(await provider.getSession()).toEqual(session);

    await provider.logout();
    expect(await provider.getSession()).toBeNull();
  });

  it("returns a role-specific dashboard", async () => {
    const dashboard = await provider.getDashboard("teacher");

    expect(dashboard.eyebrow).toBe("Кабинет педагога");
    expect(dashboard.metrics).toHaveLength(3);
  });

  it("creates separate paid invoices for selected children", async () => {
    await provider.setCartKeys([
      "LESSON:STU-DEMO-ALISA:LES-DEMO-012",
      "SUB:STU-DEMO-MAX:GRP-DEMO-KIDS:2026-07"
    ]);

    const state = await provider.checkoutCart("CARD", "PAID");
    const created = state.invoices.slice(0, 2);

    expect(created).toHaveLength(2);
    expect(new Set(created.map((invoice) => invoice.childId)).size).toBe(2);
    expect(state.cartKeys).toEqual([]);
    expect(state.payables.find((item) => item.payableKey === "LESSON:STU-DEMO-ALISA:LES-DEMO-012")?.status).toBe("PAID");
  });

  it("pays a pending invoice from the bills screen via SBP", async () => {
    await provider.setCartKeys(["LESSON:STU-DEMO-ALISA:LES-DEMO-012"]);
    const withPending = await provider.checkoutCart("CASH", "PENDING");
    const invoice = withPending.invoices[0];
    expect(invoice.status).toBe("PENDING");

    const state = await provider.payInvoice(invoice.invoiceId);
    const paid = state.invoices.find((item) => item.invoiceId === invoice.invoiceId)!;

    expect(paid.status).toBe("PAID");
    expect(paid.method).toBe("SBP");
    expect(state.payables.find((item) => item.payableKey === "LESSON:STU-DEMO-ALISA:LES-DEMO-012")?.status).toBe("PAID");
  });

  it("records a valid teacher lesson and keeps it in demo state", async () => {
    const before = await provider.getTeacherState();
    const after = await provider.recordTeacherLesson({ kind: "PAIR", date: "2026-07-11", durationMin: 60, studentIds: ["STU-DEMO-DASHA", "STU-DEMO-MISHA"] });

    expect(after.lessons).toHaveLength(before.lessons.length + 1);
    expect(after.lessons[0].title).toBe("Парное занятие");
  });

  it("does not allow submitting July before the 25th", async () => {
    await expect(provider.submitTeacherPeriod("2026-07")).rejects.toThrow("25-го");
  });
});

describe("admin operations", () => {
  it("confirms a pending payment and reduces the student debt", async () => {
    const provider = new MockDataProvider();
    await provider.reset();
    const before = await provider.getAdminState();
    const payment = before.payments.find((item) => item.status === "PENDING")!;
    const oldBalance = before.students.find((student) => student.name === payment.childName)!.balance;

    const after = await provider.confirmAdminPayment(payment.invoiceId);

    expect(after.payments.find((item) => item.invoiceId === payment.invoiceId)?.status).toBe("PAID");
    expect(after.students.find((student) => student.name === payment.childName)?.balance).toBe(Math.max(0, oldBalance - payment.amount));
  });

  it("resolves a child link request", async () => {
    const provider = new MockDataProvider();
    await provider.reset();
    const state = await provider.resolveAdminChildRequest("REQ-DEMO-001", "APPROVED");
    expect(state.childRequests.find((request) => request.requestId === "REQ-DEMO-001")?.status).toBe("APPROVED");
  });
});
