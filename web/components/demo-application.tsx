"use client";

import { useEffect, useMemo, useState } from "react";
import type { DemoSession, RoleDashboard, UserRole } from "@/lib/domain/types";
import { MockDataProvider } from "@/lib/data/mock-provider";
import { ParentPortal } from "@/components/parent/parent-portal";
import { TeacherPortal } from "@/components/teacher/teacher-portal";
import { AdminPortal } from "@/components/admin/admin-portal";

const ROLE_LABELS: Record<UserRole, { title: string; description: string; mark: string }> = {
  client: { title: "Родитель", description: "Занятия детей и оплата", mark: "Р" },
  teacher: { title: "Педагог", description: "Группы, занятия и период", mark: "П" },
  admin: { title: "Администратор", description: "Школа, финансы и контроль", mark: "А" }
};

export function DemoApplication() {
  const provider = useMemo(() => new MockDataProvider(), []);
  const [session, setSession] = useState<DemoSession | null>(null);
  const [dashboard, setDashboard] = useState<RoleDashboard | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    provider.getSession().then((value) => {
      setSession(value);
      setLoading(false);
    });
  }, [provider]);

  useEffect(() => {
    if (!session) {
      setDashboard(null);
      return;
    }
    provider.getDashboard(session.role).then(setDashboard);
  }, [provider, session]);

  async function login(role: UserRole) {
    setSession(await provider.loginAs(role));
  }

  async function logout() {
    await provider.logout();
    setSession(null);
  }

  if (loading) {
    return <div className="grid min-h-screen place-items-center text-muted">Загружаем кабинет…</div>;
  }

  if (!session) return <LoginScreen onLogin={login} />;
  if (session.role === "client") return <ParentPortal provider={provider} onLogout={logout} />;
  if (session.role === "teacher") return <TeacherPortal provider={provider} onLogout={logout} />;
  if (session.role === "admin") return <AdminPortal provider={provider} onLogout={logout} />;
  return <DashboardScreen session={session} dashboard={dashboard} onLogout={logout} />;
}

function LoginScreen({ onLogin }: { onLogin: (role: UserRole) => void }) {
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-6xl items-center px-4 py-8 sm:px-8">
      <div className="grid w-full overflow-hidden rounded-4xl border border-white/70 bg-surface/90 shadow-card backdrop-blur md:grid-cols-[1.05fr_1fr]">
        <section className="relative overflow-hidden bg-ink p-7 text-white sm:p-10 md:min-h-[650px]">
          <div className="absolute -right-20 -top-20 h-72 w-72 rounded-full border-[56px] border-accent/30" />
          <div className="relative flex h-full flex-col justify-between gap-20">
            <div>
              <div className="mb-10 inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-brand text-xl font-bold">Ф</div>
              <p className="mb-3 text-xs font-bold uppercase tracking-[0.22em] text-accent">Танцевальная школа</p>
              <h1 className="max-w-md text-4xl font-semibold leading-tight sm:text-5xl">Всё важное — в фокусе</h1>
              <p className="mt-5 max-w-md text-base leading-7 text-white/70">Занятия, группы, оплаты и работа школы в одном спокойном интерфейсе.</p>
            </div>
            <p className="text-sm text-white/50">Frontend-прототип · данные сохраняются только на этом устройстве</p>
          </div>
        </section>

        <section className="p-6 sm:p-10">
          <div className="mb-8 rounded-2xl border border-accent/40 bg-accent/10 px-4 py-3 text-sm text-ink">
            <strong>Демонстрационный режим.</strong> Авторизация и платежи не выполняются.
          </div>
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-brand">Войти в кабинет</p>
          <h2 className="mt-2 text-3xl font-semibold">Выберите роль</h2>
          <p className="mt-3 text-sm leading-6 text-muted">На этом этапе доступны общий каркас и стартовые панели трёх ролей.</p>

          <div className="mt-8 space-y-3">
            {(Object.keys(ROLE_LABELS) as UserRole[]).map((role) => {
              const item = ROLE_LABELS[role];
              return (
                <button
                  key={role}
                  type="button"
                  onClick={() => onLogin(role)}
                  className="group flex min-h-20 w-full items-center gap-4 rounded-3xl border border-black/10 bg-white px-4 py-3 text-left transition hover:-translate-y-0.5 hover:border-brand/40 hover:shadow-lg"
                >
                  <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-paper font-bold text-brand transition group-hover:bg-brand group-hover:text-white">{item.mark}</span>
                  <span className="flex-1">
                    <span className="block font-semibold">{item.title}</span>
                    <span className="mt-0.5 block text-sm text-muted">{item.description}</span>
                  </span>
                  <span aria-hidden="true" className="text-xl text-muted">→</span>
                </button>
              );
            })}
          </div>

          <div className="mt-8 grid grid-cols-2 gap-3">
            <button disabled className="min-h-12 rounded-2xl border border-black/10 text-sm text-muted disabled:opacity-60">Через Telegram</button>
            <button disabled className="min-h-12 rounded-2xl border border-black/10 text-sm text-muted disabled:opacity-60">По телефону</button>
          </div>
        </section>
      </div>
    </main>
  );
}

