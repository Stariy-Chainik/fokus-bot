/* Фокус · Mini App — кабинет педагога (экраны t.*).
   Грузится после app.js и пользуется его каркасом: api(), SCREENS, ACT, cell/list/kpi.
   Данные — /api/teacher/*: педагог видит только свои занятия, группы и учеников,
   замок сданного периода для него действует. */
'use strict';

/* ── Сводка ──────────────────────────────────────────────────────────── */
/* Сводка — как у администратора, три блока по приоритету: что ждёт педагога → что было сегодня →
   как идёт месяц. «Разделов» нет: занятия, группы и зарплата — это вкладки внизу. */
SCREENS['t.home'] = async () => {
  const h = await api('/home');
  const mon = MON_NOM[+h.period.slice(5) - 1], prevMon = MON_NOM[+h.prevPeriod.slice(5) - 1];
  const attention = [
    h.inbox ? cell({ lead: '📥', plain: true, t: 'Ждут решения', s: 'наличные и чеки родителей', r: pill(h.inbox, 'warn'), go: 'a.inbox' }) : '',
    h.heldCash ? cell({ lead: '💵', plain: true, t: 'Наличные у вас', s: 'передайте администратору — он зачтёт оплату', r: `<b>${fmt(h.heldCash)}</b>` }) : '',
    h.unrated ? cell({ lead: '📓', plain: true, t: 'Оценить тренировки', s: `${plural(h.unrated, ['запись', 'записи', 'записей'])} спортсменов без оценки`, r: pill(h.unrated, 'warn'), go: 't.diary' }) : '',
    h.periodSubmit && !h.prevSubmitted ? cell({ lead: '📤', plain: true, t: `${prevMon} не сдан`, s: 'сдайте период, чтобы счёт родителям стал окончательным', go: 't.money', p: { ym: h.prevPeriod } }) : '',
  ].filter(Boolean);
  const today = h.lessonsToday ? `
    <div class="kpis">${kpi(plural(h.lessonsToday, ['занятие', 'занятия', 'занятий']), 'отмечено сегодня', '', 't.lessons', { key: h.today })}${kpi(fmt(h.earnedToday), h.directToday ? `от школы · ещё ${fmt(h.directToday)} напрямую` : 'заработано сегодня', 'ok', 't.lessons', { key: h.today })}</div>
    <div style="margin-top:10px">${list(h.todayLessons.map(x => tLessonCell(x)))}</div>` : '<div class="card pad hint">Занятий сегодня ещё не отмечено</div>';
  // Сдача периода отключена (periodSubmit=false): ни замка, ни напоминаний «не сдан».
  const lockNote = !h.periodSubmit ? '' : h.periodSubmitted ? ' · период сдан' : h.canSubmit ? ' · можно сдать период' : '';
  const monthLine = `${plural(h.lessonsMonth, ['занятие', 'занятия', 'занятий'])}${h.groupLessonsMonth ? ` · 👥 ${h.groupLessonsMonth}` : ''}${h.individualLessonsMonth ? ` · 👤 ${h.individualLessonsMonth}` : ''}${lockNote}`;
  // плитка месяца как у администратора: кто из учеников моих групп ещё не оплатил (педагоги со счетами)
  const tiles = h.bills ? `<div class="kpis" style="margin-bottom:6px">
      ${kpi(h.bills.rest ? fmt(h.bills.rest) : '✓', h.bills.rest ? `не оплатили за ${mon.toLowerCase()} · ${plural(h.bills.students, ['ученик', 'ученика', 'учеников'])}` : `за ${mon.toLowerCase()} всё оплачено`, h.bills.rest ? 'bad' : 'ok', 't.unpaid', { ym: h.period })}
      ${kpi(fmt(h.earnedMonth), `зарплата за ${mon.toLowerCase()} · ${plural(h.lessonsMonth, ['занятие', 'занятия', 'занятий'])}`, 'ok', 't.money', { ym: h.period })}
    </div>` : '';
  return { title: 'Сводка', html: `
    ${hero(`${esc(h.name)} · ${fdate(h.today)}`)}
    ${tiles}
    <div class="eyebrow">Требует внимания</div>
    ${attention.length ? list(attention) : '<div class="calm">✓ Оценок и решений не ждёт</div>'}
    <div class="eyebrow">Сегодня</div>
    ${today}
    <div style="margin-top:10px">${goBtn('✏️ Отметить занятие', 'a.record.w', { tid: state.me.teacherId, name: state.me.name })}</div>
    <div class="eyebrow">${mon}</div>
    ${list([
      cell({ lead: '💰', plain: true, t: 'Зарплата', s: monthLine, r: `<b>${fmt(h.earnedMonth)}</b>`, go: 't.money', p: { ym: h.period } }),
      ...(h.directMonth ? [cell({ lead: '🤝', plain: true, t: 'Напрямую от родителей', s: 'индивидуальные — платят вам лично', r: `<b class="direct">${fmt(h.directMonth)}</b>`, go: 't.money', p: { ym: h.period } })] : []),
    ])}
    ${state.me.isAdmin ? `<div style="margin-top:14px">${btn('🛠 Режим администратора', 'switchRole', { to: 'admin' }, 'ghost')}</div>` : ''}` };
};

