"use client";

import { useEffect, useMemo, useState } from "react";
import type { DataProvider } from "@/lib/data/provider";
import { DEMO_TODAY } from "@/lib/domain/teacher-lessons";
import type { RecordLessonInput, TeacherLessonKind, TeacherState } from "@/lib/domain/types";

type TeacherTab = "home" | "record" | "lessons" | "groups" | "period";

const KIND_META: Record<TeacherLessonKind, { label: string; description: string }> = {
  GROUP: { label: "Группа", description: "Одно групповое занятие" },
  PAIR: { label: "Пара", description: "Ровно два ученика" },
  SOLOIST: { label: "Солист", description: "Один ученик" },
  SHARED: { label: "Совместное", description: "От двух до четырёх" }
};

export function TeacherPortal({ provider, onLogout }: { provider: DataProvider; onLogout: () => void }) {
  const [state, setState] = useState<TeacherState | null>(null);
  const [tab, setTab] = useState<TeacherTab>("home");

  useEffect(() => { provider.getTeacherState().then(setState); }, [provider]);
  if (!state) return <div className="grid min-h-screen place-items-center text-muted">Загружаем кабинет педагога…</div>;

  return (
    <div className="min-h-screen bg-paper">
      <main className="mx-auto min-h-screen w-full max-w-6xl px-4 pb-28 pt-5 sm:px-8 sm:pt-8">
        <TeacherHeader onLogout={onLogout} />
        <TeacherRail tab={tab} onChange={setTab} />
        {tab === "home" && <TeacherHome state={state} onRecord={() => setTab("record")} onLessons={() => setTab("lessons")} />}
        {tab === "record" && <RecordLesson state={state} provider={provider} onSaved={(next) => { setState(next); setTab("lessons"); }} />}
        {tab === "lessons" && <TeacherLessons state={state} />}
        {tab === "groups" && <TeacherGroups state={state} />}
        {tab === "period" && <TeacherPeriod state={state} provider={provider} onChange={setState} />}
        <TeacherMobileNav tab={tab} onChange={setTab} />
      </main>
    </div>
  );
}

function TeacherHeader({ onLogout }: { onLogout: () => void }) {
  return <header className="flex items-center justify-between gap-4"><div className="flex items-center gap-3"><div className="grid h-9 w-9 place-items-center rounded-full border border-black/10 bg-surface text-sm font-black">Ф</div><div><p className="text-sm font-bold">Фокус</p><p className="text-[11px] text-muted">Кабинет педагога</p></div></div><div className="flex items-center gap-2"><span className="hidden rounded-full border border-black/10 bg-surface px-3 py-2 text-xs text-muted sm:inline">Мария Иванова</span><button onClick={onLogout} className="min-h-9 rounded-full bg-ink px-4 text-xs font-semibold text-white">Сменить роль</button></div></header>;
}

function TeacherHome({ state, onRecord, onLessons }: { state: TeacherState; onRecord: () => void; onLessons: () => void }) {
  const julyLessons = state.lessons.filter((lesson) => lesson.date.startsWith("2026-07"));
  const todayLessons = state.lessons.filter((lesson) => lesson.date === DEMO_TODAY);
  const earned = julyLessons.reduce((sum, lesson) => sum + lesson.earned, 0);
  return <>
    <section className="mt-8 border-b border-black/10 pb-6"><p className="text-[11px] font-semibold text-muted">Рабочая доска / 11 июля 2026</p><div className="mt-2 flex flex-col justify-between gap-4 sm:flex-row sm:items-end"><div><h1 className="text-3xl font-semibold sm:text-4xl">Рабочий день Марии</h1><p className="mt-3 text-sm text-muted">Занятия, группы и готовность периода.</p></div><button onClick={onRecord} className="min-h-10 rounded-full bg-ink px-5 text-sm font-semibold text-white">+ Записать занятие</button></div></section>
    <section className="mt-5 grid gap-3 md:grid-cols-3"><BoardMetric label="Сегодня" value={`${todayLessons.length} занятия`} hint="Последнее добавлено недавно" tone="pink" /><BoardMetric label="Ученики" value={`${state.students.length}`} hint={`В ${state.groups.length} активных группах`} tone="lilac" /><BoardMetric label="Начислено за июль" value={formatMoney(earned)} hint="Демо-расчёт зарплаты" tone="yellow" /></section>
    <section className="mt-4 grid gap-3 lg:grid-cols-[1.2fr_.8fr]"><article className="rounded-3xl border border-white/80 bg-surface p-5 shadow-card"><div className="flex items-center justify-between"><div><p className="text-[11px] font-semibold text-muted">РАСПИСАНИЕ</p><h2 className="mt-2 text-xl font-semibold">Сегодня</h2></div><button onClick={onLessons} className="rounded-full border border-brand/20 px-4 py-2 text-xs font-semibold text-brand">Все занятия</button></div><div className="mt-5 grid gap-2">{todayLessons.map((lesson) => <LessonLine key={lesson.lessonId} state={state} lesson={lesson} />)}</div></article><article className="rounded-3xl border border-brand/20 bg-brand/10 p-5"><p className="text-[11px] font-semibold text-brand">ПЕРИОД</p><h2 className="mt-4 text-2xl font-semibold">Июль открыт</h2><p className="mt-2 text-sm leading-6 text-muted">Сдача станет доступна 25 июля. После сдачи редактирование занятий блокируется.</p><div className="mt-7 h-2 overflow-hidden rounded-full bg-white/70"><div className="h-full w-[44%] rounded-full bg-brand" /></div><p className="mt-2 text-xs text-muted">11 из 25 дней</p></article></section>
  </>;
}

