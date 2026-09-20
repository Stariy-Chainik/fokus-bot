/* Фокус · Mini App — кабинет родителя (экраны p.*).
   Грузится после app.js и пользуется его каркасом: api(), SCREENS, ACT, cell/list/kpi.
   Данные — /api/parent/*: родитель видит только своих детей, суммы считает сервер. */
'use strict';

/* Выбранный ребёнок: в кабинете с одним ребёнком переключателя нет. */
const kid = () => state.ui.kid || (state.me && state.me.children && state.me.children[0] || {}).id || '';
const kidName = id => ((state.me && state.me.children) || []).find(c => c.id === id)?.name || '';
const kidChips = (screen, extra = {}) => {
  const kids = (state.me && state.me.children) || [];
  if (kids.length < 2) return '';
  return `<div class="chips scroll">${kids.map(c => `<button class="chip" aria-pressed="${kid() === c.id}" data-act="pKid" data-p='${esc(JSON.stringify({ v: c.id, screen, extra }))}'>${esc(c.name.split(' ')[0])}</button>`).join('')}</div>`;
};
ACT.pKid = ({ v, screen, extra }) => { state.ui.kid = v; state.stack.pop(); go(screen, extra || {}); };

/* ── Сводка ──────────────────────────────────────────────────────────── */
SCREENS['p.home'] = async () => {
  const h = await api('/home');
  const mon = ym => MON_NOM[+ym.slice(5) - 1];
  return { title: 'Мои дети', html: `
    ${hero(h.rest ? `К оплате ${fmt(h.rest)}` : 'Всё оплачено, спасибо')}
    ${h.children.map(c => {
      const m = c.thisMonth;
      return `<div class="card pad" style="margin-bottom:10px"><div style="display:flex;justify-content:space-between;gap:10px;align-items:baseline">
        <div style="font-weight:800;font-size:16px">${esc(c.name)}</div>
        <div class="money" style="font-weight:800;color:${c.rest ? 'var(--bad)' : 'var(--ok)'}">${c.rest ? fmt(c.rest) : '✓'}</div></div>
      <div class="hint">${m ? `${mon(h.period)}: начислено ${fmt(m.accrued)}, оплачено ${fmt(m.paid)}` : `${mon(h.period)}: занятий пока нет`}</div>
      <div class="chips" style="margin:10px 0 0">
        <button class="chip" data-act="pOpen" data-p='${esc(JSON.stringify({ id: c.id, screen: 'p.bills' }))}'>🧾 Счета</button>
        <button class="chip" data-act="pOpen" data-p='${esc(JSON.stringify({ id: c.id, screen: 'p.lessons' }))}'>📋 Занятия</button>
        <button class="chip" data-act="pOpen" data-p='${esc(JSON.stringify({ id: c.id, screen: 'p.diary' }))}'>📓 Дневник</button>
      </div>
      ${c.rest ? `<div style="margin-top:10px">${btn(`💳 Оплатить ${fmt(c.rest)}`, 'pOpen', { id: c.id, screen: 'p.bills' })}</div>` : ''}</div>`;
    }).join('')}
    <p class="hint">Суммы считает школа по отмеченным занятиям. Вопросы по счёту — администратору в чате бота.</p>` };
};
ACT.pOpen = ({ id, screen }) => { state.ui.kid = id; go(screen, {}); };

/* ── Счета ───────────────────────────────────────────────────────────── */
SCREENS['p.bills'] = async () => {
  const d = await api(`/bills?student=${kid()}`);
  return { title: 'Счета', html: `
    ${kidChips('p.bills')}
    <div class="h2">${esc(d.student.name)}</div>
    ${d.months.length ? list(d.months.map(m => cell({
      lead: m.rest ? (m.paid ? '⏳' : '⬜') : '✅', plain: true,
      t: `${MON_NOM[+m.ym.slice(5) - 1]} ${m.ym.slice(0, 4)}`,
      s: m.rest ? (m.paid ? `оплачено ${fmt(m.paid)}, к доплате ${fmt(m.rest)}` : `к оплате ${fmt(m.rest)}`) : 'оплачено полностью',
      r: `<b class="${m.rest ? 'bad' : 'ok'}">${fmt(m.rest || m.accrued)}</b>`,
      go: 'p.bill', p: { ym: m.ym },
    }))) : empty('Счетов пока нет', '<p class="hint" style="margin:0">Они появятся после первых занятий</p>')}` };
};

