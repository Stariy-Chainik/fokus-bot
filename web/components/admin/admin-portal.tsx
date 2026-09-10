"use client";

import { useEffect, useMemo, useState } from "react";
import type { DataProvider } from "@/lib/data/provider";
import type { AdminPayment, AdminRequestStatus, AdminState } from "@/lib/domain/types";

type AdminTab = "home" | "students" | "teachers" | "payments" | "finance";

const PAYMENT_METHODS: Record<AdminPayment["method"], string> = {
  CARD: "Картой",
  SBP: "СБП",
  BANK: "По реквизитам",
  CASH: "Наличными"
};

export function AdminPortal({ provider, onLogout }: { provider: DataProvider; onLogout: () => void }) {
  const [state, setState] = useState<AdminState | null>(null);
  const [tab, setTab] = useState<AdminTab>("home");

  useEffect(() => { provider.getAdminState().then(setState); }, [provider]);
  if (!state) return <div className="grid min-h-screen place-items-center text-muted">Загружаем кабинет администратора…</div>;

  async function confirmPayment(invoiceId: string) {
    setState(await provider.confirmAdminPayment(invoiceId));
  }

  async function resolveRequest(requestId: string, status: Exclude<AdminRequestStatus, "PENDING">) {
    setState(await provider.resolveAdminChildRequest(requestId, status));
  }

  return (
    <div className="min-h-screen bg-paper">
      <main className="mx-auto min-h-screen w-full max-w-6xl px-4 pb-28 pt-5 sm:px-8 sm:pt-8">
        <AdminHeader onLogout={onLogout} />
        <AdminDesktopNavigation tab={tab} onChange={setTab} />
        {tab === "home" && <AdminHome state={state} onChange={setTab} />}
        {tab === "students" && <AdminStudents state={state} />}
        {tab === "teachers" && <AdminTeachers state={state} />}
        {tab === "payments" && <AdminPayments state={state} onConfirm={confirmPayment} onResolveRequest={resolveRequest} />}
        {tab === "finance" && <AdminFinance state={state} />}
        <AdminMobileNavigation tab={tab} onChange={setTab} />
      </main>
    </div>
  );
}

function AdminHeader({ onLogout }: { onLogout: () => void }) {
  return (
    <header className="flex items-center justify-between gap-4">
      <div className="flex items-center gap-3"><div className="grid h-10 w-10 place-items-center rounded-2xl bg-brand font-black text-white">Ф</div><div><p className="text-sm font-bold">Фокус</p><p className="text-[11px] text-muted">Панель администратора</p></div></div>
      <div className="flex items-center gap-2"><span className="hidden rounded-full border border-black/10 bg-surface px-3 py-2 text-xs text-muted sm:inline">Анна · 11 июля</span><button onClick={onLogout} className="min-h-9 rounded-full bg-ink px-4 text-xs font-semibold text-white">Сменить роль</button></div>
    </header>
  );
}

