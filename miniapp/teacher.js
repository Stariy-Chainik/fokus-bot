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
    ${state.me.canBill ? `<div class="eyebrow">Счета</div>${list([cell({ lead: '🧾', plain: true, t: 'Счета моих групп', s: 'выставить и отправить родителям', go: 't.bills', p: {} })])}` : ''}
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

/* ── Дневники спортсменов ────────────────────────────────────────────── */
SCREENS['t.diary'] = async () => {
  const d = await api('/diary');
  return { title: 'Дневники', html: `
    <div class="chips"><button class="chip" data-go="t.rating" data-p='${esc(JSON.stringify({ ym: d.period }))}'>🏆 Рейтинг</button></div>
    ${d.athletes.length ? list(d.athletes.map(a => cell({
      lead: initials(a.name), t: esc(a.name), s: a.unrated ? `🆕 ${plural(a.unrated, ['запись', 'записи', 'записей'])} без оценки` : 'всё оценено',
      r: a.unrated ? pill(String(a.unrated), 'warn') : '', go: 't.diary.s', p: { id: a.id },
    }))) : '<div class="empty">У ваших учеников нет кабинета спортсмена</div>'}
    <p class="hint" style="margin-top:8px">Спортсмен сам записывает тренировки в боте, вы ставите оценку и выдаёте задания.</p>` };
};

const tEntryCell = (e, sid) => cell({
  lead: e.grade ? '⭐' : '🆕', plain: true,
  t: `${fdate(e.date)} · ${e.minutes} мин`,
  s: `${esc(e.topics.join(', ') || 'без темы')}${e.comment ? ` · ${esc(e.comment)}` : ''}${e.tasks.length ? ` · задания: ${esc(e.tasks.join(', '))}` : ''}${e.gradeComment ? `<br>📝 ${esc(e.gradeComment)}` : ''}`,
  r: e.grade ? `<b>${e.grade}/5</b>` : pill('оценить', 'warn'),
  act: 'tGradeAsk', p: { id: e.id, sid, name: `${fdate(e.date)} · ${e.minutes} мин`, grade: e.grade || 0 },
});

SCREENS['t.diary.s'] = async ({ id, ym }) => {
  const d = await api(`/diary/${id}${ym ? `?ym=${ym}` : ''}`);
  const st = d.stats;
  const topics = Object.entries(st.byTopic || {}).sort((a, b) => b[1] - a[1]);
  return { title: d.student.name, html: `
    ${monthChips('t.diary.s', d.period, { id })}
    <div class="kpis">${kpi(st.sessions, 'тренировок')}${kpi(st.minutes + ' мин', 'всего')}</div>
    <div class="kpis" style="margin-top:10px">${kpi(st.avgGrade ? st.avgGrade.toFixed(1) : '—', 'средняя оценка', 'ok')}${kpi(st.points, `очки${d.place ? ` · ${d.place <= 3 ? `${d.placeIcon} ` : ''}${d.place} место` : ''}`)}</div>
    ${topics.length ? `<div class="eyebrow">По танцам</div>${list(topics.map(([t, m]) => cell({ t: esc(t), r: `${Math.round(m)} мин` })))}` : ''}
    <div class="eyebrow">Записи</div>
    ${d.entries.length ? list(d.entries.map(e => tEntryCell(e, id))) : '<div class="empty">В этом месяце записей нет</div>'}
    <div class="eyebrow">Задания</div>
    ${d.openTasks.length ? list(d.openTasks.map(t => cell({ lead: '📋', plain: true, t: esc(t.exercise), s: `${t.minutes} мин${t.comment ? ` · ${esc(t.comment)}` : ''}` }))) : '<div class="empty">Открытых заданий нет</div>'}
    <div style="margin-top:12px">${btn('➕ Выдать задание', 'tTaskForm', { sid: id })}${goBtn('📋 Все задания', 't.diary.tasks', { sid: id }, 'sec')}</div>` };
};

SCREENS['t.diary.tasks'] = async ({ sid }) => {
  const d = await api(`/diary/${sid}/tasks`);
  return { title: 'Задания', html: `
    <div class="h2">${esc(d.student.name)}</div>
    ${d.tasks.length ? list(d.tasks.map(t => cell({
      lead: t.status === 'open' ? '📋' : '✅', plain: true, t: esc(t.exercise),
      s: `${t.minutes} мин${t.comment ? ` · ${esc(t.comment)}` : ''}${t.doneTimes ? ` · сделано ${t.doneTimes} раз${t.lastDone ? `, последний ${fdate(t.lastDone)}` : ''}` : ''}`,
      r: t.status === 'open' ? `<button class="chip" data-act="tTaskClose" data-p='${esc(JSON.stringify({ id: t.id, sid }))}'>Закрыть</button>` : pill('закрыто', 'mute'),
    }))) : '<div class="empty">Заданий нет</div>'}
    <div style="margin-top:12px">${btn('➕ Выдать задание', 'tTaskForm', { sid })}</div>` };
};