function RecordLesson({ state, provider, onSaved }: { state: TeacherState; provider: DataProvider; onSaved: (state: TeacherState) => void }) {
  const [kind, setKind] = useState<TeacherLessonKind>("GROUP");
  const [date, setDate] = useState(DEMO_TODAY);
  const [durationMin, setDuration] = useState(60);
  const [groupId, setGroupId] = useState(state.groups[0]?.groupId ?? "");
  const [studentIds, setStudentIds] = useState<string[]>([]);
  const [message, setMessage] = useState<{ type: "error" | "success"; text: string } | null>(null);

  const maxStudents = kind === "SOLOIST" ? 1 : kind === "PAIR" ? 2 : 4;
  function chooseKind(value: TeacherLessonKind) { setKind(value); setStudentIds([]); setMessage(null); }
  function toggleStudent(studentId: string) { setStudentIds((current) => current.includes(studentId) ? current.filter((id) => id !== studentId) : current.length < maxStudents ? [...current, studentId] : current); }
  async function save() {
    const input: RecordLessonInput = { kind, date, durationMin, groupId: kind === "GROUP" ? groupId : undefined, studentIds: kind === "GROUP" ? [] : studentIds };
    try { const next = await provider.recordTeacherLesson(input); setMessage({ type: "success", text: "Занятие записано" }); onSaved(next); }
    catch (error) { setMessage({ type: "error", text: error instanceof Error ? error.message : "Не удалось записать занятие" }); }
  }

  return <section className="mt-8"><p className="text-[11px] font-semibold text-muted">НОВОЕ ЗАНЯТИЕ</p><h1 className="mt-2 text-3xl font-semibold sm:text-4xl">Записать занятие</h1><div className="mt-7 grid gap-5 lg:grid-cols-[1fr_340px] lg:items-start"><div className="space-y-5"><fieldset><legend className="text-sm font-semibold">Вид занятия</legend><div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">{(Object.keys(KIND_META) as TeacherLessonKind[]).map((value) => <button type="button" key={value} onClick={() => chooseKind(value)} aria-pressed={kind === value} className={`rounded-2xl border p-4 text-left ${kind === value ? "border-brand bg-brand text-white" : "border-black/10 bg-surface"}`}><strong className="block text-sm">{KIND_META[value].label}</strong><span className={`mt-1 block text-xs ${kind === value ? "text-white/70" : "text-muted"}`}>{KIND_META[value].description}</span></button>)}</div></fieldset>
      <div className="grid gap-4 sm:grid-cols-2"><label className="text-sm font-semibold">Дата<input type="date" max={DEMO_TODAY} value={date} onChange={(event) => setDate(event.target.value)} className="mt-2 min-h-12 w-full rounded-2xl border border-black/10 bg-surface px-4 font-normal" /></label><label className="text-sm font-semibold">Продолжительность<select value={durationMin} onChange={(event) => setDuration(Number(event.target.value))} className="mt-2 min-h-12 w-full rounded-2xl border border-black/10 bg-surface px-4 font-normal"><option value={45}>45 минут</option><option value={60}>60 минут</option><option value={90}>90 минут</option></select></label></div>
      {kind === "GROUP" ? <fieldset><legend className="text-sm font-semibold">Группа</legend><div className="mt-3 grid gap-3 sm:grid-cols-3">{state.groups.map((group) => <button type="button" key={group.groupId} onClick={() => setGroupId(group.groupId)} aria-pressed={groupId === group.groupId} className={`min-h-28 rounded-2xl border p-4 text-left ${groupId === group.groupId ? "border-brand ring-2 ring-brand" : "border-black/10"} ${toneClass(group.color)}`}><strong className="block">{group.name}</strong><span className="mt-2 block text-xs text-muted">{group.branchName} · {group.studentIds.length} уч.</span></button>)}</div></fieldset> : <fieldset><legend className="text-sm font-semibold">Ученики <span className="font-normal text-muted">· выбрано {studentIds.length} из {maxStudents}</span></legend><div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{state.students.map((student) => <button type="button" key={student.studentId} onClick={() => toggleStudent(student.studentId)} aria-pressed={studentIds.includes(student.studentId)} className={`flex min-h-14 items-center gap-3 rounded-2xl border px-3 text-left ${studentIds.includes(student.studentId) ? "border-brand bg-brand text-white" : "border-black/10 bg-surface"}`}><span className={`grid h-9 w-9 place-items-center rounded-full text-xs font-bold ${studentIds.includes(student.studentId) ? "bg-white/20" : "bg-brand/15 text-brand"}`}>{initials(student.name)}</span><span className="text-sm font-semibold">{student.name}</span></button>)}</div></fieldset>}</div>
      <aside className="rounded-3xl bg-ink p-5 text-white shadow-card lg:sticky lg:top-6"><p className="text-[11px] font-semibold text-white/50">ПРОВЕРКА</p><h2 className="mt-3 text-2xl font-semibold">{KIND_META[kind].label}</h2><dl className="mt-5 space-y-3 text-sm"><div className="flex justify-between gap-3"><dt className="text-white/50">Дата</dt><dd>{formatDate(date)}</dd></div><div className="flex justify-between gap-3"><dt className="text-white/50">Длительность</dt><dd>{durationMin} мин</dd></div><div className="flex justify-between gap-3"><dt className="text-white/50">Выбрано</dt><dd>{kind === "GROUP" ? state.groups.find((group) => group.groupId === groupId)?.name : `${studentIds.length} уч.`}</dd></div></dl>{message && <div role="alert" className={`mt-5 rounded-2xl p-3 text-sm ${message.type === "error" ? "bg-red-400/20 text-red-100" : "bg-emerald-400/20 text-emerald-100"}`}>{message.text}</div>}<button onClick={save} className="mt-6 min-h-12 w-full rounded-full bg-accent px-5 text-sm font-bold text-ink">Сохранить занятие</button><p className="mt-4 text-xs leading-5 text-white/45">Будущая дата и дубли соло будут отклонены до сохранения.</p></aside></div></section>;
}

