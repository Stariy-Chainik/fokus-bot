/* Фокус · Mini App — кабинет спортсмена (экраны s.*).
   Грузится после app.js и пользуется его каркасом: api(), SCREENS, ACT, cell/list/kpi.
   Данные — /api/athlete/*: свой дневник, задания педагога и рейтинг.
   Запись тренировки — одной формой (в боте это мастер из пяти шагов). */
'use strict';

const S_MIN = [30, 45, 60, 90, 120];
const sToday = () => new Date().toISOString().slice(0, 10);
/* Черновик записи живёт в state.ui, чтобы не терялся при перерисовке. */
const sDraft = () => (state.ui.sLog = state.ui.sLog || { date: sToday(), minutes: 60, topics: [], tasks: [], comment: '' });

const sEntryCell = e => cell({
  lead: e.grade ? '⭐' : '📝', plain: true, cls: 'wrap',
  t: `${fdate(e.date)} · ${e.minutes} мин`,
  s: `${esc(e.topics.join(', ') || 'без темы')}${e.comment ? ` · ${esc(e.comment)}` : ''}${e.tasks.length ? `<br>📋 ${esc(e.tasks.join(', '))}` : ''}${e.gradeComment ? `<br>📝 ${esc(e.gradeComment)}${e.gradedBy ? ` — ${esc(e.gradedBy)}` : ''}` : ''}`,
  r: e.grade ? `<b>${e.grade}/5</b>` : pill('без оценки', 'mute'),
});

/* ── Сводка ──────────────────────────────────────────────────────────── */
SCREENS['s.home'] = async () => {
  const h = await api('/home');
  const st = h.stats;
  return { title: 'Мой дневник', html: `
    ${hero(`${esc(h.student.name)} · ${fmon(h.period)}`)}
    <div class="kpis">
      ${kpi(st.sessions, 'тренировок', '', 's.entries', { ym: h.period })}
      ${kpi(st.minutes + ' мин', 'всего')}
    </div>
    <div class="kpis" style="margin-top:10px">
      ${kpi(st.avgGrade ? st.avgGrade.toFixed(1) : '—', 'средняя оценка', 'ok')}
      ${kpi(st.points, `очки${h.place ? ` · ${h.place <= 3 ? `${h.placeIcon} ` : ''}${h.place} место` : ''}`, '', 's.rating', { ym: h.period })}
    </div>
    <div style="margin-top:14px">${btn('➕ Записать тренировку', 'sLogForm', {})}</div>
    ${h.openTasks.length ? `<div class="eyebrow">Задания педагога</div>${list(h.openTasks.slice(0, 3).map(t => cell({
      lead: '📋', plain: true, cls: 'wrap', t: esc(t.exercise),
      s: `${t.minutes} мин${t.done ? ` · сделано ${t.done} раз${t.last ? `, последний ${fdate(t.last)}` : ''}` : ''}`,
      go: 's.tasks', p: {},
    })))}` : ''}
    <div class="eyebrow">Последние тренировки</div>
    ${h.recent.length ? list(h.recent.map(sEntryCell)) : empty('Записей за месяц нет', btn('➕ Записать тренировку', 'sLogForm', {}, 'sec'))}
    ${h.recent.length ? `<div style="margin-top:12px">${goBtn('📓 Все записи месяца', 's.entries', { ym: h.period }, 'ghost')}</div>` : ''}
    ${h.unrated ? `<p class="hint" style="margin-top:10px">${plural(h.unrated, ['запись ждёт', 'записи ждут', 'записей ждут'])} оценки педагога.</p>` : ''}` };
};