SCREENS['p.bill'] = async ({ ym }) => {
  const b = await api(`/bill/${kid()}/${ym}`);
  const line = l => `<div class="lesson-line"><span>${l.paid ? '✅' : '⬜'}</span><span>${fdate(l.date)} · ${l.durationMin} мин</span><span class="amt">${fmt(l.amount)}</span></div>`;
  return { title: `${MON_NOM[+ym.slice(5) - 1]} ${ym.slice(0, 4)}`, html: `
    <div class="card pad"><div style="font-weight:800;font-size:16px">${esc(b.student.name)}</div>
      <div class="hint">начислено ${fmt(b.accrued)}${b.paid ? ` · оплачено ${fmt(b.paid)}` : ''}</div></div>
    ${b.rows.length ? `<div class="card bill" style="margin-top:10px">${b.rows.map(r => `
      <div class="grp"><span>${r.subscription ? '💳' : '👨‍🏫'} ${esc(r.name)}</span><span class="money">${fmt(r.accrued)}${r.rest ? '' : ' ✓'}</span></div>
      ${r.subscription ? '<div class="lesson-line"><span></span><span class="hint">абонемент за месяц</span><span></span></div>' : r.lessons.map(line).join('')}
      ${r.overpaid ? `<div class="lesson-line"><span></span><span class="hint">переплата ${fmt(r.overpaid)} — учтём в следующем месяце</span><span></span></div>` : ''}`).join('')}
      <div class="total"><span>К оплате</span><span class="big ${b.rest ? 'bad' : 'ok'}">${b.rest ? fmt(b.rest) : '✓ оплачено'}</span></div></div>`
      : empty('За этот месяц начислений нет')}
    ${b.rest ? `<div style="margin-top:12px">${btn(`💳 Оплатить всё ${fmt(b.rest)}`, 'pPayAsk', { ym, rest: b.rest })}
      ${b.rows.filter(r => r.rest).length > 1 || b.rows.some(r => !r.subscription && r.rest)
        ? goBtn(b.rows.filter(r => r.rest).length > 1 ? '🧾 Оплатить часть' : '🧾 Выбрать занятия', 'p.pick', { ym }, 'sec') : ''}</div>` : ''}` };
};

/* Оплата части счёта: сначала позиция (педагог или абонемент), потом занятия. */
SCREENS['p.pick'] = async ({ ym, key }) => {
  const b = await api(`/bill/${kid()}/${ym}`);
  const open = b.rows.filter(r => r.rest);
  if (!key && open.length > 1) {
    return { title: 'За что платим?', html: `
      <div class="hint" style="margin-bottom:10px">${esc(b.student.name)} · ${MON_NOM[+ym.slice(5) - 1]}. Выберите, что оплачиваете сейчас.</div>
      ${list(open.map(r => cell({
        lead: r.subscription ? '💳' : '👨‍🏫', plain: true, t: esc(r.name),
        s: r.paid ? `оплачено ${fmt(r.paid)} из ${fmt(r.accrued)}` : `начислено ${fmt(r.accrued)}`,
        r: `<b>${fmt(r.rest)}</b>`, go: 'p.pick', p: { ym, key: r.key },
      })))}
      <div style="margin-top:12px">${btn(`💳 Оплатить всё ${fmt(b.rest)}`, 'pPayAsk', { ym, rest: b.rest }, 'sec')}</div>` };
  }
  const row = open.find(r => r.key === key) || open[0] || b.rows[0];
  if (row.subscription || !row.lessons.length) {
    return { title: esc(row.name), html: `
      <div class="card pad"><div style="font-weight:800;font-size:16px">${esc(row.name)}</div>
        <div class="hint">${MON_NOM[+ym.slice(5) - 1]} · начислено ${fmt(row.accrued)}${row.paid ? ` · оплачено ${fmt(row.paid)}` : ''}</div>
        <div class="money" style="font-size:26px;font-weight:800;margin-top:8px">${fmt(row.rest)}</div></div>
      <p class="hint" style="margin-top:10px">Абонемент оплачивается целиком за месяц.</p>
      <div style="margin-top:12px">${btn(`💳 Оплатить ${fmt(row.rest)}`, 'pPayAsk', { ym, rest: row.rest, key: row.key })}</div>` };
  }
  const ui = state.ui.psel || (state.ui.psel = {});
  const selKey = `${kid()}:${ym}:${row.key}`;                 // смена позиции сбрасывает отметки
  if (ui.key !== selKey) { ui.key = selKey; ui.picked = new Set(); }
  const total = row.lessons.filter(l => !l.paid && ui.picked.has(l.id)).reduce((a, l) => a + l.amount, 0);
  return { title: 'Выбрать занятия', html: `
    <div class="hint" style="margin-bottom:10px">${esc(row.name)} · ${MON_NOM[+ym.slice(5) - 1]}. Отметьте занятия, за которые платите сейчас.</div>
    <div class="list">${row.lessons.map(l => l.paid
      ? `<div class="lesson-line"><span class="mark paid">✓</span><span>${fdate(l.date)} · ${l.durationMin} мин<div class="d">оплачено</div></span><span class="amt">${fmt(l.amount)}</span></div>`
      : `<button class="lesson-line pick" data-act="pPick" data-p='${esc(JSON.stringify({ id: l.id }))}'><span class="mark ${ui.picked.has(l.id) ? 'on' : ''}">${ui.picked.has(l.id) ? '✓' : ''}</span><span>${fdate(l.date)} · ${l.durationMin} мин</span><span class="amt">${fmt(l.amount)}</span></button>`).join('')}</div>
    <div style="margin-top:12px">${btn(total ? `💳 Оплатить ${fmt(total)}` : 'Выберите занятия', 'pPayAsk',
      { ym, rest: total, key: row.key, picked: true }, total ? '' : 'sec')}</div>` };
};
ACT.pPick = ({ id }) => { const s = state.ui.psel.picked; s.has(id) ? s.delete(id) : s.add(id); render(); };