function AdminHome({ state, onChange }: { state: AdminState; onChange: (tab: AdminTab) => void }) {
  const debt = state.students.reduce((sum, student) => sum + student.balance, 0);
  const pendingPayments = state.payments.filter((payment) => payment.status === "PENDING");
  const pendingRequests = state.childRequests.filter((request) => request.status === "PENDING");
  const openPeriods = state.teachers.filter((teacher) => teacher.periodStatus === "OPEN").length;
  return (
    <>
      <section className="mt-8 border-b border-black/10 pb-6"><p className="text-[11px] font-semibold text-muted">ШКОЛА / СЕГОДНЯ</p><div className="mt-2 flex flex-col justify-between gap-4 sm:flex-row sm:items-end"><div><h1 className="text-3xl font-semibold sm:text-4xl">Добрый день, Анна</h1><p className="mt-3 max-w-2xl text-sm leading-6 text-muted">Оплаты, ученики и состояние периодов — в одной оперативной сводке.</p></div><button onClick={() => onChange("payments")} className="min-h-10 rounded-full bg-brand px-5 text-sm font-semibold text-white">Проверить оплаты</button></div></section>
      <section aria-label="Сводка администратора" className="mt-5 grid gap-3 md:grid-cols-3">
        <AdminMetric label="Общий долг" value={formatMoney(debt)} hint={`${state.students.filter((student) => student.balance > 0).length} учеников`} tone="coral" />
        <AdminMetric label="Ждут подтверждения" value={`${pendingPayments.length}`} hint={formatMoney(pendingPayments.reduce((sum, payment) => sum + payment.amount, 0))} tone="gold" />
        <AdminMetric label="Открытые периоды" value={`${openPeriods}`} hint={`из ${state.teachers.length} педагогов`} tone="white" />
      </section>
      <section className="mt-4 grid gap-3 lg:grid-cols-[1.15fr_.85fr]">
        <article className="rounded-3xl border border-white/80 bg-surface p-5 shadow-card sm:p-6"><div className="flex items-center justify-between gap-3"><div><p className="text-[11px] font-semibold text-muted">ТРЕБУЮТ ВНИМАНИЯ</p><h2 className="mt-2 text-xl font-semibold">Последние операции</h2></div><button onClick={() => onChange("payments")} className="rounded-full border border-brand/20 px-4 py-2 text-xs font-semibold text-brand">Открыть все</button></div><div className="mt-5 space-y-2">{pendingPayments.map((payment) => <div key={payment.invoiceId} className="flex items-center justify-between gap-4 rounded-2xl bg-paper px-4 py-3"><div><p className="text-sm font-semibold">{payment.childName}</p><p className="mt-1 text-xs text-muted">{PAYMENT_METHODS[payment.method]} · {payment.parentName}</p></div><strong className="text-sm">{formatMoney(payment.amount)}</strong></div>)}</div></article>
        <article className="rounded-3xl border border-brand/20 bg-brand/10 p-5 sm:p-6"><p className="text-[11px] font-semibold text-brand">НОВЫЕ ЗАЯВКИ</p><p className="mt-4 text-4xl font-semibold">{pendingRequests.length}</p><p className="mt-2 text-sm leading-6 text-muted">Родители ожидают привязки ребёнка к личному кабинету.</p><button onClick={() => onChange("payments")} className="mt-7 min-h-10 w-full rounded-full bg-ink px-5 text-sm font-semibold text-white">Рассмотреть заявки</button></article>
      </section>
    </>
  );
}

function AdminStudents({ state }: { state: AdminState }) {
  const [query, setQuery] = useState("");
  const students = useMemo(() => state.students.filter((student) => `${student.name} ${student.groupName} ${student.parentName}`.toLowerCase().includes(query.toLowerCase())), [query, state.students]);
  return <section className="mt-8"><div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end"><div><p className="text-[11px] font-semibold text-muted">БАЗА ШКОЛЫ</p><h1 className="mt-2 text-3xl font-semibold sm:text-4xl">Ученики</h1><p className="mt-2 text-sm text-muted">{state.students.length} записей в демонстрационной базе</p></div><label className="text-xs font-semibold text-muted">Поиск<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Имя, группа или родитель" className="mt-2 min-h-11 w-full rounded-2xl border border-black/10 bg-surface px-4 text-sm font-normal text-ink sm:w-72" /></label></div><div className="mt-7 overflow-hidden rounded-3xl border border-white/80 bg-surface shadow-card"><div className="hidden grid-cols-[1.1fr_1fr_1fr_auto] gap-4 border-b border-black/5 px-5 py-3 text-[11px] font-semibold text-muted md:grid"><span>Ученик</span><span>Группа</span><span>Родитель</span><span>Баланс</span></div>{students.map((student) => <article key={student.studentId} className="grid gap-3 border-b border-black/5 px-5 py-4 last:border-0 md:grid-cols-[1.1fr_1fr_1fr_auto] md:items-center"><div><p className="font-semibold">{student.name}</p><p className="mt-1 text-xs text-muted">{student.studentId}</p></div><p className="text-sm">{student.groupName}</p><p className="text-sm text-muted">{student.parentName}</p><span className={`justify-self-start rounded-full px-3 py-1.5 text-xs font-bold md:justify-self-end ${student.balance > 0 ? "bg-brand/10 text-brand" : "bg-emerald-100 text-emerald-800"}`}>{student.balance > 0 ? formatMoney(student.balance) : "Оплачено"}</span></article>)}</div></section>;
}