function TeacherLessons({ state }: { state: TeacherState }) {
  const lessons = [...state.lessons].sort((a, b) => b.date.localeCompare(a.date));
  return <section className="mt-8"><p className="text-[11px] font-semibold text-muted">ИСТОРИЯ</p><h1 className="mt-2 text-3xl font-semibold sm:text-4xl">Мои занятия</h1><div className="mt-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{lessons.map((lesson, index) => <article key={lesson.lessonId} className={`flex min-h-44 flex-col justify-between rounded-3xl border p-5 shadow-card ${index % 3 === 0 ? "border-brand/20 bg-brand/10" : index % 3 === 1 ? "border-white/80 bg-surface" : "border-accent/40 bg-accent/20"}`}><div><div className="flex items-start justify-between gap-3"><p className="text-[11px] font-semibold text-muted">{formatDate(lesson.date)} · {lesson.durationMin} мин</p><span className="rounded-full bg-white/70 px-2 py-1 text-[10px] font-bold">{KIND_META[lesson.kind].label}</span></div><h2 className="mt-4 text-lg font-semibold">{lesson.title}</h2><p className="mt-2 text-xs text-muted">{lessonParticipants(state, lesson)}</p></div><div className="mt-5 flex items-end justify-between"><span className="text-xs text-muted">{lesson.lessonId}</span><strong>{formatMoney(lesson.earned)}</strong></div></article>)}</div></section>;
}

