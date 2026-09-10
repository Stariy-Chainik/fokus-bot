"use client";

import { useEffect, useMemo, useState } from "react";
import type { DataProvider } from "@/lib/data/provider";
import { cartTotal, mergeUniqueKeys, selectableKeys } from "@/lib/domain/cart";
import type { DemoInvoice, DemoPaymentResult, ParentState, PayableItem, PaymentDisplayStatus } from "@/lib/domain/types";

type ParentTab = "home" | "lessons" | "cart" | "bills";
type PeriodFilter = "ALL" | "WEEK" | "MONTH";

const DEMO_TODAY = "2026-07-11";
const DEMO_WEEK_END = "2026-07-18";

const STATUS_META: Record<PaymentDisplayStatus, { label: string; className: string }> = {
  NOT_CHARGEABLE: { label: "Не оплачивается", className: "bg-black/5 text-muted" },
  UNPAID: { label: "Не оплачено", className: "bg-brand/10 text-brand" },
  RESERVED: { label: "Зарезервировано", className: "bg-accent/20 text-amber-800" },
  PENDING: { label: "Ждёт подтверждения", className: "bg-blue-100 text-blue-800" },
  PAID: { label: "Оплачено", className: "bg-emerald-100 text-emerald-800" }
};

const METHOD_LABELS: Record<DemoInvoice["method"], string> = {
  CARD: "Картой онлайн",
  SBP: "СБП",
  BANK: "По реквизитам",
  CASH: "Наличными"
};

export function ParentPortal({ provider, onLogout }: { provider: DataProvider; onLogout: () => void }) {
  const [state, setState] = useState<ParentState | null>(null);
  const [tab, setTab] = useState<ParentTab>("home");
  const [childId, setChildId] = useState<string>("ALL");
  const [period, setPeriod] = useState<PeriodFilter>("ALL");
  const [method, setMethod] = useState<DemoInvoice["method"]>("CARD");

  useEffect(() => {
    provider.getParentState().then(setState);
  }, [provider]);

  const selectedItems = useMemo(() => {
    if (!state) return [];
    const keys = new Set(state.cartKeys);
    return state.payables.filter((item) => keys.has(item.payableKey));
  }, [state]);

  async function setCart(keys: string[]) {
    setState(await provider.setCartKeys(keys));
  }

  async function toggleItem(item: PayableItem) {
    if (!state || item.status !== "UNPAID") return;
    const exists = state.cartKeys.includes(item.payableKey);
    await setCart(exists ? state.cartKeys.filter((key) => key !== item.payableKey) : [...state.cartKeys, item.payableKey]);
  }

  async function selectRange(range: "WEEK" | "MONTH") {
    if (!state) return;
    const added = selectableKeys(
      state.payables,
      childId === "ALL" ? undefined : childId,
      range === "WEEK" ? DEMO_TODAY : "2026-07-01",
      range === "WEEK" ? DEMO_WEEK_END : "2026-07-31"
    );
    await setCart(mergeUniqueKeys(state.cartKeys, added));
    setTab("cart");
  }

  async function checkout(result: DemoPaymentResult) {
    setState(await provider.checkoutCart(method, result));
    setTab("bills");
  }

  async function payInvoice(invoiceId: string) {
    setState(await provider.payInvoice(invoiceId));
  }

  if (!state) return <div className="grid min-h-screen place-items-center text-muted">Загружаем занятия…</div>;

  const unpaid = state.payables.filter((item) => item.status === "UNPAID");
  const unpaidTotal = unpaid.reduce((sum, item) => sum + item.amount, 0);

  return (
    <div className="min-h-screen bg-paper">
      <main className="mx-auto min-h-screen w-full max-w-6xl px-4 pb-28 pt-5 sm:px-8 sm:pt-8">
        <ParentHeader onLogout={onLogout} />
        <DesktopRail tab={tab} cartCount={state.cartKeys.length} onChange={setTab} />

      {tab === "home" && (
        <ParentHome
          state={state}
          unpaidTotal={unpaidTotal}
          onOpenLessons={() => setTab("lessons")}
          onOpenCart={() => setTab("cart")}
        />
      )}

      {tab === "lessons" && (
        <LessonsScreen
          state={state}
          childId={childId}
          period={period}
          onChildChange={setChildId}
          onPeriodChange={setPeriod}
          onToggle={toggleItem}
          onSelectRange={selectRange}
        />
      )}

      {tab === "cart" && (
        <CartScreen
          state={state}
          items={selectedItems}
          method={method}
          onMethodChange={setMethod}
          onRemove={toggleItem}
          onOpenLessons={() => setTab("lessons")}
          onCheckout={checkout}
        />
      )}

      {tab === "bills" && <BillsScreen state={state} onPay={payInvoice} />}

        <ParentNavigation tab={tab} cartCount={state.cartKeys.length} onChange={setTab} />
      </main>
    </div>
  );
}

