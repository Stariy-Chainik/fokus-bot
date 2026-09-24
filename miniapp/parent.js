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
  // по умолчанию — только то, что нужно оплатить; закрытые месяцы прячем за кнопкой
  const open = d.months.filter(m => m.rest);
  const closed = d.months.filter(m => !m.rest);
  const showAll = !!state.ui.pAllBills;
  const shown = showAll ? d.months : open;
  const monthCell = m => cell({
    lead: m.rest ? (m.paid ? '⏳' : '⬜') : '✅', plain: true,
    t: `${MON_NOM[+m.ym.slice(5) - 1]} ${m.ym.slice(0, 4)}`,
    s: m.rest ? (m.paid ? `оплачено ${fmt(m.paid)}, к доплате ${fmt(m.rest)}` : `к оплате ${fmt(m.rest)}`) : 'оплачено полностью',
    r: `<b class="${m.rest ? 'bad' : 'ok'}">${fmt(m.rest || m.accrued)}</b>`,
    go: 'p.bill', p: { ym: m.ym },
  });
  return { title: 'Счета', html: `
    ${kidChips('p.bills')}
    <div class="h2">${esc(d.student.name)}</div>
    ${shown.length ? list(shown.map(monthCell))
      : d.months.length ? empty('Всё оплачено', '<p class="hint" style="margin:0">Новый счёт появится после следующих занятий</p>')
      : empty('Счетов пока нет', '<p class="hint" style="margin:0">Они появятся после первых занятий</p>')}
    ${closed.length ? `<div style="margin-top:12px">${btn(showAll ? 'Скрыть оплаченные' : `📜 Оплаченные месяцы · ${closed.length}`, 'pAllBills', {}, 'ghost')}</div>` : ''}` };
};
ACT.pAllBills = () => { state.ui.pAllBills = !state.ui.pAllBills; render(); };

/* Счёт: позиции и занятия отмечаются прямо здесь, сумма считается на лету.
   По умолчанию отмечено всё неоплаченное — тогда это обычная оплата счёта целиком. */
const bsel = (sid, ym, rows) => {
  const id = `${sid}:${ym}`;
  const cur = state.ui.bsel;
  if (cur && cur.id === id) return cur;
  const sel = {};
  rows.filter(r => r.rest).forEach(r => { sel[r.key] = { all: true, lessons: new Set() }; });
  return (state.ui.bsel = { id, sel });
};
/* Сумма к оплате: позиция целиком — её остаток, иначе сумма отмеченных занятий. */
const bsum = (rows, sel) => rows.reduce((total, r) => {
  const pick = sel[r.key];
  if (!pick) return total;
  if (pick.all) return total + r.rest;
  return total + r.lessons.filter(l => !l.paid && pick.lessons.has(l.id)).reduce((a, l) => a + l.amount, 0);
}, 0);