/* Оплаты месяца по моим группам — как «не оплатили за …» у администратора: группы раскрываются до учеников. */
SCREENS['t.unpaid'] = async ({ ym }) => {
  ym = ym || lastPeriods(1)[0];
  const d = await api(`/unpaid?ym=${ym}`);
  const mon = MON_NOM[+ym.slice(5) - 1];
  const debt = d.groups.filter(g => g.rest), done = d.groups.filter(g => !g.rest);
  return { title: `Оплаты · ${mon}`, html: `
    ${monthChips('t.unpaid', ym, {})}
    <div class="kpis">${kpi(d.rest ? fmt(d.rest) : '✓', d.rest ? `не оплатили за ${mon.toLowerCase()} · ${plural(d.unpaidStudents, ['ученик', 'ученика', 'учеников'])}` : 'всё оплачено', d.rest ? 'bad' : 'ok')}${kpi(fmt(d.paid), `оплачено из ${fmt(d.accrued)}`, 'ok')}</div>
    ${debt.length ? `<div class="eyebrow">Группы с долгом</div>${debt.map(g => unpaidGroup(g, ym, 't.bill')).join('')}` : ''}
    ${done.length ? `<div class="eyebrow">Оплачено полностью</div>${done.map(g => unpaidGroup(g, ym, 't.bill')).join('')}` : ''}
    ${!d.groups.length ? '<div class="empty">За этот месяц начислений в ваших группах нет</div>' : ''}
    <p class="hint" style="margin-top:8px">✅ оплачено · 🟡 частично · ⬜ не оплачено. Тап по ученику — его счёт: там же отмечается оплата.</p>` };
};

/* ── Занятия ─────────────────────────────────────────────────────────── */
const tKey = () => state.ui.tKey || new Date().toISOString().slice(0, 10);
/* Отметка оплаты в строке журнала: один ученик — оплачено / нет, несколько — «оплатили N из M». */
const tPayNote = x => !x.payableCount ? '' : x.payableCount === 1
  ? (x.paidCount ? ' · ✅ оплачено' : ' · ⏳ не оплачено')
  : ` · ${x.paidCount === x.payableCount ? '✅' : '⏳'} оплатили ${x.paidCount} из ${x.payableCount}`;
/* Занятие с прямой оплатой: школа не начисляет, поэтому показываем сумму родителя. */
const tLessonCell = (x, withDate = false) => cell({
  lead: x.type === 'group' ? '👥' : '👤', plain: true,
  t: esc(x.type === 'group' ? (x.groupName || 'Группа') : x.students.join(' + ') || '—'),
  s: `${withDate ? `${fdate(x.date)} · ` : ''}${x.durationMin} мин${x.type === 'group' && x.students.length ? ` · ${plural(x.students.length, ['ученик', 'ученика', 'учеников'])}` : ''}${x.direct ? ' · платит родитель' : ''}${tPayNote(x)}${x.locked ? ' · 🔒' : ''}`,
  r: x.direct ? `<b class="direct">${fmt(x.directAmount)}</b>` : `<b>${fmt(x.earned)}</b>`,
  go: 't.lesson', p: { id: x.id },
});
/* Итог списка занятий: деньги школы и деньги родителей — разными строками. */
const tSums = items => ({
  school: items.reduce((a, l) => a + l.earned, 0),
  direct: items.reduce((a, l) => a + (l.directAmount || 0), 0),
});
const tSumLine = items => {
  const { school, direct } = tSums(items);
  if (!direct) return fmt(school);
  return school ? `${fmt(school)} от школы · ${fmt(direct)} напрямую` : `${fmt(direct)} напрямую`;
};
/* Журнал: занятия сгруппированы по дням, у каждого дня свой итог. */
SCREENS['t.lessons'] = async ({ key }) => {
  const k = key || tKey();
  state.ui.tKey = k;
  const today = new Date(); const yest = new Date(); yest.setDate(today.getDate() - 1);
  const [d0, d1] = [today.toISOString().slice(0, 10), yest.toISOString().slice(0, 10)];
  const ym = k.slice(0, 7);
  const d = await api(`/lessons?${k.length === 10 ? 'date' : 'ym'}=${k}`);
  const chips = [[d0, 'Сегодня'], [d1, 'Вчера'], [ym, MON_NOM[+ym.slice(5) - 1]]];
  const type = state.ui.tLesType || '';
  const n = t => d.lessons.filter(l => l.type === t).length;
  const shown = type ? d.lessons.filter(l => l.type === type) : d.lessons;
  const sums = tSums(shown);
  const byDay = {};
  shown.forEach(l => (byDay[l.date] = byDay[l.date] || []).push(l));
  const days = Object.keys(byDay).sort();
  const dayBlock = day => {
    const items = byDay[day];
    return `<div class="eyebrow">${fdate(day)}</div>${list(items.map(l => tLessonCell(l)))}
      <div class="hint" style="text-align:right;margin:6px 2px 0">${plural(items.length, ['занятие', 'занятия', 'занятий'])} · ${tSumLine(items)}</div>`;
  };
  return { title: 'Мои занятия', html: `
    ${stickyFilters(`<div class="chips">${chips.map(([v, nm]) => `<button class="chip" aria-pressed="${k === v}" data-go="t.lessons" data-p='${esc(JSON.stringify({ key: v }))}' data-replace="1">${nm}</button>`).join('')}</div>
      ${n('group') && n('individual') ? chipsAct('tLesType', type, [['', `Все · ${d.lessons.length}`], ['group', `Группы · ${n('group')}`], ['individual', `Индивидуальные · ${n('individual')}`]]) : ''}`)}
    ${shown.length ? `${days.map(dayBlock).join('')}
      <div class="card" style="margin-top:12px"><div class="total"><span>${plural(shown.length, ['занятие', 'занятия', 'занятий'])} · начислит школа</span><span class="big">${fmt(sums.school)}</span></div>
        ${sums.direct ? `<div class="total"><span>оплачивают родители напрямую</span><span class="big direct">${fmt(sums.direct)}</span></div>` : ''}</div>`
      : empty(type ? 'Таких занятий нет' : 'Занятий нет', '<p class="hint" style="margin:0">Отметьте занятие кнопкой ниже или выберите другой день</p>')}
    <div style="margin-top:12px">${goBtn('✏️ Отметить занятие', 'a.record.w', { tid: state.me.teacherId, name: state.me.name })}</div>` };
};