function ParentHeader({ onLogout }: { onLogout: () => void }) {
  return (
    <header className="flex items-center justify-between gap-4">
      <div className="flex items-center gap-3">
        <div className="grid h-9 w-9 place-items-center rounded-full border border-black/10 bg-surface text-sm font-black">Ф</div>
        <div><p className="text-sm font-bold">Фокус</p><p className="text-[11px] text-muted">Кабинет родителя</p></div>
      </div>
      <div className="flex items-center gap-2"><span className="hidden rounded-full border border-black/10 bg-surface px-3 py-2 text-xs text-muted sm:inline">Демо · июль 2026</span><button onClick={onLogout} className="min-h-9 rounded-full bg-ink px-4 text-xs font-semibold text-white">Сменить роль</button></div>
    </header>
  );
}

function ParentHome({ state, unpaidTotal, onOpenLessons, onOpenCart }: { state: ParentState; unpaidTotal: number; onOpenLessons: () => void; onOpenCart: () => void }) {
  const nextLesson = state.payables.filter((item) => item.type === "LESSON" && item.date >= DEMO_TODAY).sort((a, b) => a.date.localeCompare(b.date))[0];
  const paidCount = state.payables.filter((item) => item.status === "PAID").length;

  return (
    <>
      <section className="mt-8 grid gap-7 border-b border-black/10 pb-6 lg:grid-cols-[1.2fr_.8fr] lg:items-end">
        <div>
          <p className="text-[11px] font-semibold text-muted">Личный кабинет / Семья Волковых</p>
          <h1 className="mt-2 text-3xl font-semibold leading-tight sm:text-4xl">Добрый день, Елена</h1>
          <p className="mt-3 max-w-xl text-sm leading-6 text-muted">Занятия Алисы и Максима, оплаты и ближайшие события — в одном месте.</p>
        </div>
        <div className="flex gap-3 lg:justify-end">
          {state.children.map((child) => <ChildAvatar key={child.childId} child={child} />)}
        </div>
      </section>

      <section aria-label="Сводка" className="mt-5 grid gap-3 md:grid-cols-3">
        <MetricCard label="Ближайшее занятие" value={nextLesson ? formatDate(nextLesson.date) : "Нет занятий"} hint={nextLesson?.subtitle ?? "Расписание свободно"} index="01" tone="pink" />
        <MetricCard label="К оплате" value={formatMoney(unpaidTotal)} hint={`${state.payables.filter((item) => item.status === "UNPAID").length} начислений доступны`} index="02" tone="yellow" />
        <MetricCard label="Уже оплачено" value={`${paidCount}`} hint="Занятий и абонементов" index="03" tone="lilac" />
      </section>

      <section className="mt-4 grid gap-3 lg:grid-cols-[1.25fr_.75fr]">
        <article className="overflow-hidden rounded-3xl border border-white/80 bg-surface p-5 shadow-card sm:p-6">
          <p className="text-[11px] font-semibold text-muted">Ближайшее событие</p>
          <h2 className="mt-4 text-2xl font-semibold">{nextLesson?.title ?? "Свободный день"}</h2>
          <p className="mt-2 text-sm text-muted">{nextLesson ? `${formatDate(nextLesson.date)} · ${nextLesson.subtitle}` : "Новых событий пока нет"}</p>
          <button onClick={onOpenLessons} className="mt-6 min-h-10 rounded-full bg-ink px-5 text-sm font-semibold text-white">Все занятия</button>
        </article>
        <article className="rounded-3xl border border-brand/20 bg-brand/10 p-5 sm:p-6">
          <p className="text-sm text-muted">В корзине</p>
          <p className="mt-2 text-3xl font-semibold">{state.cartKeys.length}</p>
          <p className="mt-2 text-sm leading-6 text-muted">Выбранные позиции сохраняются на этом устройстве.</p>
          <button onClick={onOpenCart} className="mt-6 min-h-10 w-full rounded-full bg-ink px-5 text-sm font-semibold text-white">Открыть корзину</button>
        </article>
      </section>
    </>
  );
}

