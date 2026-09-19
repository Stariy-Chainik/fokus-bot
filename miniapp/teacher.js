/* Фокус · Mini App — кабинет педагога (экраны t.*).
   Грузится после app.js и пользуется его каркасом: api(), SCREENS, ACT, cell/list/kpi.
   Данные — /api/teacher/*: педагог видит только свои занятия, группы и учеников,
   замок сданного периода для него действует. */
'use strict';

/* ── Сводка ──────────────────────────────────────────────────────────── */
SCREENS['t.home'] = async () => {
  const h = await api('/home');
  const lock = h.periodSubmitted
    ? pill('период сдан', 'ok')
    : h.canSubmit ? pill('можно сдавать', 'warn') : pill(`сдать с 25 ${MON_SHORT[+h.period.slice(5) - 1]}`, 'mute');
  return { title: 'Мой день', html: `
    <div class="h2">${esc(h.name)}</div>
    <div class="kpis">
      ${kpi(h.lessonsToday, 'занятий сегодня', '', 't.lessons', { key: h.today })}
      ${kpi(fmt(h.earnedToday), 'заработано сегодня', 'ok')}
    </div>
    <div class="kpis" style="margin-top:10px">
      ${kpi(h.lessonsMonth, `занятий за ${MON_NOM[+h.period.slice(5) - 1].toLowerCase()}`, '', 't.lessons', { key: h.period })}
      ${kpi(fmt(h.earnedMonth), 'зарплата за месяц', '', 't.money', { ym: h.period })}
    </div>
    <div style="margin-top:14px">${goBtn('✏️ Отметить занятие', 'a.record.w', { tid: state.me.teacherId, name: state.me.name })}</div>
    <div class="eyebrow">Разделы</div>
    ${list([
      cell({ lead: '📋', plain: true, t: 'Мои занятия', s: 'сегодня, вчера, месяц', go: 't.lessons', p: {} }),
      cell({ lead: '👥', plain: true, t: 'Мои группы', s: `${plural(h.groups, ['группа', 'группы', 'групп'])}: состав, пары, солисты`, go: 't.groups', p: {} }),
      cell({ lead: '💰', plain: true, t: 'Зарплата и сдача периода', s: `${MON_NOM[+h.period.slice(5) - 1]}: ${fmt(h.earnedMonth)}`, r: lock, go: 't.money', p: { ym: h.period } }),
    ])}
    ${!h.prevSubmitted ? `<div class="card pad" style="margin-top:12px;background:var(--warn-soft);border-color:var(--warn-soft)"><b>${MON_NOM[+h.prevPeriod.slice(5) - 1]} не сдан.</b> <span class="hint">Сдайте период, чтобы счёт родителям стал окончательным.</span><div style="margin-top:10px">${goBtn('Сдать период', 't.money', { ym: h.prevPeriod }, 'sec')}</div></div>` : ''}
    ${state.me.isAdmin ? `<div style="margin-top:14px">${btn('🛠 Режим администратора', 'switchRole', { to: 'admin' }, 'ghost')}</div>` : ''}` };
};

/* ── Занятия ─────────────────────────────────────────────────────────── */
const tKey = () => state.ui.tKey || new Date().toISOString().slice(0, 10);
const tLessonCell = x => cell({
  lead: x.type === 'group' ? '👥' : '👤', plain: true,
  t: esc(x.type === 'group' ? (x.groupName || 'Группа') : x.students.join(' + ') || '—'),
  s: `${fdate(x.date)} · ${x.durationMin} мин${x.type === 'group' && x.students.length ? ` · ${plural(x.students.length, ['ученик', 'ученика', 'учеников'])}` : ''}${x.locked ? ' · 🔒' : ''}`,
  r: `<b>${fmt(x.earned)}</b>`, go: 't.lesson', p: { id: x.id },
});
SCREENS['t.lessons'] = async ({ key }) => {
  const k = key || tKey();
  state.ui.tKey = k;
  const today = new Date(); const yest = new Date(); yest.setDate(today.getDate() - 1);
  const [d0, d1] = [today.toISOString().slice(0, 10), yest.toISOString().slice(0, 10)];
  const ym = k.slice(0, 7);
  const d = await api(`/lessons?${k.length === 10 ? 'date' : 'ym'}=${k}`);
  const chips = [[d0, 'Сегодня'], [d1, 'Вчера'], [ym, MON_NOM[+ym.slice(5) - 1]]];
  return { title: 'Мои занятия', html: `
    <div class="chips">${chips.map(([v, n]) => `<button class="chip" aria-pressed="${k === v}" data-go="t.lessons" data-p='${esc(JSON.stringify({ key: v }))}' data-replace="1">${n}</button>`).join('')}</div>
    ${d.lessons.length ? `${list(d.lessons.map(tLessonCell))}<div class="card" style="margin-top:10px"><div class="total"><span>${plural(d.lessons.length, ['занятие', 'занятия', 'занятий'])}</span><span class="big">${fmt(d.earned)}</span></div></div>`
      : '<div class="empty">Занятий нет</div>'}
    <div style="margin-top:12px">${goBtn('✏️ Отметить занятие', 'a.record.w', { tid: state.me.teacherId, name: state.me.name })}</div>` };
};