function TeacherGroups({ state }: { state: TeacherState }) {
  return <section className="mt-8"><p className="text-[11px] font-semibold text-muted">УЧЕНИКИ</p><h1 className="mt-2 text-3xl font-semibold sm:text-4xl">Мои группы</h1><div className="mt-7 grid gap-3 md:grid-cols-3">{state.groups.map((group) => <article key={group.groupId} className={`rounded-3xl border p-5 shadow-card ${toneClass(group.color)}`}><div className="flex items-start justify-between"><div><p className="text-[11px] text-muted">{group.branchName}</p><h2 className="mt-2 text-xl font-semibold">{group.name}</h2></div><span className="grid h-9 min-w-9 place-items-center rounded-full bg-white/70 px-2 text-sm font-bold">{group.studentIds.length}</span></div><div className="mt-6 space-y-2">{group.studentIds.map((studentId) => { const student = state.students.find((entry) => entry.studentId === studentId); return <div key={studentId} className="flex items-center gap-3 border-t border-black/10 pt-2"><span className="grid h-8 w-8 place-items-center rounded-full bg-white/70 text-[10px] font-bold">{initials(student?.name ?? "")}</span><span className="text-sm font-medium">{student?.name}</span></div>; })}</div></article>)}</div></section>;
}

function TeacherPeriod({ state, provider, onChange }: { state: TeacherState; provider: DataProvider; onChange: (state: TeacherState) => void }) {
  const [message, setMessage] = useState<string | null>(null);
  const july = state.lessons.filter((lesson) => lesson.date.startsWith("2026-07"));
  const total = july.reduce((sum, lesson) => sum + lesson.earned, 0);
  const submitted = state.submittedPeriods.includes("2026-07");
  async function submit() { try { onChange(await provider.submitTeacherPeriod("2026-07")); } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось сдать период"); } }
  return <section className="mt-8"><p className="text-[11px] font-semibold text-muted">ПЕРИОДЫ</p><h1 className="mt-2 text-3xl font-semibold sm:text-4xl">Сдача июля</h1><div className="mt-7 grid gap-4 lg:grid-cols-[1fr_360px]"><article className="rounded-3xl border border-white/80 bg-surface p-5 shadow-card"><div className="grid gap-3 sm:grid-cols-3"><BoardMetric label="Занятий" value={`${july.length}`} hint="Записано за июль" tone="pink" /><BoardMetric label="Начислено" value={formatMoney(total)} hint="Демо-зарплата" tone="lilac" /><BoardMetric label="Статус" value={submitted ? "Сдан" : "Открыт"} hint={submitted ? "Редактирование закрыто" : "Можно дополнять"} tone="yellow" /></div><h2 className="mt-7 text-xl font-semibold">Последние занятия периода</h2><div className="mt-3 grid gap-2">{july.slice(0, 4).map((lesson) => <LessonLine key={lesson.lessonId} state={state} lesson={lesson} />)}</div></article><aside className="rounded-3xl bg-ink p-6 text-white shadow-card"><p className="text-[11px] font-semibold text-white/45">УСЛОВИЯ СДАЧИ</p><h2 className="mt-4 text-2xl font-semibold">Доступно с 25 июля</h2><p className="mt-3 text-sm leading-6 text-white/55">Сегодня в демо 11 июля. Кнопка намеренно заблокирована бизнес-правилом.</p>{message && <div role="alert" className="mt-5 rounded-2xl bg-red-400/20 p-3 text-sm text-red-100">{message}</div>}<button onClick={submit} disabled={!submitted && Number(DEMO_TODAY.slice(8, 10)) < 25} className="mt-7 min-h-12 w-full rounded-full bg-accent px-5 text-sm font-bold text-ink disabled:cursor-not-allowed disabled:opacity-35">{submitted ? "Период сдан" : "Сдать период"}</button><div className="mt-6 border-t border-white/10 pt-5"><p className="text-sm font-semibold">Июнь 2026</p><p className="mt-1 text-xs text-emerald-300">Сдан · период заблокирован</p></div></aside></div></section>;
}

function LessonLine({ state, lesson }: { state: TeacherState; lesson: TeacherState["lessons"][number] }) { return <div className="flex items-center justify-between gap-4 rounded-xl border border-black/5 bg-white/55 px-4 py-3"><div><p className="text-sm font-semibold">{lesson.title}</p><p className="mt-1 text-xs text-muted">{formatDate(lesson.date)} · {lessonParticipants(state, lesson)}</p></div><strong className="text-sm">{formatMoney(lesson.earned)}</strong></div>; }

function TeacherRail({ tab, onChange }: { tab: TeacherTab; onChange: (tab: TeacherTab) => void }) { const items: Array<{ id: TeacherTab; label: string; icon: string }> = [{ id: "home", label: "Главная", icon: "⌂" }, { id: "record", label: "Записать", icon: "+" }, { id: "lessons", label: "Занятия", icon: "◇" }, { id: "groups", label: "Группы", icon: "◫" }, { id: "period", label: "Период", icon: "✓" }]; return <nav aria-label="Навигация педагога" className="mt-6 hidden items-center gap-2 rounded-3xl border border-white/80 bg-surface p-2 shadow-card lg:flex">{items.map((item) => <button key={item.id} onClick={() => onChange(item.id)} aria-current={tab === item.id ? "page" : undefined} className={`min-h-11 rounded-2xl px-5 text-sm font-semibold transition ${tab === item.id ? "bg-brand text-white" : "text-muted hover:bg-brand/5 hover:text-brand"}`}><span aria-hidden="true" className="mr-2">{item.icon}</span>{item.label}</button>)}</nav>; }

function TeacherMobileNav({ tab, onChange }: { tab: TeacherTab; onChange: (tab: TeacherTab) => void }) { const items: Array<{ id: TeacherTab; label: string; icon: string }> = [{ id: "home", label: "Главная", icon: "⌂" }, { id: "record", label: "Записать", icon: "+" }, { id: "lessons", label: "Занятия", icon: "◇" }, { id: "groups", label: "Группы", icon: "◫" }, { id: "period", label: "Период", icon: "✓" }]; return <nav aria-label="Навигация педагога" className="fixed inset-x-2 bottom-2 z-20 mx-auto grid max-w-xl grid-cols-5 rounded-3xl border border-white/80 bg-surface/95 p-2 text-ink shadow-2xl backdrop-blur lg:hidden">{items.map((item) => <button key={item.id} onClick={() => onChange(item.id)} aria-current={tab === item.id ? "page" : undefined} className={`min-h-14 rounded-xl px-1 text-[10px] ${tab === item.id ? "bg-brand text-white" : "text-muted"}`}><span className="mb-1 block text-base">{item.icon}</span>{item.label}</button>)}</nav>; }

function BoardMetric({ label, value, hint, tone }: { label: string; value: string; hint: string; tone: "pink" | "lilac" | "yellow" }) { return <article className={`rounded-3xl border p-5 shadow-card ${toneClass(tone)}`}><p className="text-xs text-muted">{label}</p><p className="mt-5 text-2xl font-semibold">{value}</p><p className="mt-2 text-xs text-muted">{hint}</p></article>; }
function toneClass(tone: "pink" | "lilac" | "yellow") { return tone === "pink" ? "border-brand/20 bg-brand/10" : tone === "lilac" ? "border-white/80 bg-surface" : "border-accent/40 bg-accent/20"; }
function initials(name: string) { return name.split(" ").map((part) => part[0]).join("").slice(0, 2); }
function lessonParticipants(state: TeacherState, lesson: TeacherState["lessons"][number]) { if (lesson.groupId) return state.groups.find((group) => group.groupId === lesson.groupId)?.name ?? "Группа"; return lesson.studentIds.map((id) => state.students.find((student) => student.studentId === id)?.name.split(" ")[0]).filter(Boolean).join(", "); }
function formatDate(date: string) {
  if (!date) return "Дата не выбрана";
  const value = new Date(`${date}T12:00:00`);
  if (Number.isNaN(value.getTime())) return "Некорректная дата";
  return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long" }).format(value);
}
function formatMoney(amount: number) { return `${new Intl.NumberFormat("ru-RU").format(amount)} ₽`; }