function LessonsScreen({ state, childId, period, onChildChange, onPeriodChange, onToggle, onSelectRange }: {
  state: ParentState;
  childId: string;
  period: PeriodFilter;
  onChildChange: (value: string) => void;
  onPeriodChange: (value: PeriodFilter) => void;
  onToggle: (item: PayableItem) => void;
  onSelectRange: (range: "WEEK" | "MONTH") => void;
}) {
  const items = state.payables
    .filter((item) => childId === "ALL" || item.childId === childId)
    .filter((item) => period !== "WEEK" || (item.date >= DEMO_TODAY && item.date <= DEMO_WEEK_END))
    .filter((item) => period !== "MONTH" || item.periodMonth === "2026-07")
    .sort((a, b) => a.date.localeCompare(b.date));

  return (
    <section className="mt-10">
      <p className="text-xs font-bold uppercase tracking-[0.18em] text-brand">Занятия и начисления</p>
      <div className="mt-2 flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div><h1 className="text-4xl font-semibold">Июль 2026</h1><p className="mt-2 text-muted">Статус оплаты приходит готовым — интерфейс его не рассчитывает.</p></div>
        <div className="flex flex-wrap gap-2">
          <button onClick={() => onSelectRange("WEEK")} className="min-h-11 rounded-2xl border border-brand/30 bg-brand/5 px-4 text-sm font-semibold text-brand">В корзину неделю</button>
          <button onClick={() => onSelectRange("MONTH")} className="min-h-11 rounded-2xl bg-brand px-4 text-sm font-semibold text-white">В корзину месяц</button>
        </div>
      </div>

      <div className="mt-7 flex gap-2 overflow-x-auto pb-2">
        <FilterChip active={childId === "ALL"} onClick={() => onChildChange("ALL")}>Все дети</FilterChip>
        {state.children.map((child) => <FilterChip key={child.childId} active={childId === child.childId} onClick={() => onChildChange(child.childId)}>{child.shortName}</FilterChip>)}
      </div>
      <div className="mt-2 flex gap-2 overflow-x-auto pb-2">
        <FilterChip active={period === "ALL"} onClick={() => onPeriodChange("ALL")}>Все</FilterChip>
        <FilterChip active={period === "WEEK"} onClick={() => onPeriodChange("WEEK")}>Неделя</FilterChip>
        <FilterChip active={period === "MONTH"} onClick={() => onPeriodChange("MONTH")}>Месяц</FilterChip>
      </div>

      <div className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {items.map((item) => {
          const child = state.children.find((entry) => entry.childId === item.childId)!;
          const inCart = state.cartKeys.includes(item.payableKey);
          return <PayableCard key={item.payableKey} item={item} child={child.shortName} inCart={inCart} onToggle={() => onToggle(item)} />;
        })}
      </div>
    </section>
  );
}