SCREENS['t.rating'] = async ({ ym }) => {
  const period = ym || lastPeriods(1)[0];
  const d = await api(`/diary/rating?ym=${period}`);
  return { title: 'Рейтинг', html: `
    ${monthChips('t.rating', period, {})}
    ${d.rows.length ? list(d.rows.map(r => cell({
      lead: r.icon || String(r.place), plain: true, t: esc(r.name) + (r.mine ? ' <span class="hint">· мой ученик</span>' : ''),
      s: `${plural(r.sessions, ['тренировка', 'тренировки', 'тренировок'])} · ${r.minutes} мин${r.avgGrade ? ` · средняя ${r.avgGrade.toFixed(1)}` : ''}`,
      r: `<b>${r.points}</b>`,
    }))) : '<div class="empty">В этом месяце записей нет</div>'}
    <p class="hint" style="margin-top:8px">Очки = минуты × оценка (без оценки коэффициент 3).</p>` };
};

/* ── Счета своих групп (BILLING_TEACHER_IDS) ─────────────────────────── */
SCREENS['t.bills'] = async ({ ym }) => {
  const period = ym || lastPeriods(1)[0];
  const d = await api(`/bills?ym=${period}`);
  return { title: 'Счета групп', html: `
    ${monthChips('t.bills', period, {})}
    ${d.groups.length ? list(d.groups.map(g => cell({
      lead: '👥', plain: true, t: esc(g.name), s: MODE[g.mode] || g.mode,
      r: plural(g.students, ['ученик', 'ученика', 'учеников']), go: 't.bills.g', p: { gid: g.id, ym: period },
    }))) : '<div class="empty">Групп нет</div>'}` };
};

SCREENS['t.bills.g'] = async ({ gid, ym }) => {
  const d = await api(`/bills/group/${gid}?ym=${ym}`);
  const toSend = d.students.filter(s => s.total > 0).length;
  return { title: d.group.name, html: `
    <div class="card pad"><div style="font-weight:800;font-size:16px">${esc(d.group.name)}</div><div class="hint">${fmon(ym)} · начислено ${fmt(d.students.reduce((a, s) => a + s.total, 0))}</div></div>
    ${d.students.length ? list(d.students.map(s => cell({
      lead: initials(s.name), t: esc(s.name),
      s: s.hasParent ? (s.rest ? `к оплате ${fmt(s.rest)}` : 'оплачено') : 'родитель не привязан',
      r: `<b>${fmt(s.total)}</b>`, go: 't.bill', p: { sid: s.id, ym },
    }))) : '<div class="empty">В группе никого нет</div>'}
    <div style="margin-top:12px">${btn(`📨 Отправить счета всей группе (${toSend})`, 'tBillGroupAsk', { gid, ym, count: toSend, name: d.group.name }, toSend ? '' : 'ghost')}</div>` };
};

SCREENS['t.bill'] = async ({ sid, ym }) => {
  const b = await api(`/bills/student/${sid}?ym=${ym}`);
  return { title: b.student.name, html: `
    <div class="card pad"><div style="font-weight:800;font-size:16px">${esc(b.student.name)}</div>
      <div class="hint">${fmon(b.period)}${b.groups.length ? ` · ${esc(b.groups.join(', '))}` : ''}</div></div>
    ${b.rows.length ? `<div class="card bill" style="margin-top:10px">${b.rows.map(r => `
      <div class="grp"><span>${esc(r.name)}</span><span>${fmt(r.total)}${r.paid ? ` · оплачено ${fmt(r.paid)}` : ''}</span></div>
      ${r.items.map(i => `<div class="lesson-line"><span>${i.paid ? '✅' : '⬜'}</span><span>${fdate(i.date)} · ${i.durationMin} мин</span><span class="amt">${fmt(i.amount)}</span></div>`).join('')}`).join('')}
      <div class="total"><span>К оплате</span><span class="big">${fmt(b.rest)}</span></div></div>` : '<div class="empty">Начислений за месяц нет</div>'}
    ${b.rows.some(r => r.rest > 0) ? `<div class="eyebrow">Отметить оплату</div>${list(b.rows.filter(r => r.rest > 0).map(r => cell({
      lead: '💾', plain: true, t: esc(r.name), s: `остаток ${fmt(r.rest)}${r.paid ? ` · оплачено ${fmt(r.paid)}` : ''}`,
      ...(r.subscription
        ? { r: pill('отметить', 'acc'), act: 'tPayAsk', p: { sid, ym, key: r.key, name: r.name, rest: r.rest, student: b.student.name } }
        : { r: pill('занятия', 'acc'), go: 't.pay.select', p: { sid, ym, key: r.key } }),
    })))}` : ''}
    <div style="margin-top:12px">${b.student.hasParent
      ? btn('📨 Отправить родителю', 'tBillSend', { sid, ym, name: b.student.name }, b.total ? '' : 'ghost')
      : '<div class="card pad hint">Родитель не привязан к ученику — отправлять некому.</div>'}</div>` };
};