/* ── Оплата ──────────────────────────────────────────────────────────── */
ACT.pPayAsk = ({ ym, rest, key, picked }) => {
  if (!rest) { toast('Нечего оплачивать'); return; }
  const m = (state.me && state.me.methods) || {};
  // тот же набор, что в боте: СБП онлайн → наличные → реквизиты (карта и СБП по чеку не показываются)
  const rows = [];
  if (m.yookassa) rows.push(btn('📱 СБП онлайн', 'pPayDo', { ym, rest, key, picked, method: 'ysbp' }));
  if (m.cash) rows.push(btn('💵 Наличные', 'pPayDo', { ym, rest, key, picked, method: 'cash' }, 'sec'));
  if (m.bank) rows.push(btn('🏦 По реквизитам', 'pPayDo', { ym, rest, key, picked, method: 'bank' }, 'ghost'));
  sheet(`<h3>Оплата ${fmt(rest)}</h3><div class="hint">${esc(kidName(kid()))} · ${MON_NOM[+ym.slice(5) - 1]}${picked ? ' · за выбранные занятия' : ''}</div>
    <p class="hint" style="margin-top:10px">СБП онлайн — оплата зачтётся сама, чек не нужен. По реквизитам — после перевода пришлите чек в бот.</p>
    <div style="margin-top:12px">${rows.join('') || '<div class="hint">Способы оплаты не настроены — напишите администратору.</div>'}
    ${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
};
ACT.pPayDo = async ({ ym, method, key, picked }) => {
  const body = { studentId: kid(), ym, method };
  if (key) body.keys = [key];
  if (picked && state.ui.psel) body.lessonIds = [...state.ui.psel.picked];
  try {
    const r = await api('/pay', { method: 'POST', body });
    closeSheet();
    if (r.url) { try { tg ? tg.openLink(r.url) : window.open(r.url, '_blank'); } catch (_) { window.open(r.url, '_blank'); } toast('Открываю оплату…'); return; }
    if (r.details) { sheet(`<h3>Реквизиты · ${fmt(r.amount)}</h3>
    ${r.qr ? `<img src="${r.qr}" alt="QR для оплаты" style="display:block;width:180px;max-width:60%;margin:12px auto;border-radius:10px;background:#fff;padding:8px">` : ''}
    <pre class="hint" style="white-space:pre-wrap;margin:10px 0">${esc(r.details)}</pre><div class="hint">${esc(r.hint || '')}</div><div style="margin-top:12px">${btn('Понятно', 'closeSheet', {}, 'sec')}</div>`); return; }
    if (r.ok) { render(); toast('Администратор получил уведомление'); return; }
  } catch (e) { closeSheet(); toast(errText(e)); }
};

/* ── Занятия ─────────────────────────────────────────────────────────── */
SCREENS['p.lessons'] = async ({ ym }) => {
  const period = ym || lastPeriods(1)[0];
  const d = await api(`/lessons/${kid()}?ym=${period}`);
  return { title: 'Занятия', html: `
    ${kidChips('p.lessons', { ym: period })}
    ${monthChips('p.lessons', period, {})}
    ${d.lessons.length ? list(d.lessons.map(l => cell({
      lead: l.paid ? '✅' : '⬜', plain: true,
      t: esc(l.group || l.teacher),
      s: `${fdate(l.date)} · ${l.durationMin} мин${l.group ? ` · ${esc(l.teacher)}` : ''}`,
      r: l.amount ? `<b>${fmt(l.amount)}</b>` : 'абонемент',
    }))) : empty('В этом месяце занятий не было')}
    ${d.unpaid ? `<div class="card" style="margin-top:10px"><div class="total"><span>Не оплачено</span><span class="big bad">${fmt(d.unpaid)}</span></div></div>
      <div style="margin-top:12px">${goBtn('🧾 Открыть счёт', 'p.bill', { ym: period })}</div>` : ''}
    <p class="hint" style="margin-top:8px">✅ — занятие закрыто оплатой. Абонементные занятия входят в месячную оплату.</p>` };
};

/* ── Дневник ─────────────────────────────────────────────────────────── */
SCREENS['p.diary'] = async ({ ym }) => {
  const period = ym || lastPeriods(1)[0];
  let d;
  try { d = await api(`/diary/${kid()}?ym=${period}`); }
  catch (e) { return { title: 'Дневник', html: empty('Дневник недоступен', `<p class="hint" style="margin:0">${esc(errText(e))}</p>`) }; }
  if (!d.athlete && !d.entries.length) {
    return { title: 'Дневник', html: `${kidChips('p.diary', { ym: period })}
      ${empty('Дневник пока не ведётся', '<p class="hint" style="margin:0">Дневник тренировок доступен ученикам спортивных групп: ребёнок заводит кабинет спортсмена в боте и записывает тренировки сам.</p>')}` };
  }
  const st = d.stats;
  const topics = Object.entries(st.byTopic || {}).sort((a, b) => b[1] - a[1]);
  return { title: 'Дневник', html: `
    ${kidChips('p.diary', { ym: period })}
    ${monthChips('p.diary', period, {})}
    <div class="kpis">${kpi(st.sessions, 'тренировок')}${kpi(st.minutes + ' мин', 'всего')}</div>
    <div class="kpis" style="margin-top:10px">${kpi(st.avgGrade ? st.avgGrade.toFixed(1) : '—', 'средняя оценка', 'ok')}${kpi(st.points, `очки${d.place ? ` · ${d.place <= 3 ? `${d.placeIcon} ` : ''}${d.place} место` : ''}`)}</div>
    ${topics.length ? `<div class="eyebrow">По танцам</div>${list(topics.map(([t, m]) => cell({ t: esc(t), r: `${Math.round(m)} мин` })))}` : ''}
    <div class="eyebrow">Тренировки</div>
    ${d.entries.length ? list(d.entries.map(e => cell({
      lead: e.grade ? '⭐' : '📝', plain: true, cls: 'wrap',
      t: `${fdate(e.date)} · ${e.minutes} мин`,
      s: `${esc(e.topics.join(', ') || 'без темы')}${e.comment ? ` · ${esc(e.comment)}` : ''}${e.gradeComment ? `<br>📝 ${esc(e.gradeComment)}${e.gradedBy ? ` — ${esc(e.gradedBy)}` : ''}` : ''}`,
      r: e.grade ? `<b>${e.grade}/5</b>` : pill('без оценки', 'mute'),
    }))) : empty('Записей за месяц нет', '<p class="hint" style="margin:0">Ребёнок записывает тренировки сам в боте</p>')}
    ${d.openTasks.length ? `<div class="eyebrow">Задания педагога</div>${list(d.openTasks.map(t => cell({
      lead: '📋', plain: true, cls: 'wrap', t: esc(t.exercise), s: `${t.minutes} мин${t.comment ? ` · ${esc(t.comment)}` : ''}` })))}` : ''}` };
};