function AdminTeachers({ state }: { state: AdminState }) {
  return <section className="mt-8"><p className="text-[11px] font-semibold text-muted">КОМАНДА</p><h1 className="mt-2 text-3xl font-semibold sm:text-4xl">Педагоги</h1><p className="mt-2 text-sm text-muted">Занятия, начисления и готовность периода</p><div className="mt-7 grid gap-3 md:grid-cols-2 xl:grid-cols-3">{state.teachers.map((teacher, index) => <article key={teacher.teacherId} className={`rounded-3xl border p-5 shadow-card ${index === 0 ? "border-brand/20 bg-brand/10" : index === 1 ? "border-accent/40 bg-accent/20" : "border-white/80 bg-surface"}`}><div className="flex items-start justify-between gap-3"><div className="grid h-11 w-11 place-items-center rounded-2xl bg-white/70 font-bold">{initials(teacher.name)}</div><span className={`rounded-full px-2.5 py-1 text-[11px] font-bold ${teacher.periodStatus === "SUBMITTED" ? "bg-emerald-100 text-emerald-800" : "bg-white/70 text-muted"}`}>{teacher.periodStatus === "SUBMITTED" ? "Период сдан" : "Период открыт"}</span></div><h2 className="mt-5 text-xl font-semibold">{teacher.name}</h2><p className="mt-2 text-sm text-muted">{teacher.groupNames.join(" · ")}</p><dl className="mt-6 grid grid-cols-2 gap-3 border-t border-black/10 pt-4"><div><dt className="text-xs text-muted">Занятий</dt><dd className="mt-1 text-lg font-semibold">{teacher.lessonsThisMonth}</dd></div><div><dt className="text-xs text-muted">Начислено</dt><dd className="mt-1 text-lg font-semibold">{formatMoney(teacher.earned)}</dd></div></dl></article>)}</div></section>;
}

function AdminPayments({ state, onConfirm, onResolveRequest }: { state: AdminState; onConfirm: (invoiceId: string) => void; onResolveRequest: (requestId: string, status: Exclude<AdminRequestStatus, "PENDING">) => void }) {
  const requests = state.childRequests.filter((request) => request.status === "PENDING");
  return <section className="mt-8"><p className="text-[11px] font-semibold text-muted">КОНТРОЛЬ</p><h1 className="mt-2 text-3xl font-semibold sm:text-4xl">Оплаты и заявки</h1><div className="mt-7 grid gap-5 lg:grid-cols-[1.2fr_.8fr] lg:items-start"><div className="space-y-3">{state.payments.map((payment) => <article key={payment.invoiceId} className="flex flex-col gap-4 rounded-3xl border border-white/80 bg-surface p-5 shadow-card sm:flex-row sm:items-center sm:justify-between"><div><div className="flex flex-wrap items-center gap-2"><p className="font-semibold">{payment.childName}</p><PaymentBadge status={payment.status} /></div><p className="mt-2 text-sm text-muted">{payment.parentName} · {PAYMENT_METHODS[payment.method]}</p><p className="mt-1 text-xs text-muted">{payment.invoiceId} · {formatDateTime(payment.createdAt)}</p></div><div className="flex items-center justify-between gap-4 sm:justify-end"><strong className="text-lg">{formatMoney(payment.amount)}</strong>{payment.status === "PENDING" && <button onClick={() => onConfirm(payment.invoiceId)} className="min-h-10 rounded-full bg-brand px-4 text-xs font-semibold text-white">Подтвердить</button>}</div></article>)}</div><aside className="rounded-3xl border border-brand/20 bg-brand/10 p-5 lg:sticky lg:top-5"><p className="text-[11px] font-semibold text-brand">ПРИВЯЗКА РЕБЁНКА</p><h2 className="mt-2 text-xl font-semibold">Новые заявки</h2><div className="mt-5 space-y-3">{requests.length ? requests.map((request) => <article key={request.requestId} className="rounded-2xl bg-surface p-4"><p className="font-semibold">{request.childName}</p><p className="mt-1 text-sm text-muted">Родитель: {request.parentName}</p><div className="mt-4 grid grid-cols-2 gap-2"><button onClick={() => onResolveRequest(request.requestId, "APPROVED")} className="min-h-10 rounded-xl bg-brand text-xs font-semibold text-white">Принять</button><button onClick={() => onResolveRequest(request.requestId, "REJECTED")} className="min-h-10 rounded-xl border border-black/10 text-xs font-semibold">Отклонить</button></div></article>) : <p className="rounded-2xl bg-surface p-4 text-sm text-muted">Новых заявок нет.</p>}</div></aside></div></section>;
}