SCREENS['t.pay.select'] = async ({ sid, ym, key }) => {
  const d = await api(`/bills/student/${sid}/marks?ym=${ym}&key=${encodeURIComponent(key)}`);
  const ui = state.ui.tsel || (state.ui.tsel = {}); const k = key + sid + ym;
  if (ui.key !== k) { ui.key = k; ui.picked = new Set(); }
  const chosen = d.marks.filter(m => !m.paid && ui.picked.has(m.lessonId));
  const total = chosen.reduce((a, m) => a + m.amount, 0);
  return { title: d.ledger.name, html: `
    <div class="hint" style="margin-bottom:10px">${esc(d.student.name)} · ${fmon(ym)} · начислено ${fmt(d.ledger.accrued)}, оплачено ${fmt(d.ledger.paid)}. Отметьте занятия, за которые приняли деньги.</div>
    <div class="list">${d.marks.map(m => m.paid
      ? `<div class="lesson-line"><span class="mark paid">✓</span><span>${fdate(m.date)} · ${m.durationMin} мин<div class="d">оплачено</div></span><span class="amt">${fmt(m.amount)}</span></div>`
      : `<button class="lesson-line pick" data-act="tPick" data-p='${esc(JSON.stringify({ id: m.lessonId }))}'><span class="mark ${ui.picked.has(m.lessonId) ? 'on' : ''}">${ui.picked.has(m.lessonId) ? '✓' : ''}</span><span>${fdate(m.date)} · ${m.durationMin} мин</span><span class="amt">${fmt(m.amount)}</span></button>`).join('')}</div>
    <div style="margin-top:12px">${btn(total ? `✅ Отметить оплату ${fmt(total)}` : 'Выберите занятия', 'tPayAsk',
      { sid, ym, key, name: d.ledger.name, rest: total || d.ledger.remainder, student: d.student.name, picked: total > 0 }, total ? '' : 'sec')}</div>
    <p class="hint" style="margin-top:10px">Сумма закрывает самые ранние неоплаченные занятия этого начисления — как в боте.</p>` };
};