function DashboardScreen({ session, dashboard, onLogout }: { session: DemoSession; dashboard: RoleDashboard | null; onLogout: () => void }) {
  return (
    <main className="mx-auto min-h-screen w-full max-w-6xl px-4 pb-24 pt-5 sm:px-8 sm:pt-8">
      <header className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="grid h-11 w-11 place-items-center rounded-2xl bg-ink font-bold text-white">Ф</div>
          <div><p className="font-semibold">Фокус</p><p className="text-xs text-muted">Демо-кабинет</p></div>
        </div>
        <button onClick={onLogout} className="min-h-11 rounded-2xl border border-black/10 bg-surface px-4 text-sm font-medium hover:border-brand/40">Сменить роль</button>
      </header>

      <section className="mt-10">
        <p className="text-xs font-bold uppercase tracking-[0.18em] text-brand">{dashboard?.eyebrow}</p>
        <h1 className="mt-2 max-w-3xl text-4xl font-semibold leading-tight sm:text-5xl">{dashboard?.title ?? "Загрузка…"}</h1>
        <p className="mt-4 max-w-2xl leading-7 text-muted">{dashboard?.description}</p>
      </section>

      <section aria-label="Ключевые показатели" className="mt-9 grid gap-4 md:grid-cols-3">
        {dashboard?.metrics.map((metric, index) => (
          <article key={metric.label} className="rounded-3xl border border-white/80 bg-surface/90 p-5 shadow-card">
            <div className="mb-5 flex items-center justify-between"><p className="text-sm text-muted">{metric.label}</p><span className="text-xs font-bold text-brand">0{index + 1}</span></div>
            <p className="text-2xl font-semibold">{metric.value}</p>
            <p className="mt-2 text-sm leading-5 text-muted">{metric.hint}</p>
          </article>
        ))}
      </section>

      <section className="mt-8 rounded-4xl bg-ink p-6 text-white sm:p-8">
        <div className="flex flex-col justify-between gap-6 sm:flex-row sm:items-end">
          <div><p className="text-xs font-bold uppercase tracking-[0.18em] text-accent">Следующий этап</p><h2 className="mt-2 text-2xl font-semibold">Рабочие разделы роли</h2><p className="mt-2 max-w-xl text-sm leading-6 text-white/60">Навигация уже готова к подключению экранов без изменения источника данных.</p></div>
          <div className="flex flex-wrap gap-2">{dashboard?.nextActions.map((action) => <span key={action} className="rounded-full bg-white/10 px-3 py-2 text-sm">{action}</span>)}</div>
        </div>
      </section>

      <footer className="mt-8 flex flex-wrap items-center justify-between gap-3 text-xs text-muted">
        <span>{session.displayName}</span><span>Данные: MockDataProvider · backend не подключён</span>
      </footer>
    </main>
  );
}