SCREENS['t.lesson'] = async ({ id }) => {
  const l = await api(`/lessons/${id}`);
  return { title: 'Занятие', html: `
    <div class="card pad"><div style="font-weight:800;font-size:16px">${esc(l.type === 'group' ? l.groupName || 'Группа' : l.attendees.map(a => a.name).join(' + '))}</div>
      <div class="hint">${fdate(l.date)} · ${l.durationMin} мин${l.recordedAt ? ` · отмечено ${l.recordedAt.slice(11, 16)}` : ''}</div>
      ${l.locked ? `<div style="margin-top:8px">${pill('🔒 период сдан', 'mute')}</div>` : ''}</div>
    ${l.attendees.length ? `<div class="eyebrow">${l.type === 'group' ? 'Посетили' : 'Ученики'}</div>${list(l.attendees.map(a => cell({
      lead: initials(a.name), t: esc(a.name),
      r: a.amount === null ? '' : a.amount ? `<b>${fmt(a.amount)}</b>` : esc(l.freeLabel || 'абонемент'),
      go: 't.student', p: { id: a.studentId },
    })))}` : '<div class="empty">Посещаемость не отмечалась</div>'}
    <div class="card" style="margin-top:10px"><div class="total"><span>Мне начислено</span><span class="big">${fmt(l.earned)}</span></div></div>
    <div style="margin-top:12px">${l.locked
      ? `<div class="card pad hint">Период сдан — занятие меняет только администратор.</div>`
      : btn('🗑 Удалить занятие', 'tDelLesson', { id }, 'danger')}</div>
    <p class="hint" style="margin-top:10px">Правка полей не поддерживается — как в боте: удалить и отметить заново.</p>` };
};

/* ── Группы, ученики ─────────────────────────────────────────────────── */
SCREENS['t.groups'] = async () => {
  const d = await api('/groups');
  return { title: 'Мои группы', html: d.groups.length ? list(d.groups.map(g => cell({
    lead: '👥', plain: true, t: esc(g.name), s: `${esc(g.branchName)} · ${MODE[g.mode] || g.mode}`,
    r: plural(g.students, ['ученик', 'ученика', 'учеников']), go: 't.group', p: { id: g.id },
  }))) : '<div class="empty">Групп нет</div>' };
};

SCREENS['t.group'] = async ({ id }) => {
  const g = await api(`/groups/${id}`);
  const tab = state.ui.tGroupTab || 'all';
  const rows = tab === 'pairs'
    ? (g.pairs.length ? list(g.pairs.map(p => cell({ lead: '💃', plain: true, t: `${esc(p.aName)} ↔ ${esc(p.bName)}`, go: 't.student', p: { id: p.aId } }))) : '<div class="empty">Пар нет</div>')
    : tab === 'solo'
      ? (g.soloists.length ? list(g.soloists.map(s => cell({ lead: initials(s.name), t: esc(s.name), go: 't.student', p: { id: s.id } }))) : '<div class="empty">Солистов нет</div>')
      : (g.students.length ? list(g.students.map(s => cell({ lead: initials(s.name), t: esc(s.name), s: s.partnerId ? 'в паре' : 'солист', go: 't.student', p: { id: s.id } }))) : '<div class="empty">В группе никого нет</div>');
  return { title: g.name, html: `
    <div class="card pad"><div style="font-weight:800;font-size:16px">${esc(g.name)}</div><div class="hint">${MODE[g.mode] || g.mode}${g.mode === 'per_visit' ? ` · ${fmt(g.priceFull)} за посещение` : g.mode === 'subscription' ? ` · ${fmt(g.priceFull)} в месяц` : ''}</div></div>
    ${chipsAct('tGroupTab', tab, [['all', `Состав (${g.students.length})`], ['pairs', `Пары (${g.pairs.length})`], ['solo', `Солисты (${g.soloists.length})`]])}
    ${rows}` };
};

SCREENS['t.student'] = async ({ id, ym }) => {
  const s = await api(`/students/${id}${ym ? `?ym=${ym}` : ''}`);
  return { title: s.name, html: `
    <div class="card pad"><div style="font-weight:800;font-size:16px">${esc(s.name)}</div>
      <div class="hint">${s.groups.length ? esc(s.groups.join(', ')) : 'без группы'}${s.partner ? ` · пара: ${esc(s.partner.name)}` : ''}</div></div>
    <div class="eyebrow">Мои занятия с учеником</div>
    ${monthChips('t.student', s.period, { id })}
    ${s.lessons.length ? list(s.lessons.map(tLessonCell)) : '<div class="empty">В этом месяце занятий не было</div>'}` };
};