/* ── действия ────────────────────────────────────────────────────────── */
ACT.tPick = ({ id }) => { const s = state.ui.tsel.picked; s.has(id) ? s.delete(id) : s.add(id); render(); };
ACT.tGroupTab = ({ v }) => { state.ui.tGroupTab = v; render(); };
ACT.tGradeAsk = ({ id, sid, name, grade }) => sheet(`<h3>Оценить тренировку</h3><div class="hint">${esc(name)}${grade ? ` · сейчас ${grade}/5` : ''}</div>
  <div class="chips" style="margin-top:12px">${[1, 2, 3, 4, 5].map(g => `<button class="chip" data-act="tGradePick" data-p='${esc(JSON.stringify({ g }))}' id="grade-${g}" aria-pressed="${g === grade}">${g}</button>`).join('')}</div>
  ${field('gcomment', 'Комментарий (необязательно)', '', 'placeholder="Что получилось, что подтянуть"')}
  <div style="margin-top:12px">${btn('⭐ Сохранить оценку', 'tGradeDo', { id, sid })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
ACT.tGradePick = ({ g }) => { state.ui.tGrade = g; document.querySelectorAll('[id^="grade-"]').forEach(b => b.setAttribute('aria-pressed', b.id === `grade-${g}`)); };
ACT.tGradeDo = async ({ id }) => {
  const grade = state.ui.tGrade;
  if (!grade) { toast('Выберите оценку от 1 до 5'); return; }
  try { await api(`/diary/entries/${id}/grade`, { method: 'POST', body: { grade, comment: val('gcomment').trim() } }); state.ui.tGrade = 0; closeSheet(); render(); toast('Оценка сохранена, спортсмену отправлено'); }
  catch (e) { toast(errText(e)); }
};
ACT.tTaskForm = ({ sid }) => sheet(`<h3>➕ Задание</h3><div class="hint">Спортсмен увидит его при записи тренировки.</div>
  ${field('task-e', 'Упражнение', '', 'placeholder="Махи у станка, растяжка…"')}${field('task-m', 'Минут', '15', 'inputmode="numeric"')}${field('task-c', 'Комментарий (необязательно)', '')}
  <div style="margin-top:12px">${btn('💾 Выдать задание', 'tTaskAdd', { sid })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
ACT.tTaskAdd = async ({ sid }) => {
  const exercise = val('task-e').trim(); const minutes = +val('task-m');
  if (!exercise || !minutes) { toast('Нужно упражнение и минуты'); return; }
  try { await api(`/diary/${sid}/tasks`, { method: 'POST', body: { exercise, minutes, comment: val('task-c').trim() } }); closeSheet(); render(); toast('Задание отправлено спортсмену'); }
  catch (e) { toast(errText(e)); }
};
ACT.tTaskClose = async ({ id }) => { try { await api(`/diary/tasks/${id}/close`, { method: 'POST' }); render(); toast('Задание закрыто'); } catch (e) { toast(errText(e)); } };
ACT.tPayAsk = ({ sid, ym, key, name, rest, student, picked }) => {
  state.ui.tPayMethod = 'cash';
  sheet(`<h3>Отметить оплату</h3><div class="hint">${esc(student)} · ${esc(name)} · ${fmon(ym)}. ${picked ? 'За выбранные занятия' : 'Остаток'} ${fmt(rest)}.</div>
    ${field('pay-a', 'Сумма, ₽', String(rest), 'inputmode="numeric"')}
    <div class="hint" style="margin:10px 0 4px">Способ оплаты</div>
    <div class="chips">${[['cash', '💵 Наличные'], ['receipt_bank', '🏦 Перевод'], ['admin_manual', '👤 Вручную']].map(([v, n]) => `<button class="chip" id="pm-${v}" aria-pressed="${v === 'cash'}" data-act="tPayMethod" data-p='${esc(JSON.stringify({ v }))}'>${n}</button>`).join('')}</div>
    <div style="margin-top:12px">${btn('💾 Зачесть оплату', 'tPayDo', { sid, ym, key })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
};
ACT.tPayMethod = ({ v }) => { state.ui.tPayMethod = v; document.querySelectorAll('[id^="pm-"]').forEach(b => b.setAttribute('aria-pressed', b.id === `pm-${v}`)); };
ACT.tPayDo = async ({ sid, ym, key }) => {
  const amount = +val('pay-a');
  if (!amount) { toast('Укажите сумму'); return; }
  try {
    const r = await api(`/bills/student/${sid}/pay`, { method: 'POST', body: { ym, key, amount, method: state.ui.tPayMethod || 'cash' } });
    if (state.ui.tsel) state.ui.tsel.key = '';
    closeSheet(); render(); toast(r.credited ? `Зачтено ${fmt(r.credited)}` : 'Закрывать нечего — остатков нет');
  } catch (e) { closeSheet(); toast(errText(e)); }
};
ACT.tBillSend = ({ sid, ym, name }) => sheet(`<h3>Отправить счёт?</h3><div class="hint">${esc(name)} · ${fmon(ym)}. Родитель получит счёт в Telegram или MAX.</div>
  <div style="margin-top:12px">${btn('📨 Отправить', 'tBillSendDo', { sid, ym })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
ACT.tBillSendDo = async ({ sid, ym }) => {
  try { const r = await api(`/bills/student/${sid}/send?ym=${ym}`, { method: 'POST' }); closeSheet(); toast(r.sentTo ? `Счёт отправлен (${r.sentTo} из ${r.recipients})` : 'Не удалось доставить'); }
  catch (e) { closeSheet(); toast(errText(e)); }
};
ACT.tBillGroupAsk = ({ gid, ym, count, name }) => { if (!count) { toast('Начислений в группе нет'); return; } sheet(`<h3>Счета всей группе?</h3><div class="hint">${esc(name)} · ${fmon(ym)}: ${plural(count, ['ученик', 'ученика', 'учеников'])} с начислениями. Родители получат счёт сразу.</div>
  <div style="margin-top:12px">${btn('📨 Отправить всем', 'tBillGroupDo', { gid, ym })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`); };
ACT.tBillGroupDo = async ({ gid, ym }) => {
  try { const r = await api(`/bills/group/${gid}/send?ym=${ym}`, { method: 'POST' }); closeSheet(); toast(`Отправлено: ${r.sent}${r.noParent ? `, без родителя: ${r.noParent}` : ''}${r.failed ? `, не дошло: ${r.failed}` : ''}`); }
  catch (e) { closeSheet(); toast(errText(e)); }
};
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