/* Статус оплаты ученика в карточке занятия: по накопительным оплатам месяца (как ✅/⬜ у родителя). */
const T_PAY = { paid: ['✅ оплачено', 'ok'], unpaid: ['⏳ не оплачено', 'warn'], sub_paid: ['✅ абонемент оплачен', 'ok'], sub_unpaid: ['⏳ абонемент не оплачен', 'warn'] };
SCREENS['t.lesson'] = async ({ id }) => {
  const l = await api(`/lessons/${id}`);
  const payPill = !l.payableCount ? '' : l.payableCount > 1
    ? pill(`оплатили ${l.paidCount} из ${l.payableCount}`, l.paidCount === l.payableCount ? 'ok' : 'warn')
    : pill(...T_PAY[l.attendees.find(a => a.payStatus).payStatus]);
  return { title: 'Занятие', html: `
    <div class="card pad"><div style="font-weight:800;font-size:16px">${esc(l.type === 'group' ? l.groupName || 'Группа' : l.attendees.map(a => a.name).join(' + '))}</div>
      <div class="hint">${fdate(l.date)} · ${l.durationMin} мин${l.recordedAt ? ` · отмечено ${l.recordedAt.slice(11, 16)}` : ''}</div>
      ${payPill || l.locked ? `<div style="margin-top:8px">${payPill}${l.locked ? ` ${pill('🔒 период сдан', 'mute')}` : ''}</div>` : ''}</div>
    ${l.attendees.length ? `<div class="eyebrow">${l.roster ? `Абонемент за ${MON_NOM[+l.date.slice(5, 7) - 1].toLowerCase()}` : l.type === 'group' ? 'Посетили' : 'Ученики'}</div>${list(l.attendees.map(a => cell({
      lead: initials(a.name), t: esc(a.name), s: l.direct ? 'платит напрямую' : (T_PAY[a.payStatus] ? T_PAY[a.payStatus][0] : ''),
      r: a.amount === null ? '' : a.amount ? `<b class="${l.direct ? 'direct' : ''}">${fmt(a.amount)}</b>` : esc(l.freeLabel || 'абонемент'),
      go: 't.student', p: { id: a.studentId },
    })))}` : '<div class="empty">Посещаемость не отмечалась</div>'}
    <div class="card" style="margin-top:10px">${l.direct
      ? `<div class="total"><span>Платят родители напрямую</span><span class="big direct">${fmt(l.directAmount)}</span></div>
         ${l.rent ? `<div class="total"><span>Аренда зала школе</span><span class="big">${fmt(l.rent)}</span></div>` : ''}
         <div class="pad hint" style="padding-top:0">Школа это занятие не начисляет и оплату по нему не отслеживает.</div>`
      : `<div class="total"><span>Мне начислено</span><span class="big">${fmt(l.earned)}</span></div>`}</div>
    <div style="margin-top:12px">${l.locked
      ? `<div class="card pad hint">Период сдан — занятие меняет только администратор.</div>`
      : btn('🗑 Удалить занятие', 'tDelLesson', { id }, 'danger')}</div>
    <p class="hint" style="margin-top:10px">Правка полей не поддерживается — как в боте: удалить и отметить заново.</p>` };
};

/* ── Группы, ученики ─────────────────────────────────────────────────── */
SCREENS['t.groups'] = async () => {
  const d = await api('/groups');
  // фильтр по филиалам — чипы показываем только когда филиалов больше одного
  const branches = [];
  d.groups.forEach(g => { if (!branches.some(b => b[0] === g.branchId)) branches.push([g.branchId, g.branchName]); });
  const cur = branches.some(b => b[0] === state.ui.tGroupBranch) ? state.ui.tGroupBranch : '';
  const shown = cur ? d.groups.filter(g => g.branchId === cur) : d.groups;
  const chips = branches.length > 1
    ? stickyFilters(chipsAct('tGroupBranch', cur, [['', `Все (${d.groups.length})`],
        ...branches.map(([id, name]) => [id, `${plainName(name)} (${d.groups.filter(g => g.branchId === id).length})`])]))
    : '';
  return { title: 'Мои группы', html: chips + (shown.length ? list(shown.map(g => cell({
    lead: '👥', plain: true, t: esc(g.name), s: `${esc(g.branchName)} · ${MODE[g.mode] || g.mode}`,
    r: plural(g.students, ['ученик', 'ученика', 'учеников']), go: 't.group', p: { id: g.id },
  }))) : empty('Групп нет', '<p class="hint" style="margin:0">Группы назначает администратор</p>')) };
};