/* ── Зарплата и сдача периода ────────────────────────────────────────── */
const T_LINE_ICON = { shift: '🕒', override: '✍️', in_shift: '↳', lesson: '📘' };
SCREENS['t.money'] = async ({ ym }) => {
  const period = ym || lastPeriods(1)[0];
  const [st, sub] = [await api(`/stats?ym=${period}`), await api(`/submit?ym=${period}`)];
  const state_ = sub.submitted ? pill('период сдан', 'ok') : sub.canSubmit ? pill('можно сдать', 'warn') : pill('сдаётся с 25-го', 'mute');
  return { title: 'Зарплата', html: `
    ${monthChips('t.money', period, {})}
    <div class="card pad"><div style="font-size:24px;font-weight:800;letter-spacing:-.02em">${fmt(st.total)}</div>
      <div class="hint">${MON_NOM[+period.slice(5) - 1]} · ${plural(st.groupLessons + st.individualLessons, ['занятие', 'занятия', 'занятий'])}${st.groupLessons ? ` · 👥 ${st.groupLessons}` : ''}${st.individualLessons ? ` · 👤 ${st.individualLessons}` : ''}</div>
      <div style="margin-top:8px">${state_}</div></div>
    <div style="margin-top:12px">${sub.submitted
      ? '<div class="card pad hint">Период сдан: занятия этого месяца больше не редактируются. Открыть его может администратор.</div>'
      : btn(sub.canSubmit ? `📤 Сдать ${MON_NOM[+period.slice(5) - 1].toLowerCase()} (${plural(sub.lessons, ['занятие', 'занятия', 'занятий'])}, ${fmt(sub.total)})` : `Сдать период можно с 25 ${MON_SHORT[+period.slice(5) - 1]}`,
        'tSubmitAsk', { ym: period, lessons: sub.lessons, total: sub.total }, sub.canSubmit ? '' : 'ghost')}</div>
    <div class="eyebrow">Начисления</div>
    ${st.lines.length ? list(st.lines.map(x => cell({
      lead: T_LINE_ICON[x.kind] || '📘', plain: true, t: `${fdate(x.date)}${x.label ? ` · ${esc(x.label)}` : ''}`,
      s: x.kind === 'in_shift' ? 'в смене — отдельно не оплачивается' : x.minutes ? `${x.minutes} мин` : '',
      r: `<b>${fmt(x.amount)}</b>`, ...(x.lessonId ? { go: 't.lesson', p: { id: x.lessonId } } : {}),
    }))) : '<div class="empty">Начислений нет</div>'}` };
};

/* ── действия ────────────────────────────────────────────────────────── */
ACT.tGroupTab = ({ v }) => { state.ui.tGroupTab = v; render(); };
ACT.tDelLesson = ({ id }) => sheet(`<h3>Удалить занятие?</h3><div class="hint">Занятие и начисления по нему пропадут. Отменить нельзя.</div>
  <div style="margin-top:12px">${btn('🗑 Удалить', 'tDelLessonDo', { id }, 'danger')}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
ACT.tDelLessonDo = async ({ id }) => {
  try { await api(`/lessons/${id}`, { method: 'DELETE' }); closeSheet(); back(); toast('Занятие удалено'); }
  catch (e) { closeSheet(); toast(e instanceof ApiError && e.code === 'period_locked' ? 'Период сдан — удаляет администратор' : errText(e)); }
};
ACT.tSubmitAsk = ({ ym, lessons, total }) => sheet(`<h3>Сдать ${MON_NOM[+ym.slice(5) - 1].toLowerCase()}?</h3>
  <div class="hint">${plural(lessons, ['занятие', 'занятия', 'занятий'])} на ${fmt(total)}. После сдачи занятия месяца больше не изменить — открыть период может только администратор.</div>
  <div style="margin-top:12px">${btn('📤 Сдать период', 'tSubmitDo', { ym })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
ACT.tSubmitDo = async ({ ym }) => {
  try { const r = await api('/submit', { method: 'POST', body: { ym } }); closeSheet(); render(); toast(`Период сдан: ${plural(r.lessons, ['занятие', 'занятия', 'занятий'])}, ${fmt(r.total)}`); }
  catch (e) {
    closeSheet();
    toast(e instanceof ApiError && e.code === 'too_early' ? 'Сдать период можно с 25-го числа'
      : e instanceof ApiError && e.code === 'already' ? 'Период уже сдан' : errText(e));
  }
};