/* ── Запись тренировки: одна форма вместо мастера ─────────────────────── */
ACT.sLogForm = () => { state.ui.sLog = { date: sToday(), minutes: 60, topics: [], tasks: [], comment: '' }; go('s.log', {}); };
ACT.sLogSet = ({ k, v }) => { sDraft()[k] = v; render(); };
ACT.sLogTopic = ({ v }) => { const d = sDraft(); d.topics = d.topics.includes(v) ? d.topics.filter(x => x !== v) : [...d.topics, v]; render(); };
ACT.sLogTask = ({ v }) => { const d = sDraft(); d.tasks = d.tasks.includes(v) ? d.tasks.filter(x => x !== v) : [...d.tasks, v]; render(); };
ACT.sLogSave = async () => {
  const d = sDraft();
  const date = val('s-date') || d.date;
  const minutes = +(val('s-min') || d.minutes);
  if (!date || !minutes) { toast('Нужны дата и минуты'); return; }
  try {
    await api('/entries', { method: 'POST', body: { date, minutes, topics: d.topics, taskIds: d.tasks, comment: val('s-comment').trim() } });
    state.ui.sLog = null; back(); toast('Тренировка записана');
  } catch (e) { toast(errText(e)); }
};
ACT.sEntryDelete = ({ id }) => sheet(`<h3>Удалить запись?</h3><div class="hint">Тренировка исчезнет из статистики и рейтинга. Запись с оценкой педагога удалить нельзя.</div><div style="margin-top:12px">${btn('🗑 Удалить', 'sEntryDelDo', { id }, 'danger')}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
ACT.sEntryDelDo = async ({ id }) => {
  try { await api(`/entries/${id}`, { method: 'DELETE' }); closeSheet(); render(); toast('Запись удалена'); }
  catch (e) { closeSheet(); toast(errText(e)); }
};

SCREENS['s.log'] = async () => {
  const d = sDraft();
  const tasks = (await api('/tasks')).tasks;
  const topics = state.me.topics || [];
  const chip = (label, on, act, p) => `<button class="chip" aria-pressed="${on}" data-act="${act}" data-p='${esc(JSON.stringify(p))}'>${label}</button>`;
  return { title: 'Тренировка', html: `
    <div class="h2">Когда тренировались?</div>
    <div class="chips">${chip('Сегодня', d.date === sToday(), 'sLogSet', { k: 'date', v: sToday() })}${chip('Вчера', d.date === yesterdayOf(sToday()), 'sLogSet', { k: 'date', v: yesterdayOf(sToday()) })}</div>
    ${field('s-date', 'Другая дата', d.date, `type="date" max="${sToday()}"`)}
    <div class="eyebrow">Сколько минут</div>
    <div class="chips">${S_MIN.map(m => chip(m, +d.minutes === m, 'sLogSet', { k: 'minutes', v: m })).join('')}</div>
    ${field('s-min', 'Другое количество', d.minutes, 'type="number" min="1" max="600" inputmode="numeric"')}
    ${topics.length ? `<div class="eyebrow">Что отрабатывали</div>
      <div class="chips">${topics.map(t => chip(esc(t), d.topics.includes(t), 'sLogTopic', { v: t })).join('')}</div>` : ''}
    ${tasks.length ? `<div class="eyebrow">Задания педагога</div>
      ${list(tasks.map(t => cell({ lead: d.tasks.includes(t.id) ? '☑️' : '⬜', plain: true, cls: 'wrap',
        t: esc(t.exercise), s: `${t.minutes} мин${t.comment ? ` · ${esc(t.comment)}` : ''}`,
        act: 'sLogTask', p: { v: t.id } })))}` : ''}
    <div class="eyebrow">Комментарий</div>
    <input class="search" id="s-comment" placeholder="Что получилось, что нет" value="${esc(d.comment)}">
    <div style="margin-top:12px">${btn('💾 Сохранить', 'sLogSave', {})}</div>` };
};

/* ── Записи месяца ───────────────────────────────────────────────────── */
SCREENS['s.entries'] = async ({ ym }) => {
  const period = ym || lastPeriods(1)[0];
  const d = await api(`/entries?ym=${period}`);
  const topics = Object.entries(d.stats.byTopic || {}).sort((a, b) => b[1] - a[1]);
  return { title: 'Мои тренировки', html: `
    ${monthChips('s.entries', period, {})}
    <div class="kpis">${kpi(d.stats.sessions, 'тренировок')}${kpi(d.stats.minutes + ' мин', 'всего')}</div>
    ${topics.length ? `<div class="eyebrow">По танцам</div>${list(topics.map(([t, m]) => cell({ t: esc(t), r: `${Math.round(m)} мин` })))}` : ''}
    <div class="eyebrow">Записи</div>
    ${d.entries.length ? d.entries.map(e => `<div class="list" style="margin-bottom:8px">${sEntryCell(e)}${e.canDelete ? cell({ lead: '🗑', plain: true, t: 'Удалить запись', act: 'sEntryDelete', p: { id: e.id } }) : ''}</div>`).join('')
      : empty('Записей за месяц нет', btn('➕ Записать тренировку', 'sLogForm', {}, 'sec'))}` };
};

/* ── Задания педагога ────────────────────────────────────────────────── */
SCREENS['s.tasks'] = async () => {
  const d = await api('/tasks');
  return { title: 'Мои задания', html: `
    ${d.tasks.length ? list(d.tasks.map(t => cell({
      lead: '📋', plain: true, cls: 'wrap', t: esc(t.exercise),
      s: `${t.minutes} мин${t.comment ? ` · ${esc(t.comment)}` : ''}${t.done ? `<br>сделано ${t.done} раз${t.last ? `, последний ${fdate(t.last)}` : ''}` : '<br>ещё не отрабатывали'}`,
    }))) : empty('Открытых заданий нет', '<p class="hint" style="margin:0">Задания выдаёт педагог после тренировки</p>')}
    ${d.tasks.length ? `<div style="margin-top:12px">${btn('➕ Записать тренировку', 'sLogForm', {}, 'sec')}</div>
      <p class="hint" style="margin-top:8px">Отметьте задание в записи — педагог увидит, что вы его отработали. Закрывает задание педагог.</p>` : ''}` };
};

/* ── Рейтинг ─────────────────────────────────────────────────────────── */
SCREENS['s.rating'] = async ({ ym, topic }) => {
  const period = ym || lastPeriods(1)[0];
  const d = await api(`/rating?ym=${period}${topic ? `&topic=${encodeURIComponent(topic)}` : ''}`);
  const chip = (label, on, p) => `<button class="chip" aria-pressed="${on}" data-go="s.rating" data-p='${esc(JSON.stringify(p))}' data-replace="1">${label}</button>`;
  const row = r => cell({
    lead: r.placeIcon, plain: true,
    t: `${esc(r.name)}${r.me ? ' — вы' : ''}`, s: `${r.sessions} трен. · ${r.minutes} мин${r.avgGrade ? ` · ${r.avgGrade.toFixed(1)}` : ''}`,
    r: `<b>${r.points}</b>`, cls: r.me ? 'me wrap' : '',
  });
  return { title: 'Рейтинг', html: `
    ${stickyFilters(monthChips('s.rating', period, { topic: topic || '' }) +
      `<div class="chips scroll">${chip('Все темы', !topic, { ym: period })}${(state.me.allTopics || []).map(t => chip(esc(t), topic === t, { ym: period, topic: t })).join('')}</div>`)}
    ${d.top.length ? list(d.top.map(row)) : empty('Пока никто не тренировался')}
    ${d.me && d.me.place > 10 ? `<div class="eyebrow">Вы</div>${list([row(d.me)])}` : ''}
    <p class="hint" style="margin-top:10px">Очки = минуты × оценка. Запись без оценки считается с коэффициентом 3.</p>` };
};