SCREENS['t.group'] = async ({ id, ym }) => {
  const g = await api(`/groups/${id}`);
  const tab = state.ui.tGroupTab || 'all';
  if (tab === 'pay' && state.me.canBill) return tGroupPay(g, id, ym);
  const rows = tab === 'pairs'
    ? (g.pairs.length ? list(g.pairs.map(p => cell({ lead: '💃', plain: true, t: `${esc(p.aName)} ↔ ${esc(p.bName)}`, go: 't.student', p: { id: p.aId } }))) : '<div class="empty">Пар нет</div>')
    : tab === 'solo'
      ? (g.soloists.length ? list(g.soloists.map(s => cell({ lead: initials(s.name), t: esc(s.name), go: 't.student', p: { id: s.id } }))) : '<div class="empty">Солистов нет</div>')
      : (g.students.length ? list(g.students.map(s => cell({ lead: initials(s.name), t: esc(s.name), s: s.partnerId ? 'в паре' : 'солист', go: 't.student', p: { id: s.id } }))) : '<div class="empty">В группе никого нет</div>');
  return { title: g.name, html: `
    <div class="card pad"><div style="font-weight:800;font-size:16px">${esc(g.name)}</div><div class="hint">${MODE[g.mode] || g.mode}${g.mode === 'per_visit' ? ` · ${fmt(g.priceFull)} за посещение` : g.mode === 'subscription' ? ` · ${fmt(g.priceFull)} в месяц` : ''}</div></div>
    ${chipsAct('tGroupTab', tab, [['all', `Состав (${g.students.length})`], ['pairs', `Пары (${g.pairs.length})`], ['solo', `Солисты (${g.soloists.length})`],
      ...(state.me.canBill ? [['pay', '💳 Оплата']] : [])])}
    ${rows}` };
};

/* Оплата группы: счета учеников за месяц — раньше это был отдельный раздел «Счета групп». */
async function tGroupPay(g, id, ym) {
  const period = ym || lastPeriods(1)[0];
  const d = await api(`/bills/group/${id}?ym=${period}`);
  const toSend = d.students.filter(s => s.total > 0).length;
  const rest = d.students.reduce((a, s) => a + (s.rest || 0), 0);
  return { title: g.name, html: `
    <div class="card pad"><div style="font-weight:800;font-size:16px">${esc(g.name)}</div>
      <div class="hint">${fmon(period)} · начислено ${fmt(d.students.reduce((a, s) => a + s.total, 0))}${rest ? ` · к оплате ${fmt(rest)}` : ' · всё оплачено'}</div></div>
    ${chipsAct('tGroupTab', 'pay', [['all', `Состав (${g.students.length})`], ['pairs', `Пары (${g.pairs.length})`], ['solo', `Солисты (${g.soloists.length})`], ['pay', '💳 Оплата']])}
    ${monthChips('t.group', period, { id })}
    ${d.students.length ? list(d.students.map(s => cell({
      lead: initials(s.name), t: esc(s.name),
      s: s.hasParent ? (s.rest ? `к оплате ${fmt(s.rest)}` : 'оплачено') : 'родитель не привязан',
      r: `<b>${fmt(s.total)}</b>`, go: 't.bill', p: { sid: s.id, ym: period },
    }))) : '<div class="empty">В группе никого нет</div>'}
    <div style="margin-top:12px">${btn(`📨 Отправить счета всей группе (${toSend})`, 'tBillGroupAsk', { gid: id, ym: period, count: toSend, name: g.name }, toSend ? '' : 'ghost')}</div>` };
}

SCREENS['t.student'] = async ({ id, ym }) => {
  const s = await api(`/students/${id}${ym ? `?ym=${ym}` : ''}`);
  return { title: s.name, html: `
    <div class="card pad"><div style="font-weight:800;font-size:16px">${esc(s.name)}</div>
      <div class="hint">${s.groups.length ? esc(s.groups.join(', ')) : 'без группы'}${s.partner ? ` · пара: ${esc(s.partner.name)}` : ''}</div></div>
    ${(s.tariffs || []).length ? `<div class="eyebrow">Абонемент</div>${list(s.tariffs.map(g => `<div class="cell static"><span class="lead plain">💃</span><span><div class="t">${esc(g.name)}</div><div class="s">${fmt(g.freq.times === 2 ? g.freq.priceTwice : g.freq.priceThrice)} в месяц${g.freq.times === 2 && g.freq.since ? ` с ${monthLabel(g.freq.since).toLowerCase()}` : ''}</div>
      <div class="chips" style="margin:6px 0 0">${[2, 3].map(n => `<button class="chip" aria-pressed="${g.freq.times === n}" data-act="freqAsk" data-p='${esc(JSON.stringify({ sid: id, gid: g.id, times: n, name: g.name, price: n === 2 ? g.freq.priceTwice : g.freq.priceThrice, cur: g.freq.times }))}'>${n} раза в неделю</button>`).join('')}</div></span><span></span></div>`))}` : ''}
    <div style="margin-top:10px">${list([cell({ lead: '📅', plain: true, t: 'Все занятия ученика', s: state.me.canBill ? 'по месяцам: кто вёл, группа, сумма, оплата' : 'по месяцам: кто вёл, группа', go: 'a.student.lessons', p: { id, name: s.name } })])}</div>
    <div class="eyebrow">Мои занятия с учеником</div>
    ${monthChips('t.student', s.period, { id })}
    ${s.lessons.length ? list(s.lessons.map(l => tLessonCell(l, true))) : '<div class="empty">В этом месяце занятий не было</div>'}
    ${state.me.canBill ? `<div style="margin-top:12px">${goBtn(`🧾 Счёт за ${MON_NOM[+(s.period).slice(5) - 1].toLowerCase()}`, 't.bill', { sid: id, ym: s.period }, 'sec')}</div>` : ''}` };
};