function CartScreen({ state, items, method, onMethodChange, onRemove, onOpenLessons, onCheckout }: {
  state: ParentState;
  items: PayableItem[];
  method: DemoInvoice["method"];
  onMethodChange: (method: DemoInvoice["method"]) => void;
  onRemove: (item: PayableItem) => void;
  onOpenLessons: () => void;
  onCheckout: (result: DemoPaymentResult) => void;
}) {
  const total = cartTotal(state.payables, state.cartKeys);
  const childGroups = state.children.map((child) => ({ child, items: items.filter((item) => item.childId === child.childId) })).filter((group) => group.items.length);

  if (!items.length) {
    return (
      <section className="mt-10 rounded-4xl border border-dashed border-black/15 bg-surface/70 px-6 py-16 text-center">
        <div className="mx-auto grid h-16 w-16 place-items-center rounded-3xl bg-paper text-2xl">◎</div>
        <h1 className="mt-5 text-3xl font-semibold">Корзина пока пуста</h1>
        <p className="mx-auto mt-3 max-w-md text-muted">Выберите неоплаченные занятия или абонемент. Сумма будет собрана автоматически.</p>
        <button onClick={onOpenLessons} className="mt-7 min-h-12 rounded-2xl bg-brand px-5 font-semibold text-white">Выбрать занятия</button>
      </section>
    );
  }

  return (
    <section className="mt-10 grid gap-6 lg:grid-cols-[1fr_380px] lg:items-start">
      <div>
        <p className="text-xs font-bold uppercase tracking-[0.18em] text-brand">Корзина оплаты</p>
        <h1 className="mt-2 text-4xl font-semibold">Проверьте начисления</h1>
        <p className="mt-3 text-muted">Для каждого ребёнка будет создан отдельный демонстрационный счёт.</p>
        <div className="mt-7 space-y-5">
          {childGroups.map(({ child, items: childItems }) => (
            <article key={child.childId} className="rounded-4xl border border-white/80 bg-surface/90 p-5 shadow-card">
              <div className="flex items-center justify-between gap-3 border-b border-black/5 pb-4"><ChildAvatar child={child} /><strong>{formatMoney(childItems.reduce((sum, item) => sum + item.amount, 0))}</strong></div>
              <div className="divide-y divide-black/5">
                {childItems.map((item) => (
                  <div key={item.payableKey} className="flex items-start justify-between gap-4 py-4">
                    <div><p className="font-medium">{item.title}</p><p className="mt-1 text-sm text-muted">{formatDate(item.date)} · {item.subtitle}</p></div>
                    <div className="text-right"><p className="font-semibold">{formatMoney(item.amount)}</p><button onClick={() => onRemove(item)} className="mt-1 text-xs font-semibold text-brand">Убрать</button></div>
                  </div>
                ))}
              </div>
            </article>
          ))}
        </div>
      </div>

      <aside className="rounded-4xl bg-ink p-6 text-white lg:sticky lg:top-6">
        <p className="text-sm text-white/60">Итого</p><p className="mt-1 text-4xl font-semibold">{formatMoney(total)}</p>
        <p className="mt-6 text-sm font-semibold">Способ оплаты</p>
        <div className="mt-3 grid grid-cols-2 gap-2">
          {(Object.keys(METHOD_LABELS) as DemoInvoice["method"][]).map((value) => (
            <button key={value} onClick={() => onMethodChange(value)} className={`min-h-12 rounded-2xl border px-3 text-sm ${method === value ? "border-accent bg-accent text-ink" : "border-white/15 bg-white/5 text-white"}`}>{METHOD_LABELS[value]}</button>
          ))}
        </div>
        <div className="mt-6 rounded-2xl bg-white/10 p-4 text-sm leading-6 text-white/70">Демо: выберите результат платежа. Реальные деньги и данные никуда не отправляются.</div>
        <div className="mt-4 grid gap-2">
          <button onClick={() => onCheckout("PAID")} className="min-h-12 rounded-2xl bg-brand font-semibold text-white">Имитировать успешную оплату</button>
          <button onClick={() => onCheckout("PENDING")} className="min-h-12 rounded-2xl bg-white/10 font-semibold">Оставить на подтверждении</button>
          <button onClick={() => onCheckout("CANCELLED")} className="min-h-11 text-sm text-white/60">Отменить демонстрационный платёж</button>
        </div>
      </aside>
    </section>
  );
}