function AdminFinance({ state }: { state: AdminState }) {
  const income = state.financeEntries.filter((entry) => entry.type === "INCOME").reduce((sum, entry) => sum + entry.amount, 0);
  const expenses = state.financeEntries.filter((entry) => entry.type === "EXPENSE").reduce((sum, entry) => sum + entry.amount, 0);
  return <section className="mt-8"><p className="text-[11px] font-semibold text-muted">ИЮЛЬ 2026</p><h1 className="mt-2 text-3xl font-semibold sm:text-4xl">Финансы</h1><section className="mt-7 grid gap-3 md:grid-cols-3"><AdminMetric label="Доходы" value={formatMoney(income)} hint="Занятия и прочие доходы" tone="white" /><AdminMetric label="Расходы" value={formatMoney(expenses)} hint="Зарплата и аренда" tone="gold" /><AdminMetric label="Прибыль" value={formatMoney(income - expenses)} hint="Предварительный итог" tone="coral" /></section><div className="mt-5 rounded-3xl border border-white/80 bg-surface p-5 shadow-card"><h2 className="text-xl font-semibold">Операции месяца</h2><div className="mt-4 divide-y divide-black/5">{state.financeEntries.map((entry) => <div key={entry.entryId} className="flex items-center justify-between gap-4 py-4"><div><p className="font-medium">{entry.title}</p><p className="mt-1 text-xs text-muted">{entry.type === "INCOME" ? "Доход" : "Расход"}</p></div><strong className={entry.type === "INCOME" ? "text-emerald-700" : "text-brand"}>{entry.type === "INCOME" ? "+" : "−"}{formatMoney(entry.amount)}</strong></div>)}</div></div></section>;
}

function AdminMetric({ label, value, hint, tone }: { label: string; value: string; hint: string; tone: "coral" | "gold" | "white" }) {
  const colors = tone === "coral" ? "border-brand/20 bg-brand/10" : tone === "gold" ? "border-accent/40 bg-accent/20" : "border-white/80 bg-surface";
  return <article className={`rounded-3xl border p-5 shadow-card ${colors}`}><p className="text-xs text-muted">{label}</p><p className="mt-5 text-2xl font-semibold">{value}</p><p className="mt-2 text-xs leading-5 text-muted">{hint}</p></article>;
}

function PaymentBadge({ status }: { status: AdminPayment["status"] }) {
  const meta = status === "PAID" ? ["Оплачено", "bg-emerald-100 text-emerald-800"] : status === "PENDING" ? ["Проверить", "bg-accent/20 text-amber-800"] : ["Отменено", "bg-black/5 text-muted"];
  return <span className={`rounded-full px-2.5 py-1 text-[11px] font-bold ${meta[1]}`}>{meta[0]}</span>;
}

const ADMIN_NAV: Array<{ id: AdminTab; label: string; icon: string }> = [
  { id: "home", label: "Главная", icon: "⌂" },
  { id: "students", label: "Ученики", icon: "◇" },
  { id: "teachers", label: "Педагоги", icon: "○" },
  { id: "payments", label: "Оплаты", icon: "✓" },
  { id: "finance", label: "Финансы", icon: "▤" }
];

function AdminDesktopNavigation({ tab, onChange }: { tab: AdminTab; onChange: (tab: AdminTab) => void }) {
  return <nav aria-label="Навигация администратора" className="mt-6 hidden items-center gap-2 rounded-3xl border border-white/80 bg-surface p-2 shadow-card lg:flex">{ADMIN_NAV.map((item) => <button key={item.id} onClick={() => onChange(item.id)} aria-current={tab === item.id ? "page" : undefined} className={`min-h-11 rounded-2xl px-5 text-sm font-semibold transition ${tab === item.id ? "bg-brand text-white" : "text-muted hover:bg-brand/5 hover:text-brand"}`}><span aria-hidden="true" className="mr-2">{item.icon}</span>{item.label}</button>)}</nav>;
}

function AdminMobileNavigation({ tab, onChange }: { tab: AdminTab; onChange: (tab: AdminTab) => void }) {
  return <nav aria-label="Навигация администратора" className="fixed inset-x-2 bottom-2 z-20 mx-auto grid max-w-xl grid-cols-5 rounded-3xl border border-white/80 bg-surface/95 p-2 text-ink shadow-2xl backdrop-blur lg:hidden">{ADMIN_NAV.map((item) => <button key={item.id} onClick={() => onChange(item.id)} aria-current={tab === item.id ? "page" : undefined} className={`min-h-14 rounded-xl px-1 text-[10px] ${tab === item.id ? "bg-brand text-white" : "text-muted"}`}><span className="mb-1 block text-base">{item.icon}</span>{item.label}</button>)}</nav>;
}

function initials(name: string) { return name.split(" ").map((part) => part[0]).join("").slice(0, 2); }
function formatMoney(amount: number) { return `${new Intl.NumberFormat("ru-RU").format(amount)} ₽`; }
function formatDateTime(value: string) { return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