/* ── Зарплата и сдача периода ────────────────────────────────────────── */
const T_LINE_ICON = { shift: '🕒', override: '✍️', in_shift: '↳', lesson: '📘' };
SCREENS['t.money'] = async ({ ym }) => {
  const period = ym || lastPeriods(1)[0];
  const canSubmitPeriod = !!(state.me && state.me.periodSubmit);
  const [st, sub] = [await api(`/stats?ym=${period}`), canSubmitPeriod ? await api(`/submit?ym=${period}`) : { submitted: st => false }];
  const state_ = !canSubmitPeriod ? '' : sub.submitted ? pill('период сдан', 'ok') : sub.canSubmit ? pill('можно сдать', 'warn') : pill('сдаётся с 25-го', 'mute');
  const d = st.direct && st.direct.lessons ? st.direct : null;   // блок прямой оплаты
  const paid = st.lines.filter(x => !x.direct);                  // строки, которые платит школа
  return { title: 'Зарплата', html: `
    ${monthChips('t.money', period, {})}
    <div class="card pad"><div style="font-size:24px;font-weight:800;letter-spacing:-.02em">${fmt(st.total)}</div>
      <div class="hint">${MON_NOM[+period.slice(5) - 1]} · ${plural(st.groupLessons + st.individualLessons, ['занятие', 'занятия', 'занятий'])}${st.groupLessons ? ` · 👥 ${st.groupLessons}` : ''}${st.individualLessons ? ` · 👤 ${st.individualLessons}` : ''}</div>
      ${state_ ? `<div style="margin-top:8px">${state_}</div>` : ''}</div>
    ${!canSubmitPeriod ? '' : `<div style="margin-top:12px">${sub.submitted
      ? '<div class="card pad hint">Период сдан: занятия этого месяца больше не редактируются. Открыть его может администратор.</div>'
      : btn(sub.canSubmit ? `📤 Сдать ${MON_NOM[+period.slice(5) - 1].toLowerCase()} (${plural(sub.lessons, ['занятие', 'занятия', 'занятий'])}, ${fmt(sub.total)})` : `Сдать период можно с 25 ${MON_SHORT[+period.slice(5) - 1]}`,
        'tSubmitAsk', { ym: period, lessons: sub.lessons, total: sub.total }, sub.canSubmit ? '' : 'ghost')}</div>`}
    <div class="eyebrow">Начисления</div>
    ${paid.length || d ? list([
      ...paid.map(x => cell({
        lead: T_LINE_ICON[x.kind] || '📘', plain: true, t: `${fdate(x.date)}${x.label ? ` · ${esc(x.label)}` : ''}`,
        s: x.kind === 'in_shift' ? 'в смене — отдельно не оплачивается' : x.minutes ? `${x.minutes} мин` : '',
        r: `<b>${fmt(x.amount)}</b>`, ...(x.lessonId ? { go: 't.lesson', p: { id: x.lessonId } } : {}),
      })),
      // Занятия прямой оплаты одной строкой: их 59 из 63, нулями список не засыпаем
      ...(d && d.lessons ? [cell({
        lead: '🤝', plain: true, t: 'Индивидуальные — прямая оплата',
        s: `${plural(d.lessons, ['занятие', 'занятия', 'занятий'])} · школа не начисляет`,
        r: '<b>0 ₽</b>', go: 't.lessons', p: { key: period },
      })] : []),
    ]) : empty('Начислений нет', '<p class="hint" style="margin:0">Отметьте занятия — они появятся здесь</p>')}
    ${d ? `<div class="eyebrow">Прямая оплата</div>
      <div class="card">
        <div class="total"><span>Родители платят вам за ${MON_NOM[+period.slice(5) - 1].toLowerCase()}</span><span class="big direct">${fmt(d.total)}</span></div>
        ${d.rent ? `<div class="total"><span>Аренда зала школе${d.rentPerLesson ? ` · ${fmt(d.rentPerLesson)} × ${d.lessons}` : ''}</span><span class="big">${fmt(d.rent)}</span></div>` : ''}
        <div class="pad hint" style="padding-top:0">Школа эти занятия не начисляет и оплату по ним не отслеживает — суммы справочные.</div>
      </div>
      ${d.students.length ? list(d.students.map(x => cell({
        lead: initials(x.name), t: esc(x.name), s: plural(x.lessons, ['занятие', 'занятия', 'занятий']),
        r: `<b class="direct">${fmt(x.amount)}</b>`, go: 't.student', p: { id: x.id, ym: period },
      }))) : ''}` : ''}` };
};

/* ── Дневники спортсменов ────────────────────────────────────────────── */
SCREENS['t.diary'] = async () => {
  const d = await api('/diary');
  return { title: 'Дневники', html: `
    <div class="chips"><button class="chip" data-go="t.rating" data-p='${esc(JSON.stringify({ ym: d.period }))}'>🏆 Рейтинг</button></div>
    ${d.athletes.length ? list(d.athletes.map(a => cell({
      lead: initials(a.name), t: esc(a.name), s: a.unrated ? `🆕 ${plural(a.unrated, ['запись', 'записи', 'записей'])} без оценки` : 'всё оценено',
      r: a.unrated ? pill(String(a.unrated), 'warn') : '', go: 't.diary.s', p: { id: a.id },
    }))) : empty('У ваших учеников нет кабинета спортсмена', '<p class="hint" style="margin:0">Спортсмен заводит его сам: /start → «Я спортсмен»</p>')}
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
    ${d.openTasks.length ? list(d.openTasks.map(t => cell({ lead: '📋', plain: true, cls: 'wrap', t: esc(t.exercise), s: `${t.minutes} мин${t.comment ? ` · ${esc(t.comment)}` : ''}` }))) : '<div class="empty">Открытых заданий нет</div>'}
    <div style="margin-top:12px">${btn('➕ Выдать задание', 'tTaskForm', { sid: id })}${goBtn('📋 Все задания', 't.diary.tasks', { sid: id }, 'sec')}</div>` };
};