function BillsScreen({ state, onPay }: { state: ParentState; onPay: (invoiceId: string) => void }) {
  return (
    <section className="mt-10">
      <p className="text-xs font-bold uppercase tracking-[0.18em] text-brand">История</p>
      <h1 className="mt-2 text-4xl font-semibold">Счета и оплаты</h1>
      <p className="mt-3 text-muted">Каждый счёт относится только к одному ребёнку. Неоплаченный счёт можно оплатить прямо отсюда.</p>
      <div className="mt-7 grid gap-3">
        {state.invoices.map((invoice) => {
          const child = state.children.find((entry) => entry.childId === invoice.childId)!;
          const status: PaymentDisplayStatus = invoice.status === "PAID" ? "PAID" : invoice.status === "PENDING" ? "PENDING" : "UNPAID";
          return (
            <article key={invoice.invoiceId} className="flex flex-col gap-4 rounded-3xl border border-white/80 bg-surface/90 p-5 shadow-card sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-center gap-4"><ChildAvatar child={child} /><div><p className="font-semibold">{invoice.invoiceId}</p><p className="mt-1 text-sm text-muted">{formatInvoiceDate(invoice.createdAt)} · {METHOD_LABELS[invoice.method]} · {invoice.itemKeys.length} поз.</p></div></div>
              <div className="flex items-center justify-between gap-4 sm:justify-end">
                <StatusBadge status={status} />
                <strong className="text-lg">{formatMoney(invoice.amount)}</strong>
                {invoice.status !== "PAID" && (
                  <button onClick={() => onPay(invoice.invoiceId)} className="min-h-10 rounded-full bg-brand px-4 text-xs font-semibold text-white">
                    Оплатить · СБП
                  </button>
                )}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function PayableCard({ item, child, inCart, onToggle }: { item: PayableItem; child: string; inCart: boolean; onToggle: () => void }) {
  const tone = item.type === "SUBSCRIPTION" ? "bg-accent/20" : item.childId.includes("ALISA") ? "bg-brand/10" : "bg-surface";
  return (
    <article className={`flex min-h-48 flex-col justify-between rounded-3xl border border-white/80 p-5 shadow-card ${tone} ${inCart ? "ring-2 ring-brand ring-offset-2 ring-offset-paper" : ""}`}>
      <div><div className="flex items-start justify-between gap-3"><div><p className="text-[11px] font-semibold text-black/55">{child} · {formatDate(item.date)}</p><h2 className="mt-2 font-semibold leading-tight">{item.title}</h2></div><span className="grid h-7 min-w-7 place-items-center rounded-full bg-white/55 px-2 text-xs font-bold">{item.amount ? formatMoney(item.amount) : "—"}</span></div><p className="mt-3 text-xs leading-5 text-black/60">{item.subtitle}</p></div>
      <div className="mt-5 flex items-end justify-between gap-2"><StatusBadge status={item.status} />{item.status === "UNPAID" && <button onClick={onToggle} aria-pressed={inCart} className="min-h-9 rounded-full bg-brand px-4 text-xs font-semibold text-white">{inCart ? "В корзине" : "Добавить"}</button>}</div>
    </article>
  );
}

function ParentNavigation({ tab, cartCount, onChange }: { tab: ParentTab; cartCount: number; onChange: (tab: ParentTab) => void }) {
  const items: Array<{ id: ParentTab; label: string; icon: string }> = [
    { id: "home", label: "Главная", icon: "⌂" },
    { id: "lessons", label: "Занятия", icon: "◇" },
    { id: "cart", label: "Корзина", icon: "◎" },
    { id: "bills", label: "Счета", icon: "▤" }
  ];
  return (
    <nav aria-label="Навигация родителя" className="fixed inset-x-3 bottom-3 z-20 mx-auto grid max-w-xl grid-cols-4 rounded-3xl border border-white/80 bg-surface/95 p-2 text-ink shadow-2xl backdrop-blur lg:hidden">
      {items.map((item) => <button key={item.id} onClick={() => onChange(item.id)} aria-current={tab === item.id ? "page" : undefined} className={`relative min-h-14 rounded-2xl px-2 text-xs transition ${tab === item.id ? "bg-brand text-white" : "text-muted hover:bg-brand/5"}`}><span aria-hidden="true" className="mb-1 block text-lg">{item.icon}</span>{item.label}{item.id === "cart" && cartCount > 0 && <span className="absolute right-2 top-1 grid h-5 min-w-5 place-items-center rounded-full bg-accent px-1 text-[10px] font-bold text-ink">{cartCount}</span>}</button>)}
    </nav>
  );
}

function FilterChip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button onClick={onClick} aria-pressed={active} className={`min-h-10 shrink-0 rounded-full px-4 text-sm font-semibold ${active ? "bg-brand text-white" : "border border-black/10 bg-surface text-muted"}`}>{children}</button>;
}

function StatusBadge({ status }: { status: PaymentDisplayStatus }) {
  const meta = STATUS_META[status];
  return <span className={`mt-1 inline-flex rounded-full px-2.5 py-1 text-[11px] font-bold ${meta.className}`}>{meta.label}</span>;
}

function ChildAvatar({ child }: { child: ParentState["children"][number] }) {
  const initials = child.name.split(" ").map((part) => part[0]).join("").slice(0, 2);
  return <div className="flex items-center gap-3"><span className={`grid h-11 w-11 place-items-center rounded-2xl font-bold ${child.color === "coral" ? "bg-brand/15 text-brand" : "bg-accent/25 text-amber-800"}`}>{initials}</span><span><strong className="block text-sm">{child.shortName}</strong><span className="text-xs text-muted">ученик</span></span></div>;
}

function DesktopRail({ tab, cartCount, onChange }: { tab: ParentTab; cartCount: number; onChange: (tab: ParentTab) => void }) {
  const items: Array<{ id: ParentTab; label: string; icon: string }> = [{ id: "home", label: "Главная", icon: "⌂" }, { id: "lessons", label: "Занятия", icon: "◇" }, { id: "cart", label: "Корзина", icon: "◎" }, { id: "bills", label: "Счета", icon: "▤" }];
  return <nav aria-label="Навигация родителя" className="mt-6 hidden items-center gap-2 rounded-3xl border border-white/80 bg-surface p-2 shadow-card lg:flex">{items.map((item) => <button key={item.id} onClick={() => onChange(item.id)} aria-current={tab === item.id ? "page" : undefined} className={`relative min-h-11 rounded-2xl px-5 text-sm font-semibold transition ${tab === item.id ? "bg-brand text-white" : "text-muted hover:bg-brand/5 hover:text-brand"}`}><span aria-hidden="true" className="mr-2">{item.icon}</span>{item.label}{item.id === "cart" && cartCount > 0 && <span className="ml-2 inline-grid h-5 min-w-5 place-items-center rounded-full bg-accent px-1 text-[10px] font-bold text-ink">{cartCount}</span>}</button>)}</nav>;
}

function MetricCard({ label, value, hint, index, tone }: { label: string; value: string; hint: string; index: string; tone: "pink" | "yellow" | "lilac" }) {
  const colors = { pink: "border-brand/20 bg-brand/10", yellow: "border-accent/40 bg-accent/20", lilac: "border-white/80 bg-surface" };
  return <article className={`rounded-3xl border p-5 shadow-card ${colors[tone]}`}><div className="mb-5 flex items-center justify-between"><p className="text-xs text-muted">{label}</p><span className="text-[10px] font-bold text-brand">{index}</span></div><p className="text-2xl font-semibold">{value}</p><p className="mt-2 text-xs leading-5 text-muted">{hint}</p></article>;
}

function formatMoney(amount: number): string { return `${new Intl.NumberFormat("ru-RU").format(amount)} ₽`; }
function formatDate(date: string): string { return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long" }).format(new Date(`${date}T12:00:00`)); }
function formatInvoiceDate(date: string): string { return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" }).format(new Date(date)); }