SCREENS['p.bill'] = async ({ ym }) => {
  const b = await api(`/bill/${kid()}/${ym}`);
  const { sel } = bsel(kid(), ym, b.rows);
  state.ui.bsel.rows = b.rows;                       // действиям выбора нужен состав позиций
  const total = bsum(b.rows, sel);
  const mark = on => `<span class="mark ${on ? 'on' : ''}">${on ? '✓' : ''}</span>`;
  const line = (r, l) => {
    const pick = sel[r.key];
    const what = `${l.type === 'group' ? '👥' : '👤'} ${fdate(l.date)} · ${l.durationMin} мин`;
    // прямая оплата педагогу: выбирать нечего — школе за них не платят
    if (r.direct) return `<div class="lesson-line"><span></span><span>${what}</span><span class="amt">${fmt(l.amount)}</span></div>`;
    if (l.paid) return `<div class="lesson-line"><span class="mark paid">✓</span><span>${what}<div class="d">оплачено</div></span><span class="amt">${fmt(l.amount)}</span></div>`;
    const on = !!pick && (pick.all || pick.lessons.has(l.id));
    return `<button class="lesson-line pick" data-act="bPickLesson" data-p='${esc(JSON.stringify({ key: r.key, id: l.id }))}'>${mark(on)}<span>${what}</span><span class="amt">${fmt(l.amount)}</span></button>`;
  };
  const head = r => {
    const pick = sel[r.key];
    const on = !!pick && (pick.all || pick.lessons.size > 0);
    const money = `${fmt(r.accrued)}${r.direct || r.rest ? '' : ' ✓'}`;
    const icon = r.direct ? '🤝' : r.subscription ? '💳' : (r.lessons.some(l => l.type === 'group') ? '👥' : '👨‍🏫');
    if (!r.rest) return `<div class="grp"><span>${icon} ${esc(r.name)}</span><span class="money">${money}</span></div>`;
    return `<button class="grp pick" data-act="bPickRow" data-p='${esc(JSON.stringify({ key: r.key }))}'>
      <span>${mark(on)} ${icon} ${esc(r.name)}</span><span class="money">${money}</span></button>`;
  };
  // блок позиции: заголовок с галочкой, свёрнутые занятия, переплата
  const block = r => {
    const open = !!(state.ui.bopen || {})[r.key];
    const unpaid = r.lessons.filter(l => !l.paid).length;
    return `${head(r)}
      ${r.subscription ? `<div class="lesson-line"><span></span><span class="hint">абонемент за месяц, целиком</span><span></span></div>`
        : open ? r.lessons.map(l => line(r, l)).join('')
        : `<button class="lesson-line pick" data-act="bToggleRow" data-p='${esc(JSON.stringify({ key: r.key }))}'><span></span><span class="hint">${plural(r.lessons.length, ['занятие', 'занятия', 'занятий'])}${!r.direct && unpaid ? `, ${unpaid} не оплачено` : ''} — показать</span><span class="hint">▾</span></button>`}
      ${open && !r.subscription ? `<button class="lesson-line pick" data-act="bToggleRow" data-p='${esc(JSON.stringify({ key: r.key }))}'><span></span><span class="hint">свернуть</span><span class="hint">▴</span></button>` : ''}
      ${r.overpaid ? `<div class="lesson-line"><span></span><span class="hint">переплата ${fmt(r.overpaid)} — учтём в следующем месяце</span><span></span></div>` : ''}`;
  };
  // позиции раскладываем по смыслу: абонемент → группы → индивидуальные, у каждого раздела свой итог
  const kind = r => r.direct ? 'direct' : r.subscription ? 'sub'
    : (r.lessons.filter(l => l.type === 'group').length >= r.lessons.length / 2 ? 'group' : 'solo');
  const parts = [['sub', 'Абонемент'], ['group', 'Групповые занятия'], ['solo', 'Индивидуальные и парные'],
                 ['direct', 'Оплачивается педагогу напрямую']];
  // считаем только поштучные занятия: абонемент идёт строкой за месяц,
  // прямая оплата — мимо школы. Полное расписание — во вкладке «Занятия»
  const lessonsAll = b.rows.filter(r => !r.subscription && !r.direct).flatMap(r => r.lessons);
  const nGroup = lessonsAll.filter(l => l.type === 'group').length;
  const section = ([id, title]) => {
    const rows = b.rows.filter(r => kind(r) === id);
    if (!rows.length) return '';
    const sum = rows.reduce((a, r) => a + r.accrued, 0);
    return `<div class="eyebrow" style="display:flex;justify-content:space-between"><span>${title}</span><span class="money">${fmt(sum)}</span></div>
      <div class="card bill">${rows.map(block).join('')}</div>
      ${id === 'direct' ? '<p class="hint" style="margin:6px 2px 0">Эти занятия вы оплачиваете педагогу лично — в сумму «К оплате» они не входят, школа их не отслеживает.</p>' : ''}`;
  };
  return { title: `${MON_NOM[+ym.slice(5) - 1]} ${ym.slice(0, 4)}`, html: `
    <div class="card pad"><div style="font-weight:800;font-size:16px">${esc(b.student.name)}</div>
      <div class="hint">начислено ${fmt(b.accrued)}${b.paid ? ` · оплачено ${fmt(b.paid)}` : ''}</div>
      ${lessonsAll.length ? `<div class="hint">${plural(lessonsAll.length, ['занятие оплачивается', 'занятия оплачиваются', 'занятий оплачиваются'])} поштучно: ${nGroup} в группах, ${lessonsAll.length - nGroup} индивидуальных</div>` : ''}</div>
    ${b.rows.length ? `${parts.map(section).join('')}
      <div class="card" style="margin-top:10px"><div class="total"><span>К оплате</span><span class="big ${total ? 'bad' : 'ok'}">${b.rest ? fmt(total) : '✓ оплачено'}</span></div></div>`
      : empty('За этот месяц начислений нет')}
    ${b.rest ? `<div style="margin-top:12px">${btn(total ? `💳 Оплатить ${fmt(total)}` : 'Отметьте, что оплачиваете', 'pPayAsk', { ym, rest: total, sel: true }, total ? '' : 'sec')}</div>
      <p class="hint" style="margin-top:8px">Снимите галочки с того, что платите позже — сумма пересчитается.</p>` : ''}` };
};