SCREENS['t.diary.tasks'] = async ({ sid }) => {
  const d = await api(`/diary/${sid}/tasks`);
  return { title: 'Задания', html: `
    <div class="h2">${esc(d.student.name)}</div>
    ${d.tasks.length ? list(d.tasks.map(t => cell({
      lead: t.status === 'open' ? '📋' : '✅', plain: true, cls: 'wrap', t: esc(t.exercise),
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
/* Счёт ученика у педагога (только свои направления): неоплаченные уроки и абонемент — галочками,
   «Отметить оплату N ₽» → способ → зачёт по каждому начислению за выбранные уроки. */
SCREENS['t.bill'] = async ({ sid, ym }) => {
  const b = await api(`/bills/student/${sid}?ym=${ym}`);
  const sel = state.ui.tbill && state.ui.tbill.k === sid + ym ? state.ui.tbill : (state.ui.tbill = { k: sid + ym, rows: {}, sub: {} });
  sel.data = b.rows;
  const picked = tbillTotal();
  const mark = (on, paid) => `<span class="mark ${paid ? 'paid' : on ? 'on' : ''}">${paid || on ? '✓' : ''}</span>`;
  const rowsHtml = b.rows.map(r => {
    const head = `<div class="grp"><span>${esc(r.name)}${r.ownGroup ? ` <button class="chip" style="padding:1px 7px;margin-left:6px" data-act="tSubAsk" data-p='${esc(JSON.stringify({ sid, ym, gid: r.key.split(':')[1], total: r.total, paid: r.paid, student: b.student.name }))}' aria-label="Абонемент за этот месяц">✏️</button>` : ''}</span><span>${fmt(r.total)}${r.paid ? ` · оплачено ${fmt(r.paid)}` : ''}</span></div>`
      // оплаты по начислению; свою отметку педагог может снять («❌» — тот же лист, что у администратора)
      + (r.paidRows || []).map(p => `<div class="lesson-line">${mark(false, true)}<span class="hint">оплачено ${fdate(p.date)}${p.method ? ' · ' + (METHOD[p.method] || p.method) : ''}</span><span class="amt">${fmt(p.amount)}${p.mine ? ` <button class="chip" style="padding:2px 8px;margin-left:6px" data-act="cancelPayAsk" data-p='${esc(JSON.stringify({ pid: p.id, title: `${b.student.name} · ${r.name} · ${fmon(ym)}`, amount: p.amount }))}' aria-label="Убрать оплату">❌</button>` : ''}</span></div>`).join('');
    if (r.subscription) {
      return head + (r.rest
        ? `<button class="lesson-line pick" data-act="tbillSub" data-p='${esc(JSON.stringify({ key: r.key }))}'>${mark(!!sel.sub[r.key], false)}<span>абонемент за месяц · к оплате</span><span class="amt">${fmt(r.rest)}</span></button>`
        : `<div class="lesson-line">${mark(false, true)}<span class="hint">абонемент оплачен</span><span class="amt">${fmt(r.total)}</span></div>`);
    }
    return head + r.items.map(i => i.paid
      ? `<div class="lesson-line">${mark(false, true)}<span>${fdate(i.date)} · ${i.durationMin} мин</span><span class="amt">${fmt(i.amount)}</span></div>`
      : `<button class="lesson-line pick" data-act="tbillPick" data-p='${esc(JSON.stringify({ key: r.key, id: i.lessonId }))}'>${mark((sel.rows[r.key] || []).includes(i.lessonId), false)}<span>${fdate(i.date)} · ${i.durationMin} мин</span><span class="amt">${fmt(i.amount)}</span></button>`).join('');
  }).join('');
  return { title: b.student.name, html: `
    <div class="card pad"><div style="font-weight:800;font-size:16px">${esc(b.student.name)}</div>
      <div class="hint">${fmon(b.period)}${b.groups.length ? ` · ${esc(b.groups.join(', '))}` : ''}${b.rest ? ' · отметьте галочками, что оплачено' : ''}</div></div>
    ${b.rows.length ? `<div class="card bill" style="margin-top:10px">${rowsHtml}
      <div class="total"><span>К оплате</span><span class="big ${b.rest ? 'bad' : 'ok'}">${fmt(b.rest)}</span></div></div>` : '<div class="empty">Начислений за месяц нет</div>'}
    ${b.rest ? `<div style="margin-top:12px">${btn(picked ? `✅ Отметить оплату ${fmt(picked)}` : 'Отметьте галочками оплаченные уроки', 'tbillAsk', { sid, ym, name: b.student.name }, picked ? '' : 'sec')}</div>` : ''}
    <div style="margin-top:12px">${b.student.hasParent
      ? btn('📨 Отправить родителю', 'tBillSend', { sid, ym, name: b.student.name }, 'ghost')
      : '<div class="card pad hint">Родитель не привязан к ученику — отправлять некому.</div>'}</div>` };
};
/* «Абонемент за этот месяц»: ученик пришёл не с начала месяца — педагог ставит сумму за месяц, администратору уходит сообщение. */
ACT.tSubAsk = ({ sid, ym, gid, total, paid, student }) => sheet(`<h3>Абонемент за ${fmon(ym)}</h3><div class="hint">${esc(student)}. Сейчас начислено ${fmt(total)}${paid ? `, оплачено ${fmt(paid)}` : ''}. Новая сумма действует только на этот месяц — например, если ребёнок пришёл в середине месяца.</div>
  ${field('sub-a', 'Сумма абонемента за месяц, ₽', String(total), 'inputmode="numeric"')}${field('sub-r', 'Причина (необязательно)', '', 'placeholder="пришла с 15 числа, 2 занятия…"')}
  <div style="margin-top:12px">${btn('💾 Сохранить', 'tSubDo', { sid, ym, gid })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
ACT.tSubDo = async ({ sid, ym, gid }) => {
  const amount = +val('sub-a'); if (!(amount >= 0) || val('sub-a').trim() === '') { toast('Укажите сумму'); return; }
  try { const r = await api(`/bills/student/${sid}/subscription`, { method: 'PUT', body: { ym, groupId: gid, amount, reason: val('sub-r').trim() } }); closeSheet(); state.ui.tbill = null; render(); toast(`Абонемент за месяц: ${fmt(r.old)} → ${fmt(r.amount)}`); }
  catch (e) { closeSheet(); toast(e.data && e.data.message ? e.data.message : errText(e)); }
};
function tbillTotal() {
  const a = state.ui.tbill; if (!a || !a.data) return 0;
  let t = 0;
  a.data.forEach(r => {
    if (r.subscription) { if (a.sub[r.key]) t += r.rest; return; }
    const ids = a.rows[r.key] || []; r.items.forEach(i => { if (ids.includes(i.lessonId)) t += i.amount; });
  });
  return t;
}
ACT.tbillPick = ({ key, id }) => { const a = state.ui.tbill; const cur = a.rows[key] || []; a.rows[key] = cur.includes(id) ? cur.filter(x => x !== id) : [...cur, id]; render(); };
ACT.tbillSub = ({ key }) => { const a = state.ui.tbill; a.sub[key] = !a.sub[key]; render(); };
ACT.tbillAsk = ({ sid, ym, name }) => {
  const total = tbillTotal(); if (!total) { toast('Отметьте галочками оплаченные уроки'); return; }
  state.ui.tPayMethod = 'cash'; state.ui.tReceipt = null;
  sheet(`<h3>Отметить оплату?</h3><div class="hint">${esc(name)} · ${fmon(ym)}</div><div class="money" style="font-size:26px;font-weight:800;margin:10px 0">${fmt(total)}</div>
    <div class="hint" style="margin:10px 0 4px">Способ оплаты</div>
    ${tPayChips()}
    <div style="margin-top:12px">${btn('💵 Приняла наличные — передам администратору', 'tbillDo', { sid, ym })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
};
ACT.tbillDo = async ({ sid, ym }) => {
  if (state.ui.tPaying) return;
  if ((state.ui.tPayMethod || 'cash') !== 'cash' && !tReceiptChosen(sid)) { toast('Прикрепите чек перевода'); return; }
  state.ui.tPaying = true;
  document.querySelectorAll('.sheet .btn').forEach(b => { b.disabled = true; });
  const a = state.ui.tbill; let credited = 0;
  try {
    if ((state.ui.tPayMethod || 'cash') === 'cash') {
      const parts = a.data.map(r => {
        const ids = r.subscription ? [] : (a.rows[r.key] || []);
        const amount = r.subscription ? (a.sub[r.key] ? r.rest : 0) : r.items.filter(i => ids.includes(i.lessonId)).reduce((x, i) => x + i.amount, 0);
        return { key: r.key, amount, lessonIds: ids };
      }).filter(p => p.amount);
      const amount = await tCashDo(sid, ym, parts);
      closeSheet(); state.ui.tbill = null; render(); toast(`Передано администратору: ${fmt(amount)} — он зачтёт, когда получит деньги`);
      return;
    }
    const receiptId = await tReceiptId(sid, ym, tbillTotal());
    for (const r of a.data) {
      const ids = r.subscription ? [] : (a.rows[r.key] || []);
      const amount = r.subscription ? (a.sub[r.key] ? r.rest : 0) : r.items.filter(i => ids.includes(i.lessonId)).reduce((x, i) => x + i.amount, 0);
      if (!amount) continue;
      const res = await api(`/bills/student/${sid}/pay`, { method: 'POST', body: { ym, key: r.key, amount, method: 'receipt_bank', lessonIds: ids, receiptId } });
      credited += res.credited;
    }
    closeSheet(); state.ui.tbill = null; render(); toast(`Оплата ${fmt(credited)} отмечена`);
  } catch (e) {
    closeSheet(); render();
    // оплату этих уроков уже отметил кто-то другой (админ или педагог) — второй раз не зачитываем
    toast(e.status === 409 ? (credited ? `Отмечено ${fmt(credited)}; остальное уже оплачено` : 'Эти уроки уже оплачены — экран обновлён') : (e.data && e.data.message ? e.data.message : errText(e))); render(); toast(e.data && e.data.message ? e.data.message : errText(e)); }
  finally { state.ui.tPaying = false; }
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
ACT.tLesType = ({ v }) => { state.ui.tLesType = v; render(); };
ACT.tGroupBranch = ({ v }) => { state.ui.tGroupBranch = v; render(); };
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
  state.ui.tPayMethod = 'cash'; state.ui.tReceipt = null;
  sheet(`<h3>Отметить оплату</h3><div class="hint">${esc(student)} · ${esc(name)} · ${fmon(ym)}. ${picked ? 'За выбранные занятия' : 'Остаток'} ${fmt(rest)}.</div>
    ${field('pay-a', 'Сумма, ₽', String(rest), 'inputmode="numeric"')}
    <div class="hint" style="margin:10px 0 4px">Способ оплаты</div>
    ${tPayChips()}
    <div style="margin-top:12px">${btn('💵 Приняла наличные — передам администратору', 'tPayDo', { sid, ym, key })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
};
/* Наличные педагог не зачитывает (решение владельца 30.09.2026): «Приняла наличные» — заявка
   администратору, он зачтёт оплату, когда получит деньги. Перевод на счёт школы — зачёт сразу. */
const tPayChips = () => `<div class="chips">${[['cash', '💵 Наличные'], ['receipt_bank', '🏦 Перевод на счёт школы']].map(([v, n]) => `<button class="chip" id="pm-${v}" aria-pressed="${v === (state.ui.tPayMethod || 'cash')}" data-act="tPayMethod" data-p='${esc(JSON.stringify({ v }))}'>${n}</button>`).join('')}</div>
  <div class="hint" id="pm-note" style="margin-top:6px">${(state.ui.tPayMethod || 'cash') === 'cash' ? 'Наличные зачтёт администратор, когда вы передадите ему деньги. Родитель до этого видит «ждёт подтверждения школы».' : 'Деньги пришли на счёт школы — оплата зачтётся сразу.'}</div>
  <div id="pm-file" style="margin-top:10px;display:${(state.ui.tPayMethod || 'cash') === 'cash' ? 'none' : 'block'}">
    <label for="tp-file" class="btn" id="tp-label" style="display:block;text-align:center;background:var(--warn-soft);color:var(--warn);border:2px dashed var(--warn)">📎 Прикрепить чек перевода (фото или PDF)</label>
    <input id="tp-file" type="file" accept="image/*,application/pdf" style="position:absolute;width:1px;height:1px;opacity:0" onchange="tReceiptPicked(this)">
    <div class="hint" style="margin-top:4px">Без чека перевод отметить нельзя — его увидит администратор.</div></div>`;
/* Файл выбран — подсветка снимается, в кнопке имя файла. */
function tReceiptPicked(input) {
  const l = document.getElementById('tp-label'); if (!l) return;
  const f = input.files && input.files[0];
  if (f) { l.textContent = `✅ Чек прикреплён: ${f.name}`; l.style.cssText = 'display:block;text-align:center;background:var(--ok-soft);color:var(--ok);border:2px solid var(--ok)'; }
}
/* Перевод педагог зачитывает только с чеком (решение владельца 02.10.2026): файл уходит администраторам,
   receiptId прикладывается к каждой отметке оплаты этого ученика. */
const tReceiptChosen = sid => (state.ui.tReceipt && state.ui.tReceipt.sid === sid) || !!(document.getElementById('tp-file') || {}).files?.length;
async function tReceiptId(sid, ym, amount) {
  if (state.ui.tReceipt && state.ui.tReceipt.sid === sid) return state.ui.tReceipt.id;
  const file = document.getElementById('tp-file').files[0];
  const form = new FormData(); form.append('ym', ym); form.append('amount', String(amount || 0)); form.append('file', file, file.name);
  const r = await apiForm(`/bills/student/${sid}/receipt`, form);
  state.ui.tReceipt = { sid, id: r.receiptId };
  return r.receiptId;
}
async function tCashDo(sid, ym, parts) {
  const r = await api(`/bills/student/${sid}/cash`, { method: 'POST', body: { ym, parts } });
  return r.amount;
}
ACT.tPayMethod = ({ v }) => {
  state.ui.tPayMethod = v;
  document.querySelectorAll('.chip[id^="pm-"]').forEach(b => b.setAttribute('aria-pressed', b.id === `pm-${v}`));
  const note = document.getElementById('pm-note');
  if (note) note.textContent = v === 'cash' ? 'Наличные зачтёт администратор, когда вы передадите ему деньги. Родитель до этого видит «ждёт подтверждения школы».' : 'Деньги пришли на счёт школы — оплата зачтётся сразу.';
  document.querySelectorAll('[data-act="tbillDo"],[data-act="tPayDo"]').forEach(b => { b.textContent = v === 'cash' ? '💵 Приняла наличные — передам администратору' : '✅ Подтвердить'; });
  const pf = document.getElementById('pm-file'); if (pf) pf.style.display = v === 'cash' ? 'none' : 'block';
};
ACT.tPayDo = async ({ sid, ym, key, amount: forced, force }) => {
  const amount = forced || +val('pay-a');
  if (!amount) { toast('Укажите сумму'); return; }
  if (state.ui.tPaying) return;
  if ((state.ui.tPayMethod || 'cash') !== 'cash' && !tReceiptChosen(sid)) { toast('Прикрепите чек перевода'); return; }                     // двойное нажатие зачитывало оплату дважды (Андреянова 28.09)
  state.ui.tPaying = true;
  document.querySelectorAll('.sheet .btn').forEach(b => { b.disabled = true; });
  try {
    const lessonIds = [...((state.ui.tsel && state.ui.tsel.picked) || [])];
    if ((state.ui.tPayMethod || 'cash') === 'cash') {
      const sum = await tCashDo(sid, ym, [{ key, amount, lessonIds }]);
      if (state.ui.tsel) state.ui.tsel.key = '';
      closeSheet(); render(); toast(`Передано администратору: ${fmt(sum)} — он зачтёт, когда получит деньги`);
      return;
    }
    const receiptId = await tReceiptId(sid, ym, amount);
    const r = await api(`/bills/student/${sid}/pay`, { method: 'POST', body: { ym, key, amount, force: !!force, method: 'receipt_bank', lessonIds, receiptId } });
    if (state.ui.tsel) state.ui.tsel.key = '';
    closeSheet(); render(); toast(r.credited ? `Зачтено ${fmt(r.credited)}${r.overpaid ? ` (переплата ${fmt(r.overpaid)})` : ''}` : 'Закрывать нечего — остатков нет');
  } catch (e) {
    closeSheet();
    // остаток меньше суммы (экран устарел или платят больше) — спрашиваем, что зачесть
    if (e.status === 409 && e.data && e.data.needsConfirm) return overpaySheet(e.data, 'tPayDo', { sid, ym, key });
    toast(errText(e));
  } finally { state.ui.tPaying = false; }
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
