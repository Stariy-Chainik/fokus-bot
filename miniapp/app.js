/* Фокус · Mini App — кабинет администратора.
   Без сборки: один файл, данные только через /api/admin/* (суммы считает сервер). */
'use strict';

/* ── Telegram ────────────────────────────────────────────────────────── */
const tg = window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData !== undefined ? window.Telegram.WebApp : null;
const DEV = new URLSearchParams(location.search).get('dev') === '1';
if (tg) { try { tg.ready(); tg.expand(); } catch (_) { /* старый клиент */ } }
function applyTheme() { document.documentElement.dataset.theme = tg && tg.colorScheme === 'dark' ? 'dark' : (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'); }
applyTheme(); if (tg) tg.onEvent('themeChanged', applyTheme);

/* ── API ─────────────────────────────────────────────────────────────── */
class ApiError extends Error { constructor(status, code) { super(code || `HTTP ${status}`); this.status = status; this.code = code; } }
async function api(path, { method = 'GET', body } = {}) {
  const headers = { 'Accept': 'application/json' };
  if (tg && tg.initData) headers.Authorization = `tma ${tg.initData}`;
  else if (DEV) headers.Authorization = 'dev';
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const resp = await fetch('/api/admin' + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let data = null; try { data = await resp.json(); } catch (_) { /* не JSON */ }
  if (!resp.ok) throw new ApiError(resp.status, data && data.error);
  return data;
}
const ERR_TEXT = { unauthorized: 'Откройте приложение из Telegram — подпись не подтверждена.', forbidden: 'Доступ только для администраторов школы.', not_found: 'Не найдено — возможно, запись удалена.', in_progress: 'Операция уже выполняется, подождите.', nothing_to_send: 'Начислений нет — отправлять нечего.', bot_unavailable: 'Бот недоступен, попробуйте позже.' };
const errText = e => e instanceof ApiError ? (ERR_TEXT[e.code] || `Ошибка сервера (${e.status})`) : 'Нет связи с сервером';

/* ── форматирование ──────────────────────────────────────────────────── */
const fmt = n => Math.round(n || 0).toLocaleString('ru-RU').replace(/ /g, ' ') + ' ₽';
const MON_SHORT = ['янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек'];
const MON_NOM = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
const WD = ['вс','пн','вт','ср','чт','пт','сб'];
const fdate = d => { if (!d) return ''; const x = new Date(d + 'T00:00:00'); return `${x.getDate()} ${MON_SHORT[x.getMonth()]}, ${WD[x.getDay()]}`; };
const fmon = ym => `${MON_NOM[+ym.slice(5) - 1]} ${ym.slice(0, 4)}`;
const plural = (n, f) => { const m = n % 10, h = n % 100; return `${n} ${h > 10 && h < 20 ? f[2] : m === 1 ? f[0] : m > 1 && m < 5 ? f[1] : f[2]}`; };
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const initials = n => n.split(' ').map(x => x[0]).join('').slice(0, 2);
const METHOD = { yookassa: 'Картой онлайн', yookassa_sbp: 'СБП онлайн', receipt_bank: 'По реквизитам', receipt_sbp: 'СБП по чеку', cash: 'Наличные', admin_manual: 'Вручную', '': '' };
const MODE = { subscription: 'абонемент', per_visit: 'по посещению', none: 'без оплаты' };
function lastPeriods(n) { const out = []; const d = new Date(); for (let i = 0; i < n; i++) { const x = new Date(d.getFullYear(), d.getMonth() - i, 1); out.push(`${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}`); } return out; }

/* ── UI-кирпичи ──────────────────────────────────────────────────────── */
const attr = (go, p) => go ? `data-go="${go}" data-p='${esc(JSON.stringify(p || {}))}'` : '';
const cell = ({ lead, t, s, r, go, p, plain, cls = '' }) => `<button class="cell ${lead === undefined ? 'nolead' : ''} ${go ? '' : 'static'} ${cls}" ${attr(go, p)}>${lead !== undefined ? `<span class="lead ${plain ? 'plain' : ''}">${lead}</span>` : ''}<span><div class="t">${t}</div>${s ? `<div class="s">${s}</div>` : ''}</span><span class="r">${r || ''}${go ? '<span class="chev">›</span>' : ''}</span></button>`;
const list = rows => `<div class="list">${rows.join('')}</div>`;
const pill = (txt, kind = 'mute') => `<span class="pill ${kind}">${txt}</span>`;
const btn = (txt, act, p = {}, kind = '') => `<button class="btn ${kind}" data-act="${act}" data-p='${esc(JSON.stringify(p))}'>${txt}</button>`;
const goBtn = (txt, go, p = {}, kind = '') => `<button class="btn ${kind}" ${attr(go, p)}>${txt}</button>`;
const kpi = (v, l, kind = '') => `<div class="kpi ${kind}"><div class="v">${v}</div><div class="l">${l}</div></div>`;
const restPill = x => x.total === 0 ? pill('нет начислений') : x.rest === 0 ? pill('✓ оплачено', 'ok') : x.paid ? pill(`к доплате ${fmt(x.rest)}`, 'warn') : pill(`к оплате ${fmt(x.rest)}`, 'warn');
const skeleton = () => '<div class="skeleton w60"></div><div class="skeleton tall"></div><div class="skeleton"></div><div class="skeleton tall"></div>';

/* ── навигация ───────────────────────────────────────────────────────── */
const TABS = [['a.home', 'Сводка', 'home'], ['a.payhub', 'Оплаты', 'card'], ['a.students', 'Ученики', 'users'], ['a.teachers', 'Педагоги', 'chart'], ['a.more', 'Ещё', 'dots']];
const ICON = {
  home: '<path d="M3 11 12 4l9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>', card: '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18M7 15h4"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M21.5 20a6.5 6.5 0 0 0-5-6.3"/>', chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  dots: '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>',
};
const state = { stack: [{ n: 'a.home' }], ui: {}, me: null };
const cur = () => state.stack[state.stack.length - 1];
let renderSeq = 0;
function go(n, p = {}) { state.stack.push({ n, p }); render(); }
function back() { if (state.stack.length > 1) { state.stack.pop(); render(); } }
function root(n) { state.stack = [{ n }]; render(); }
function refresh() { render(); }
function toast(msg) { const el = document.createElement('div'); el.className = 'toast fade'; el.textContent = msg; document.getElementById('overlay').appendChild(el); setTimeout(() => el.remove(), 2400); }
function sheet(html) { document.getElementById('overlay').insertAdjacentHTML('beforeend', `<div class="sheet-wrap" data-act="closeSheet"><div class="sheet fade" data-stop="1"><div class="grab"></div>${html}</div></div>`); }
function closeSheet() { document.querySelectorAll('.sheet-wrap').forEach(w => w.remove()); }
const val = id => { const el = document.getElementById(id); return el ? el.value : ''; };

/* ── экраны ──────────────────────────────────────────────────────────── */
const SCREENS = {};

SCREENS['a.home'] = async () => {
  const h = await api('/home');
  return { title: 'Школа сегодня', html: `
    <div class="h2">${fdate(h.today)}</div>
    <div class="kpis">${kpi(fmt(h.pendingTotal), `ожидает оплаты за ${MON_NOM[+h.period.slice(5) - 1].toLowerCase()}`, 'warn')}${kpi(h.debtorsCount, `должников за ${MON_NOM[+h.prevPeriod.slice(5) - 1].toLowerCase()} и раньше`, h.debtorsCount ? 'bad' : 'ok')}${kpi(plural(h.lessonsToday, ['занятие', 'занятия', 'занятий']), 'отмечено сегодня')}${kpi(h.studentsCount, 'учеников')}</div>
    <div class="eyebrow">Быстрые действия</div>
    ${list([cell({ lead: '💾', plain: true, t: 'Подтвердить оплату', s: 'ученик → педагог → занятия', go: 'a.pay' }), cell({ lead: '⚠️', plain: true, t: 'Должники', s: 'закрытые месяцы', go: 'a.debtors' }), cell({ lead: '🧾', plain: true, t: 'Счёт ученика', s: 'просмотр и отправка родителям', go: 'a.pay', p: { bill: true } })])}` };
};

SCREENS['a.payhub'] = async () => ({ title: 'Оплаты', html: list([cell({ lead: '💾', plain: true, t: 'Подтвердить оплату', s: 'ученик → педагог → занятия', go: 'a.pay' }), cell({ lead: '🧾', plain: true, t: 'Счёт ученика за период', s: 'просмотр и отправка родителям', go: 'a.pay', p: { bill: true } }), cell({ lead: '⚠️', plain: true, t: 'Должники', s: 'сводный долг по месяцам', go: 'a.debtors' })]) });

SCREENS['a.pay'] = async ({ bill }) => ({ title: bill ? 'Счёт ученика' : 'Подтвердить оплату', html: `<div class="h2">Выберите месяц</div>${list(lastPeriods(3).map((ym, i) => cell({ t: fmon(ym), s: i === 0 ? 'текущий месяц' : 'закрыт', go: 'a.pay.groups', p: { ym, bill } })))}` });

SCREENS['a.pay.groups'] = async ({ ym, bill }) => {
  const d = await api('/pay/groups');
  return { title: fmon(ym), html: d.branches.map(b => `<div class="eyebrow">${esc(b.name)}</div>${list(b.groups.map(g => cell({ lead: '💃', plain: true, t: esc(g.name), s: MODE[g.mode] + (g.price ? ` · ${fmt(g.price)}` : ''), go: 'a.pay.students', p: { ym, g: g.id, gname: g.name, bill } })))}`).join('') + `<div class="eyebrow">Без группы</div>${list([cell({ t: '📋 Все ученики', go: 'a.pay.students', p: { ym, g: '', gname: 'Все ученики', bill } })])}` };
};

SCREENS['a.pay.students'] = async ({ ym, g, gname, bill }) => {
  const d = await api(`/pay/students?ym=${ym}&group=${encodeURIComponent(g || '')}`);
  return { title: gname || 'Ученики', html: `<div class="hint" style="margin-bottom:10px">${fmon(ym)} — выберите ученика</div>${d.students.length ? list(d.students.map(s => cell({ lead: initials(s.name), t: esc(s.name), s: s.total ? `начислено ${fmt(s.total)} · оплачено ${fmt(s.paid)}` : 'нет начислений', r: s.rest ? pill(fmt(s.rest), 'warn') : s.total ? pill('✓', 'ok') : '', go: bill ? 'a.bill' : 'a.pay.student', p: { ym, sid: s.id } }))) : '<div class="empty">В группе нет учеников</div>'}` };
};

SCREENS['a.pay.student'] = async ({ ym, sid }) => {
  const d = await api(`/pay/student/${sid}?ym=${ym}`);
  if (!d.positions.length) return { title: d.student.name, html: `<div class="empty">У ${esc(d.student.name)} за ${fmon(ym)} нет занятий.</div>` };
  const rows = []; let total = 0, paid = 0, rest = 0;
  d.positions.forEach(p => {
    total += p.accrued; paid += Math.min(p.paid, p.accrued); rest += p.remainder;
    p.paidRows.forEach(r => rows.push(cell({ lead: '✅', plain: true, t: `${esc(p.name)} — ${fmt(r.amount)}`, s: `${fdate(r.date)}${r.method ? ' · ' + (METHOD[r.method] || r.method) : ''}` })));
    if (p.remainder > 0) rows.push(cell({ lead: '⏳', plain: true, t: `${esc(p.name)} — к ${p.paid ? 'доплате' : 'оплате'} ${fmt(p.remainder)}`, s: p.subscription ? 'абонемент — только целиком' : `${plural(p.unpaidLessons, ['занятие', 'занятия', 'занятий'])} не оплачено`, go: p.subscription ? 'a.pay.sub' : 'a.pay.select', p: { ym, sid, key: p.key, name: p.name, pendingId: p.pending && p.pending.id, amount: p.remainder } }));
  });
  return { title: d.student.name, html: `<div class="hint" style="margin-bottom:10px">${fmon(ym)} — выберите счёт для подтверждения</div>${list(rows.length ? rows : [cell({ t: '✅ Всё оплачено' })])}<div class="total"><span>Итого ${fmt(total)} · оплачено ${fmt(paid)}</span><span>${rest ? `к оплате ${fmt(rest)}` : '✓'}</span></div><div style="margin-top:12px">${goBtn('🧾 Открыть счёт ученика', 'a.bill', { ym, sid }, 'sec')}</div>` };
};

SCREENS['a.pay.select'] = async ({ ym, sid, key }) => {
  const d = await api(`/pay/marks/${sid}?ym=${ym}&key=${encodeURIComponent(key)}`);
  const ui = state.ui.sel || (state.ui.sel = {}); const k = key + sid + ym;
  if (ui.key !== k) { ui.key = k; ui.picked = new Set(); }
  const chosen = d.marks.filter(m => !m.paid && ui.picked.has(m.lessonId)); const total = chosen.reduce((a, m) => a + m.amount, 0);
  return { title: d.ledger.name, html: `
    <div class="hint" style="margin-bottom:10px">${esc(d.student.name)} · ${fmon(ym)} · начислено ${fmt(d.ledger.accrued)}, оплачено ${fmt(d.ledger.paid)}. Отметьте занятия, за которые приняли деньги.</div>
    <div class="list">${d.marks.map(m => m.paid ? `<div class="lesson-line"><span class="mark paid">✓</span><span>${fdate(m.date)} · ${m.lessonType === 'group' ? 'групп.' : 'инд.'} · ${m.durationMin} мин<div class="d">оплачено</div></span><span class="amt">${fmt(m.amount)}</span></div>` : `<button class="lesson-line pick" data-act="pick" data-p='${esc(JSON.stringify({ id: m.lessonId }))}'><span class="mark ${ui.picked.has(m.lessonId) ? 'on' : ''}">${ui.picked.has(m.lessonId) ? '✓' : ''}</span><span>${fdate(m.date)} · ${m.lessonType === 'group' ? 'групп.' : 'инд.'} · ${m.durationMin} мин</span><span class="amt">${fmt(m.amount)}</span></button>`).join('')}</div>
    <div style="margin-top:12px">${btn(total ? `✅ Подтвердить оплату ${fmt(total)}` : 'Выберите занятия', 'confirmSel', { ym, sid, key, total, name: d.ledger.name, student: d.student.name }, total ? '' : 'sec')}</div>
    <p class="hint" style="margin-top:10px">Сумма зачитывается на самые ранние неоплаченные занятия этого педагога — как в боте.</p>` };
};

SCREENS['a.pay.sub'] = async ({ ym, sid, key, name, pendingId, amount }) => ({ title: 'Абонемент', html: `<div class="card pad"><div style="font-weight:700">${esc(name)}</div><div class="hint">${fmon(ym)}</div><div class="money" style="font-size:26px;font-weight:800;margin-top:8px">${fmt(amount)}</div></div><div style="margin-top:12px">${btn('✅ Подтвердить оплату абонемента', 'confirmInvoice', { pendingId, amount }, pendingId ? '' : 'sec')}</div>` });

SCREENS['a.bill'] = async ({ ym, sid }) => {
  const d = await api(`/bill/${sid}?ym=${ym}`);
  return { title: 'Счёт ученика', html: `
    <div class="card bill"><div class="pad" style="border-bottom:1px solid var(--line)"><div style="font-weight:800;font-size:16px">${esc(d.student.name)}</div><div class="hint">${d.groups.length ? 'Группы: ' + esc(d.groups.join(', ')) + '<br>' : ''}Месяц: ${fmon(ym)}</div></div>
    ${d.rows.length ? d.rows.map(r => `<div class="grp"><span>${r.subscription ? '💳' : r.group ? '👥' : '👨‍🏫'} ${esc(r.name)}</span><span class="money">${fmt(r.total)}</span></div>${r.subscription ? '<div class="lesson-line"><span></span><span class="hint">фиксированная сумма за месяц</span><span></span></div>' : r.items.map(m => `<div class="lesson-line"><span class="mark ${m.paid ? 'paid' : ''}">${m.paid ? '✓' : ''}</span><span>${fdate(m.date)} · ${m.durationMin} мин</span><span class="amt">${fmt(m.amount)}</span></div>`).join('')}`).join('') : '<div class="empty">Начислений за месяц нет</div>'}
    <div class="total"><span>Начислено ${fmt(d.total)}<br><span class="hint">оплачено ${fmt(d.paid)}</span></span><span class="big">${d.rest ? fmt(d.rest) : '✓'}</span></div></div>
    <div style="margin-top:12px">${btn('📨 Отправить родителям', 'sendBill', { ym, sid }, d.rows.length ? '' : 'sec')}${goBtn('💾 Подтвердить оплату', 'a.pay.student', { ym, sid }, 'ghost')}</div>` };
};

SCREENS['a.debtors'] = async () => {
  const d = await api('/debtors');
  const rows = d.debtors.filter(r => r.closedTotal || r.currentTotal);
  return { title: 'Должники', html: `<div class="hint" style="margin-bottom:10px">Долг = начислено − оплачено. Текущий месяц помечен * и в напоминание не входит.</div>${rows.length ? list(rows.map(r => cell({ lead: initials(r.name), t: esc(r.name), s: Object.entries(r.months).map(([ym, a]) => `${MON_NOM[+ym.slice(5) - 1]}${ym === d.period ? '*' : ''}: ${fmt(a)}`).join(' · ') + (r.hasParent ? '' : ' · родитель не привязан'), r: r.closedTotal ? pill(fmt(r.closedTotal), 'bad') : pill(fmt(r.currentTotal), 'warn'), go: 'a.student', p: { id: r.id } }))) : '<div class="empty">Должников нет</div>'}<p class="hint" style="margin-top:12px">Массовое напоминание родителям — пока из бота («⚠️ Должники» → «📤 Напомнить всем»).</p>` };
};

SCREENS['a.students'] = async () => {
  const q = state.ui.q || '';
  const d = await api(`/students?q=${encodeURIComponent(q)}`);
  return { title: 'Ученики', html: `<input class="search" id="q" placeholder="Поиск по фамилии" value="${esc(q)}" autocomplete="off">${d.students.length ? list(d.students.map(s => cell({ lead: initials(s.name), t: esc(s.name), s: esc(s.groups.join(', ')) || 'без группы', r: s.hasParent ? '' : pill('без родителя', 'warn'), go: 'a.student', p: { id: s.id } }))) : '<div class="empty">Никого не нашли</div>'}` };
};

SCREENS['a.student'] = async ({ id }) => {
  const s = await api(`/students/${id}`);
  const cur = s.months.find(m => m.period === lastPeriods(1)[0]) || { total: 0, paid: 0, rest: 0 };
  return { title: s.name, html: `
    <div class="card pad"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><div><div style="font-weight:800;font-size:17px">${esc(s.name)}</div><div class="hint">${s.client ? 'Родитель: ' + esc(s.client.name) : s.parents.length ? 'Родитель в ' + s.parents.map(p => p.platform === 'tg' ? 'Telegram' : 'MAX').join(', ') : 'Родитель не привязан'}${s.isAthlete ? ' · 🏃 спортсмен' : ''}${s.partner ? ' · пара с ' + esc(s.partner.name) : ''}</div></div>${s.debt ? pill(`долг ${fmt(s.debt)}`, 'bad') : cur.rest ? pill(`к оплате ${fmt(cur.rest)}`, 'warn') : cur.total ? pill('оплачено', 'ok') : ''}</div></div>
    <div class="eyebrow">Группы</div>${s.groups.length ? list(s.groups.map(g => cell({ lead: '💃', plain: true, t: esc(g.name), s: `${esc(g.branch)}${g.mode ? ' · ' + MODE[g.mode] : ''}` }))) : '<div class="empty">Не состоит в группах</div>'}
    ${s.teachers.length ? `<div class="eyebrow">Педагоги</div><div class="card pad">${esc(s.teachers.join(', '))}</div>` : ''}
    <div class="eyebrow">Счета</div>${list(s.months.map(m => cell({ lead: m.total ? (m.rest ? '⏳' : '✅') : '—', plain: true, t: fmon(m.period), s: m.total ? `начислено ${fmt(m.total)} · оплачено ${fmt(m.paid)}` : 'начислений нет', r: m.rest ? pill(fmt(m.rest), m.period < lastPeriods(1)[0] ? 'bad' : 'warn') : '', go: 'a.bill', p: { ym: m.period, sid: id } })))}
    <div style="margin-top:12px">${goBtn('💾 Подтвердить оплату', 'a.pay.student', { ym: lastPeriods(1)[0], sid: id }, 'sec')}</div>
    <p class="hint" style="margin-top:10px">Редактирование карточки (группы, партнёр, родитель, переименование) — следующий этап; пока из бота.</p>` };
};

SCREENS['a.teachers'] = async () => {
  const d = await api('/teachers');
  return { title: 'Педагоги', html: `${list(d.teachers.map(t => cell({ lead: t.submittedPrev || t.isOwner ? '🟢' : '🔴', plain: true, t: esc(t.name), s: esc(t.groups.join(', ')) || 'групп нет', r: t.isOwner ? pill('👑 руководитель', 'warn') : t.directPay ? pill('прямая оплата', 'acc') : '', go: 'a.teacher', p: { id: t.id } })))}<p class="hint" style="margin-top:8px">🟢/🔴 — сдан ли ${MON_NOM[+d.prevPeriod.slice(5) - 1].toLowerCase()}</p>` };
};

SCREENS['a.teacher'] = async ({ id }) => {
  const t = await api(`/teachers/${id}`);
  return { title: t.name, html: `
    <div class="kpis">${kpi(fmt(t.rates.group), 'ставка — группа / 45 мин')}${kpi(fmt(t.rates.teacher), 'ставка — инд. / 45 мин')}${kpi(fmt(t.rates.student), 'цена для ученика / 45 мин')}${kpi(fmt(t.salary), `начислено за ${MON_NOM[+t.period.slice(5) - 1].toLowerCase()}`)}</div>
    ${t.isOwner ? '<div class="card pad" style="margin-top:10px;background:var(--warn-soft);border-color:var(--warn-soft)">👑 Руководитель: зарплата остаётся в прибыли</div>' : ''}
    <div class="eyebrow">Группы</div>${t.groups.length ? list(t.groups.map(g => cell({ lead: '💃', plain: true, t: esc(g.name) }))) : '<div class="empty">Групп нет</div>'}
    <div class="eyebrow">Сданные периоды</div>${t.submitted.length ? list(t.submitted.map(ym => cell({ lead: '🔒', plain: true, t: fmon(ym), s: 'сдан — занятия заморожены' }))) : '<div class="empty">Ещё ничего не сдано</div>'}
    <p class="hint" style="margin-top:10px">Ставки, группы и открытие периода — следующий этап; пока из бота.</p>` };
};

SCREENS['a.more'] = async () => ({ title: 'Ещё', html: list([cell({ lead: '📊', plain: true, t: 'Прибыль', s: 'скоро — пока в боте' }), cell({ lead: '💰', plain: true, t: 'Зарплаты и выплаты', s: 'скоро — пока в боте' }), cell({ lead: '🏢', plain: true, t: 'Филиалы и группы', s: 'скоро — пока в боте' }), cell({ lead: '✏️', plain: true, t: 'Изменить занятия', s: 'скоро — пока в боте' })]) + `<p class="hint" style="margin-top:12px">Версия кабинета: этап 1 — сводка, оплаты, счета, должники, ученики, педагоги.${state.me ? ` Вы вошли как администратор (id ${state.me.tgId}).` : ''}</p>` });

/* ── действия ────────────────────────────────────────────────────────── */
const ACT = {
  closeSheet: () => closeSheet(),
  pick: ({ id }) => { const s = state.ui.sel.picked; s.has(id) ? s.delete(id) : s.add(id); render(); },
  confirmSel: ({ ym, sid, key, total, name, student }) => { if (!total) return; state.ui.method = state.ui.method || 'cash'; sheet(`<h3>Подтвердить оплату?</h3><div class="hint">${esc(student)} · ${esc(name)} · ${fmon(ym)}</div><div class="money" style="font-size:26px;font-weight:800;margin:10px 0">${fmt(total)}</div><div class="chips">${[['cash', 'Наличные'], ['receipt_bank', 'По реквизитам'], ['receipt_sbp', 'СБП']].map(([k, n]) => `<button class="chip" aria-pressed="${state.ui.method === k}" data-act="method" data-p='{"k":"${k}"}'>${n}</button>`).join('')}</div>${btn('✅ Подтвердить', 'doConfirm', { ym, sid, key, amount: total })}${btn('Отмена', 'closeSheet', {}, 'ghost')}`); },
  method: ({ k }) => { state.ui.method = k; document.querySelectorAll('.sheet .chip').forEach(c => c.setAttribute('aria-pressed', String(JSON.parse(c.dataset.p).k === k))); },
  doConfirm: async ({ ym, sid, key, amount }) => {
    try { const r = await api('/pay/confirm', { method: 'POST', body: { studentId: sid, periodMonth: ym, key, amount, method: state.ui.method || 'cash' } }); closeSheet(); state.ui.sel = null; back(); toast(`Оплата ${fmt(r.credited)} зачтена`); }
    catch (e) { toast(errText(e)); }
  },
  confirmInvoice: async ({ pendingId, amount }) => {
    if (!pendingId) return;
    try { const r = await api('/pay/confirm-invoice', { method: 'POST', body: { paymentId: pendingId, method: 'cash' } }); if (r.ok) { back(); toast(`Абонемент ${fmt(amount)} оплачен`); } else toast('Счёт уже оплачен или не найден'); }
    catch (e) { toast(errText(e)); }
  },
  sendBill: async ({ ym, sid }) => {
    try { const r = await api(`/bill/${sid}/send?ym=${ym}`, { method: 'POST' }); toast(r.recipients ? `Счёт отправлен: ${r.sentTo} из ${r.recipients}` : 'У ученика нет привязанных родителей'); }
    catch (e) { toast(errText(e)); }
  },
};

/* ── рендер ─────────────────────────────────────────────────────────── */
async function render() {
  const seq = ++renderSeq; const s = cur();
  const content = document.getElementById('content');
  const rootN = state.stack[0].n;
  document.getElementById('tabs').innerHTML = TABS.map(([n, label, ic]) => `<button role="tab" aria-selected="${rootN === n}" data-root="${n}"><svg viewBox="0 0 24 24">${ICON[ic]}</svg>${label}</button>`).join('');
  const deep = state.stack.length > 1;
  document.getElementById('back').classList.toggle('on', deep && !tg);
  if (tg) { try { deep ? tg.BackButton.show() : tg.BackButton.hide(); } catch (_) { /* нет BackButton */ } }
  content.innerHTML = skeleton();
  let scr;
  try { scr = await SCREENS[s.n](s.p || {}); }
  catch (e) { scr = { title: 'Ошибка', html: `<div class="card pad"><div style="font-weight:700">${esc(errText(e))}</div></div><div style="margin-top:12px">${btn('Повторить', 'retry', {}, 'sec')}</div>` }; }
  if (seq !== renderSeq) return;
  document.getElementById('title').innerHTML = `${esc(scr.title)}<span class="sub">Администратор</span>`;
  content.innerHTML = `<div class="fade">${scr.html}</div>`; content.scrollTop = 0;
  const q = document.getElementById('q');
  if (q) { let t; q.addEventListener('input', e => { state.ui.q = e.target.value; clearTimeout(t); t = setTimeout(() => { const pos = e.target.selectionStart; render().then(() => { const nq = document.getElementById('q'); if (nq) { nq.focus(); nq.setSelectionRange(pos, pos); } }); }, 250); }); }
}
ACT.retry = () => render();

document.addEventListener('click', e => {
  const stop = e.target.closest('[data-stop]'); const wrap = e.target.closest('.sheet-wrap');
  if (wrap && !stop) { closeSheet(); return; }
  const el = e.target.closest('[data-go],[data-act],[data-root]'); if (!el) return;
  if (el.dataset.root) { closeSheet(); root(el.dataset.root); return; }
  const p = el.dataset.p ? JSON.parse(el.dataset.p) : {};
  if (el.dataset.go) { go(el.dataset.go, p); return; }
  if (el.dataset.act && ACT[el.dataset.act]) ACT[el.dataset.act](p);
});
document.getElementById('back').addEventListener('click', () => { closeSheet(); back(); });
if (tg) { try { tg.BackButton.onClick(() => { closeSheet(); back(); }); } catch (_) { /* нет BackButton */ } }

(async () => {
  try { state.me = await api('/me'); render(); }
  catch (e) {
    document.getElementById('tabs').innerHTML = '';
    document.getElementById('content').innerHTML = `<div class="card pad" style="margin-top:20px"><div style="font-weight:800;font-size:16px">Нет доступа</div><div class="hint" style="margin-top:6px">${esc(errText(e))}</div></div>`;
  }
})();