/* Раскрыть/свернуть занятия позиции: по умолчанию в счёте видны только суммы. */
ACT.bToggleRow = ({ key }) => {
  const o = (state.ui.bopen = state.ui.bopen || {});
  o[key] = !o[key];
  render();
};

/* Позиция целиком ⇄ снята. Занятие: первый тап переводит позицию в частичный выбор. */
ACT.bPickRow = ({ key }) => {
  const st = state.ui.bsel.sel;
  if (st[key] && (st[key].all || st[key].lessons.size)) delete st[key];
  else st[key] = { all: true, lessons: new Set() };
  render();
};
ACT.bPickLesson = ({ key, id }) => {
  const st = state.ui.bsel.sel;
  const row = (state.ui.bsel.rows || []).find(r => r.key === key);
  if (!st[key]) st[key] = { all: false, lessons: new Set([id]) };
  else if (st[key].all) { st[key] = { all: false, lessons: new Set((row ? row.lessons : []).filter(l => !l.paid).map(l => l.id)) }; st[key].lessons.delete(id); }
  else { st[key].lessons.has(id) ? st[key].lessons.delete(id) : st[key].lessons.add(id); }
  if (!st[key].all && !st[key].lessons.size) delete st[key];
  render();
};


/* ── Оплата ──────────────────────────────────────────────────────────── */
ACT.pPayAsk = ({ ym, rest, sel }) => {
  if (!rest) { toast('Отметьте, что оплачиваете'); return; }
  const m = (state.me && state.me.methods) || {};
  const part = sel && state.ui.bsel && bsum(state.ui.bsel.rows || [], state.ui.bsel.sel) < (state.ui.bsel.rows || []).reduce((a, r) => a + r.rest, 0);
  // тот же набор, что в боте: СБП онлайн → наличные → реквизиты (карта и СБП по чеку не показываются)
  const rows = [];
  // порядок способов: наличные → по реквизитам с чеком → СБП онлайн
  const child = (state.me.children || []).find(c => c.id === kid()) || {};
  const cashPreferred = child.cashPreferred;
  const cashOn = m.cash && child.cashAllowed !== false;     // в части групп наличные не принимают
  if (cashOn) rows.push(btn('💵 Наличные', 'pPayDo', { ym, rest, sel, method: 'cash' }));
  if (m.bank) rows.push(btn('🏦 По реквизитам', 'pPayDo', { ym, rest, sel, method: 'bank' }, 'ghost'));
  if (m.yookassa) rows.push(btn('📱 СБП онлайн', 'pPayDo', { ym, rest, sel, method: 'ysbp' }, 'ghost'));
  sheet(`<h3>Оплата ${fmt(rest)}</h3><div class="hint">${esc(kidName(kid()))} · ${MON_NOM[+ym.slice(5) - 1]}${part ? ' · за отмеченное' : ''}</div>
    <p class="hint" style="margin-top:10px">${cashPreferred ? 'В этой группе удобнее наличными — передайте администратору или педагогу. ' : ''}По реквизитам — после перевода пришлите чек в бот. СБП онлайн — оплата зачтётся сама, чек не нужен.</p>
    <div style="margin-top:12px">${rows.join('') || '<div class="hint">Способы оплаты не настроены — напишите администратору.</div>'}
    ${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
};
ACT.pPayDo = async ({ ym, method, sel }) => {
  if (state.ui.paying) { toast('Отправляем, подождите…'); return; }
  state.ui.paying = true;
  // блокируем кнопки: запрос к таблицам может идти несколько секунд
  const sheetEl = document.querySelector('.sheet');
  if (sheetEl) sheetEl.querySelectorAll('.btn').forEach(b => { b.disabled = true; if (b.dataset.act === 'pPayDo') b.textContent = 'Отправляем…'; });
  const body = { studentId: kid(), ym, method };
  if (sel && state.ui.bsel) {                        // что отмечено в счёте: позиции и занятия внутри них
    const st = state.ui.bsel.sel;
    body.keys = Object.keys(st);
    body.lessonIds = Object.values(st).flatMap(x => (x.all ? [] : [...x.lessons]));
  }
  try {
    const r = await api('/pay', { method: 'POST', body });
    closeSheet();
    if (r.url) { try { tg ? tg.openLink(r.url) : window.open(r.url, '_blank'); } catch (_) { window.open(r.url, '_blank'); } toast('Открываю оплату…'); return; }
    if (r.details) { sheet(`<h3>Реквизиты · ${fmt(r.amount)}</h3>
    ${r.qr ? `<img src="${r.qr}" alt="QR для оплаты" style="display:block;width:180px;max-width:60%;margin:12px auto;border-radius:10px;background:#fff;padding:8px">` : ''}
    <pre class="hint" style="white-space:pre-wrap;margin:10px 0">${esc(r.details)}</pre><div class="hint">${esc(r.hint || '')}</div><div style="margin-top:12px">${btn('Понятно', 'closeSheet', {}, 'sec')}</div>`); return; }
    if (r.ok) { render(); toast(r.duplicate ? 'Уведомление уже отправлено' : 'Администратор получил уведомление'); return; }
  } catch (e) { closeSheet(); toast(errText(e)); }
  finally { state.ui.paying = false; }
};

/* ── Занятия ─────────────────────────────────────────────────────────── */
/* Календарь месяца: точка на занятие, цвет — группа или индивидуальное. Тап ведёт к дню в журнале. */
const WD_SHORT = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'];
function calendar(period, byDay, picked = '') {
  const [y, m] = period.split('-').map(Number);
  const days = new Date(y, m, 0).getDate();
  const lead = (new Date(y, m - 1, 1).getDay() + 6) % 7;      // неделя с понедельника
  const today = new Date().toISOString().slice(0, 10);
  const cells = WD_SHORT.map(w => `<div class="wd">${w}</div>`);
  for (let i = 0; i < lead; i++) cells.push('<div class="d empty"></div>');
  for (let day = 1; day <= days; day++) {
    const date = `${period}-${String(day).padStart(2, '0')}`;
    const items = byDay[date] || [];
    const dots = items.slice(0, 4).map(l => `<i class="${l.type === 'group' ? '' : 'solo'}"></i>`).join('');
    const cls = `d${items.length ? '' : ' empty'}${date === today ? ' today' : ''}${date === picked ? ' on' : ''}`;
    cells.push(items.length
      ? `<button class="${cls}" data-act="pDayPick" data-p='${esc(JSON.stringify({ d: date }))}'>${day}<span class="dots">${dots}</span></button>`
      : `<div class="${cls}">${day}</div>`);
  }
  return `<div class="cal">${cells.join('')}</div>`;
}
ACT.pDayPick = ({ d }) => { state.ui.pDay = state.ui.pDay === d ? '' : d; render(); };
ACT.pDayReset = () => { state.ui.pDay = ''; render(); };

SCREENS['p.lessons'] = async ({ ym }) => {
  const period = ym || lastPeriods(1)[0];
  const d = await api(`/lessons/${kid()}?ym=${period}`);
  // здесь только расписание: ни сумм, ни статусов оплаты — деньги живут в «Счетах»
  const teachers = [];
  d.lessons.forEach(l => { if (!teachers.some(t => t[0] === l.teacherId)) teachers.push([l.teacherId, l.teacher]); });
  const cur = teachers.some(t => t[0] === state.ui.pTeacher) ? state.ui.pTeacher : '';
  const shown = cur ? d.lessons.filter(l => l.teacherId === cur) : d.lessons;
  const chips = teachers.length > 1
    ? chipsAct('pTeacher', cur, [['', `Все · ${d.lessons.length}`],
        ...teachers.map(([id, name]) => [id, `${esc(plainName(name))} · ${d.lessons.filter(l => l.teacherId === id).length}`])])
    : '';
  const byDay = {};
  shown.forEach(l => (byDay[l.date] = byDay[l.date] || []).push(l));
  const day = byDay[state.ui.pDay] ? state.ui.pDay : '';
  const inView = day ? byDay[day] : shown;
  const days = Object.keys(byDay).sort((a, b) => (a < b ? 1 : -1));
  const minutes = inView.reduce((a, l) => a + l.durationMin, 0);
  const hours = `${Math.floor(minutes / 60)} ч${minutes % 60 ? ` ${minutes % 60} мин` : ''}`;
  const dayBlock = dd => `<div class="eyebrow" id="d-${dd}">${fdate(dd)}</div>${list(byDay[dd].map(l => cell({
    lead: l.type === 'group' ? '👥' : '👤', plain: true,
    t: esc(l.group || l.teacher),
    s: `${l.durationMin} мин${l.group ? ` · ${esc(l.teacher)}` : ''}`,
  })))}`;
  return { title: 'Занятия', html: `
    ${kidChips('p.lessons', { ym: period })}
    ${monthChips('p.lessons', period, {})}
    ${chips}
    ${shown.length ? `<div class="card pad" style="margin-bottom:10px">
        <div style="font-weight:700">${day ? `${fdate(day)} · ` : ''}${plural(inView.length, ['занятие', 'занятия', 'занятий'])} · ${hours}</div>
        ${day ? '<div class="hint">показан один день — нажмите ещё раз, чтобы вернуть месяц</div>' : ''}
        <div style="margin-top:10px">${calendar(period, byDay, day)}</div>
        <div class="hint" style="margin-top:6px"><i style="display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--accent);vertical-align:middle"></i> группа · <i style="display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--ok);vertical-align:middle"></i> индивидуальное</div>
        ${day ? `<div style="margin-top:10px">${btn('✕ Весь месяц', 'pDayReset', {}, 'ghost')}</div>` : ''}
      </div>${(day ? [day] : days).map(dayBlock).join('')}`
      : empty(cur ? 'У этого педагога занятий в месяце нет' : 'В этом месяце занятий не было')}
    <p class="hint" style="margin-top:8px">Это расписание занятий. Суммы и оплата — во вкладке «Счета».</p>` };
};
ACT.pTeacher = ({ v }) => { state.ui.pTeacher = v; state.ui.pDay = ''; render(); };

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
