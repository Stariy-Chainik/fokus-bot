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
/* Роль задаёт и префикс API, и набор вкладок: кабинет администратора (a.*) или педагога (t.*). */
let ROLE = 'admin';
const API_BASE = { admin: '/api/admin', teacher: '/api/teacher' };
const ROLE_TITLE = { admin: 'Администратор', teacher: 'Педагог' };
const ROLE_HOME = { admin: 'a.home', teacher: 't.home' };
class ApiError extends Error { constructor(status, code) { super(code || `HTTP ${status}`); this.status = status; this.code = code; } }
async function api(path, { method = 'GET', body } = {}) {
  const headers = { 'Accept': 'application/json' };
  if (tg && tg.initData) headers.Authorization = `tma ${tg.initData}`;
  else if (DEV) headers.Authorization = 'dev';
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const resp = await fetch(API_BASE[ROLE] + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
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
const cell = ({ lead, t, s, r, go, p, act, plain, cls = '' }) => `<button class="cell ${lead === undefined ? 'nolead' : ''} ${go || act ? '' : 'static'} ${cls}" ${go ? attr(go, p) : act ? `data-act="${act}" data-p='${esc(JSON.stringify(p || {}))}'` : ''}>${lead !== undefined ? `<span class="lead ${plain ? 'plain' : ''}">${lead}</span>` : ''}<span><div class="t">${t}</div>${s ? `<div class="s">${s}</div>` : ''}</span><span class="r">${r || ''}${go ? '<span class="chev">›</span>' : ''}</span></button>`;
const list = rows => `<div class="list">${rows.join('')}</div>`;
const pill = (txt, kind = 'mute') => `<span class="pill ${kind}">${txt}</span>`;
const btn = (txt, act, p = {}, kind = '') => `<button class="btn ${kind}" data-act="${act}" data-p='${esc(JSON.stringify(p))}'>${txt}</button>`;
const goBtn = (txt, go, p = {}, kind = '') => `<button class="btn ${kind}" ${attr(go, p)}>${txt}</button>`;
const kpi = (v, l, kind = '', go, p) => go ? `<button class="kpi ${kind}" ${attr(go, p)}><div class="v">${v}</div><div class="l">${l} ›</div></button>` : `<div class="kpi ${kind}"><div class="v">${v}</div><div class="l">${l}</div></div>`;
const restPill = x => x.total === 0 ? pill('нет начислений') : x.rest === 0 ? pill('✓ оплачено', 'ok') : x.paid ? pill(`к доплате ${fmt(x.rest)}`, 'warn') : pill(`к оплате ${fmt(x.rest)}`, 'warn');
const skeleton = () => '<div class="skeleton w60"></div><div class="skeleton tall"></div><div class="skeleton"></div><div class="skeleton tall"></div>';

/* ── навигация ───────────────────────────────────────────────────────── */
const TABS = {
  admin: [['a.home', 'Сводка', 'home'], ['a.payhub', 'Оплаты', 'card'], ['a.students', 'Ученики', 'users'], ['a.school', 'Школа', 'teacher'], ['a.finance', 'Финансы', 'chart']],
  teacher: [['t.home', 'Сводка', 'home'], ['t.lessons', 'Занятия', 'card'], ['t.groups', 'Группы', 'users'], ['t.diary', 'Дневник', 'book'], ['t.money', 'Зарплата', 'chart']],
};
const ICON = {
  home: '<path d="M3 11 12 4l9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>', card: '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18M7 15h4"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M21.5 20a6.5 6.5 0 0 0-5-6.3"/>', chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  teacher: '<circle cx="12" cy="7" r="3.5"/><path d="M5 21a7 7 0 0 1 14 0M3 3h4M17 3h4"/>',
  book: '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M8 7h7M8 11h7"/>',
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
    <div class="kpis">${kpi(fmt(h.pendingTotal), `ожидает оплаты за ${MON_NOM[+h.period.slice(5) - 1].toLowerCase()}`, 'warn', 'a.pay.students', { ym: h.period, g: '', gname: 'Все ученики' })}${kpi(h.debtorsCount, `должников за ${MON_NOM[+h.prevPeriod.slice(5) - 1].toLowerCase()} и раньше`, h.debtorsCount ? 'bad' : 'ok', 'a.debtors')}${kpi(plural(h.lessonsToday, ['занятие', 'занятия', 'занятий']), 'отмечено сегодня', '', 'a.lessons.day', { date: h.today })}${kpi(h.studentsCount, 'учеников', '', 'a.students')}</div>
    <div class="eyebrow">Быстрые действия</div>
    ${list([cell({ lead: '💾', plain: true, t: 'Подтвердить оплату', s: 'ученик → педагог → занятия', go: 'a.pay' }), cell({ lead: '⚠️', plain: true, t: 'Должники', s: 'закрытые месяцы', go: 'a.debtors' }), cell({ lead: '🧾', plain: true, t: 'Счёт ученика', s: 'просмотр и отправка родителям', go: 'a.pay', p: { bill: true } }), cell({ lead: '📝', plain: true, t: 'Отметить занятие за педагога', s: 'мастер как в боте', go: 'a.record' })])}
    ${state.me && state.me.teacherId ? `<div style="margin-top:14px">${btn('🎓 Режим педагога', 'switchRole', { to: 'teacher' }, 'ghost')}</div>` : ''}` };
};

SCREENS['a.payhub'] = async () => ({ title: 'Оплаты', html: `<div class="eyebrow">Принять оплату</div>${list([cell({ lead: '💾', plain: true, t: 'Подтвердить оплату', s: 'ученик → педагог → занятия', go: 'a.pay' }), cell({ lead: '🧾', plain: true, t: 'Счёт ученика за период', s: 'просмотр и отправка родителям', go: 'a.pay', p: { bill: true } })])}<div class="eyebrow">Контроль</div>${list([cell({ lead: '⚠️', plain: true, t: 'Должники', s: 'сводный долг по месяцам, напоминание', go: 'a.debtors' }), cell({ lead: '📜', plain: true, t: 'История оплат', s: 'по фамилии → месяцы → оплаты', go: 'a.payhist.search' })])}` });

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
  const rows = d.debtors.filter(r => r.closedTotal || r.currentTotal); const targets = rows.filter(r => r.closedTotal && r.hasParent);
  return { title: 'Должники', html: `<div class="hint" style="margin-bottom:10px">Долг = начислено − оплачено. Текущий месяц помечен * и в напоминание не входит.</div>${rows.length ? list(rows.map(r => cell({ lead: initials(r.name), t: esc(r.name), s: Object.entries(r.months).map(([ym, a]) => `${MON_NOM[+ym.slice(5) - 1]}${ym === d.period ? '*' : ''}: ${fmt(a)}`).join(' · ') + (r.hasParent ? '' : ' · родитель не привязан'), r: r.closedTotal ? pill(fmt(r.closedTotal), 'bad') : pill(fmt(r.currentTotal), 'warn'), go: 'a.student', p: { id: r.id } }))) : '<div class="empty">Должников нет</div>'}<div style="margin-top:12px">${btn(`📤 Напомнить всем (${targets.length})`, 'remind', { n: targets.length, total: targets.reduce((a, r) => a + r.closedTotal, 0) }, targets.length ? '' : 'sec')}</div>` };
};

SCREENS['a.students'] = async () => {
  const f = state.ui.sf || (state.ui.sf = { group: '', noparent: false, debt: false });
  const q = state.ui.q || '';
  if (!state.ui.groupsCache) state.ui.groupsCache = (await api('/pay/groups')).branches;
  const qs = `q=${encodeURIComponent(q)}&group=${encodeURIComponent(f.group)}${f.noparent ? '&noparent=1' : ''}${f.debt ? '&debt=1' : ''}`;
  const d = await api(`/students?${qs}`);
  const filtered = f.group || f.noparent || f.debt || q;
  return { title: 'Ученики', html: `
    <input class="search" id="q" placeholder="Поиск по фамилии" value="${esc(q)}" autocomplete="off">
    <select class="search" id="sf-group" data-act="sfGroup"><option value="">Все группы</option>${state.ui.groupsCache.map(b => `<optgroup label="${esc(b.name)}">${b.groups.map(g => `<option value="${g.id}" ${f.group === g.id ? 'selected' : ''}>${esc(g.name)}</option>`).join('')}</optgroup>`).join('')}</select>
    <div class="chips"><button class="chip" aria-pressed="${f.noparent}" data-act="sfToggle" data-p='{"k":"noparent"}'>Без родителя</button><button class="chip" aria-pressed="${f.debt}" data-act="sfToggle" data-p='{"k":"debt"}'>С долгом</button>${filtered ? `<button class="chip" data-act="sfReset" data-p='{}'>✕ Сбросить</button>` : ''}</div>
    <p class="hint" style="margin:-4px 0 10px">${filtered ? `Показано ${d.students.length} из ${d.total}` : plural(d.total, ['ученик', 'ученика', 'учеников'])}</p>
    ${d.students.length ? list(d.students.map(s => cell({ lead: initials(s.name), t: esc(s.name), s: esc(s.groups.join(', ')) || 'без группы', r: s.hasParent ? '' : pill('без родителя', 'warn'), go: 'a.student', p: { id: s.id } }))) : '<div class="empty">Никого не нашли</div>'}<div style="margin-top:12px">${goBtn('➕ Добавить ученика', 'a.student.add', {}, 'sec')}</div>` };
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
    <div class="eyebrow">Карточка</div>${list([cell({ lead: '💃', plain: true, t: 'Группы ученика', s: 'добавить / убрать', go: 'a.student.groups', p: { id, name: s.name } }), cell({ lead: '👫', plain: true, t: s.partner ? `Партнёр: ${esc(s.partner.name)}` : 'Партнёр не назначен', s: 'сменить / убрать', go: 'a.partner', p: { id, name: s.name } }), cell({ lead: '👨‍👩‍👧', plain: true, t: s.client ? 'Сменить родителя' : 'Привязать родителя', s: s.client ? esc(s.client.name) + (s.client.phone ? ' · ' + esc(s.client.phone) : '') : 'карточка клиента: имя и телефон', go: 'a.parent.link', p: { id, name: s.name } })].concat(s.groups.some(g => g.mode === 'per_visit') ? [`<button class="cell" data-act="tierToggle" data-p='${esc(JSON.stringify({ id }))}'><span class="lead plain">⏱</span><span><div class="t">Тариф: ${s.tier === 'short' ? 'короткое занятие' : 'полное занятие'}</div><div class="s">переключить — только детсадовские группы</div></span><span class="r"><span class="chev">›</span></span></button>`] : []).concat(s.isAthlete ? [`<button class="cell" data-act="athleteUnlink" data-p='${esc(JSON.stringify({ id }))}'><span class="lead plain">🏃</span><span><div class="t">Кабинет спортсмена привязан</div><div class="s">отвязать Telegram ученика</div></span><span class="r"><span class="chev">›</span></span></button>`] : []))}
    <div style="margin-top:12px">${btn('✏️ Переименовать', 'studentRename', { id, name: s.name }, 'ghost')}${btn('🗑 Удалить ученика', 'studentDelete', { id, name: s.name }, 'danger')}</div>` };
};

SCREENS['a.teachers'] = async () => {
  const d = await api('/teachers');
  return { title: 'Педагоги', html: `${list(d.teachers.map(t => cell({ lead: t.submittedPrev || t.isOwner ? '🟢' : '🔴', plain: true, t: esc(t.name), s: esc(t.groups.join(', ')) || 'групп нет', r: t.isOwner ? pill('👑 руководитель', 'warn') : t.directPay ? pill('прямая оплата', 'acc') : '', go: 'a.teacher', p: { id: t.id } })))}<p class="hint" style="margin-top:8px">🟢/🔴 — сдан ли ${MON_NOM[+d.prevPeriod.slice(5) - 1].toLowerCase()}</p><div style="margin-top:8px">${goBtn('➕ Добавить педагога', 'a.teacher.add', {}, 'sec')}</div>` };
};

SCREENS['a.teacher'] = async ({ id }) => {
  const t = await api(`/teachers/${id}`);
  return { title: t.name, html: `
    <div class="kpis">${kpi(fmt(t.rates.group), 'ставка — группа / 45 мин')}${kpi(fmt(t.rates.teacher), 'ставка — инд. / 45 мин')}${kpi(fmt(t.rates.student), 'цена для ученика / 45 мин')}${kpi(fmt(t.salary), `начислено за ${MON_NOM[+t.period.slice(5) - 1].toLowerCase()}`)}</div>
    ${t.isOwner ? '<div class="card pad" style="margin-top:10px;background:var(--warn-soft);border-color:var(--warn-soft)">👑 Руководитель: зарплата остаётся в прибыли</div>' : ''}
    <div class="eyebrow">Группы</div>${t.groups.length ? list(t.groups.map(g => cell({ lead: '💃', plain: true, t: esc(g.name) }))) : '<div class="empty">Групп нет</div>'}
    <div class="eyebrow">Сданные периоды</div>${t.submitted.length ? list(t.submitted.map(ym => `<div class="cell static"><span class="lead plain">🔒</span><span><div class="t">${fmon(ym)}</div><div class="s">сдан — занятия заморожены</div></span><span class="r"><button class="chip" style="padding:2px 8px" data-act="openPeriod" data-p='${esc(JSON.stringify({ id, ym }))}'>открыть</button></span></div>`)) : '<div class="empty">Ещё ничего не сдано</div>'}
    <div style="margin-top:12px">${goBtn('📝 Отметить занятие за педагога', 'a.record.w', { tid: id, name: t.name })}${btn('✏️ Изменить ставки', 'ratesForm', { id, rates: t.rates }, 'sec')}${goBtn('💃 Группы педагога', 'a.teacher.groups', { id, name: t.name }, 'ghost')}${btn('🗑 Удалить педагога', 'teacherDelete', { id, name: t.name }, 'danger')}</div>` };
};

SCREENS['a.finance'] = async () => ({ title: 'Финансы', html: `<div class="eyebrow">Школа</div>${list([cell({ lead: '📊', plain: true, t: 'Прибыль', s: 'месяц или день; доходы и расходы', go: 'a.profit', p: {} })])}<div class="eyebrow">Педагоги</div>${list([cell({ lead: '💰', plain: true, t: 'Зарплаты', s: 'начислено педагогам, строки', go: 'a.salaries', p: {} }), cell({ lead: '💸', plain: true, t: 'Выплатить зарплату', s: 'остаток, аванс, нестандартный день', go: 'a.payouts', p: {} })])}<p class="hint" style="margin-top:12px">${state.me ? `Вы вошли как администратор (id ${state.me.tgId}).` : ''}</p>` });

/* ── действия ────────────────────────────────────────────────────────── */
const ACT = {
  closeSheet: () => closeSheet(),
  pick: ({ id }) => { const s = state.ui.sel.picked; s.has(id) ? s.delete(id) : s.add(id); render(); },
  confirmSel: ({ ym, sid, key, total, name, student }) => { if (!total) return; state.ui.method = state.ui.method || 'cash'; sheet(`<h3>Подтвердить оплату?</h3><div class="hint">${esc(student)} · ${esc(name)} · ${fmon(ym)}</div><div class="money" style="font-size:26px;font-weight:800;margin:10px 0">${fmt(total)}</div><div class="chips">${[['cash', 'Наличные'], ['receipt_bank', 'По реквизитам'], ['receipt_sbp', 'СБП']].map(([k, n]) => `<button class="chip" aria-pressed="${state.ui.method === k}" data-act="method" data-p='{"k":"${k}"}'>${n}</button>`).join('')}</div>${btn('✅ Подтвердить', 'doConfirm', { ym, sid, key, amount: total })}${btn('Отмена', 'closeSheet', {}, 'ghost')}`); },
  method: ({ k }) => { state.ui.method = k; document.querySelectorAll('.sheet .chip').forEach(c => c.setAttribute('aria-pressed', String(JSON.parse(c.dataset.p).k === k))); },
  doConfirm: async ({ ym, sid, key, amount }) => {
    try { const r = await api('/pay/confirm', { method: 'POST', body: { studentId: sid, periodMonth: ym, key, amount, method: state.ui.method || 'cash', lessonIds: [...((state.ui.sel && state.ui.sel.picked) || [])] } }); closeSheet(); state.ui.sel = null; back(); toast(`Оплата ${fmt(r.credited)} зачтена`); }
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

/* ── этап 2: финансы, история оплат, напоминание должникам ─────────────── */
const field = (id, label, value = '', attrs = '') => `<label class="hint" for="${id}" style="display:block;margin:10px 0 4px">${label}</label><input class="search" style="margin:0" id="${id}" value="${esc(value)}" ${attrs}>`;
const monthChips = (go, cur, p) => `<div class="chips">${lastPeriods(3).map(m => `<button class="chip" aria-pressed="${m === cur}" data-go="${go}" data-p='${esc(JSON.stringify({ ...p, ym: m }))}' data-replace="1">${MON_NOM[+m.slice(5) - 1]}</button>`).join('')}</div>`;
const profitRow = (r, period) => `<button class="cell" data-go="a.profit.teacher" data-p='${esc(JSON.stringify({ period, tid: r.teacherId }))}'><span class="lead" style="${r.owner ? 'background:var(--warn-soft);color:var(--warn)' : ''}">${r.owner ? '👑' : initials(r.name)}</span><span><div class="t">${esc(r.name)}</div><div class="s">${r.groupLessons ? `👥 ${r.groupLessons}` : ''} ${r.individualLessons ? `👤 ${r.individualLessons}` : ''} · выручка ${fmt(r.income)} · зарплата ${fmt(r.salary)}${r.rent ? `<br>🏟 в т.ч. аренда зала ${fmt(r.rent)} (${r.rentLessons} зан.)` : ''}${r.owner ? `<br>👑 руководитель: ${fmt(r.ownerIncome)} остаются в прибыли` : ''}</div></span><span class="r"><b>${fmt(r.profit)}</b><br>${r.margin}%<span class="chev">›</span></span></button>`;
const totalsCard = t => `<div class="card" style="margin-top:14px"><div class="pad money" style="display:grid;gap:6px"><div style="display:flex;justify-content:space-between"><span>Выручка</span><b>${fmt(t.totalIncome)}</b></div>${t.rentIncome ? `<div style="display:flex;justify-content:space-between" class="hint"><span>в т.ч. аренда зала</span><span>${fmt(t.rentIncome)} (${t.rentLessons} зан.)</span></div>` : ''}<div style="display:flex;justify-content:space-between"><span>Зарплата</span><b>${fmt(t.salary)}</b></div>${t.ownerIncome ? `<div style="display:flex;justify-content:space-between" class="hint"><span>👑 руководитель в прибыли</span><span>${fmt(t.ownerIncome)}</span></div>` : ''}${t.manualExpenses ? `<div style="display:flex;justify-content:space-between"><span>Расходы</span><b>${fmt(t.manualExpenses)}</b></div>` : ''}</div><div class="total"><span>Прибыль</span><span class="big" style="color:var(--ok)">${fmt(t.profit)}</span></div></div>`;

SCREENS['a.profit'] = async ({ ym }) => {
  ym = ym || lastPeriods(1)[0];
  const p = await api(`/profit?ym=${ym}`);
  return { title: `Прибыль за ${fmon(ym)}`, html: `
    ${monthChips('a.profit', ym, {})}<div class="chips" style="margin-top:-6px"><button class="chip" data-go="a.profit.day" data-p='{}'>📅 За день…</button></div>
    ${p.totals.isEmpty ? '<div class="empty">Занятий нет</div>' : `
    <div class="kpis">${kpi(fmt(p.totals.totalIncome), 'выручка', '', 'a.profit.income', { ym })}${kpi(fmt(p.totals.profit), 'прибыль', 'ok', 'a.profit.profit', { ym })}</div>
    <div class="eyebrow">Педагоги</div><div class="list">${p.rows.map(r => profitRow(r, ym)).join('')}</div>
    ${p.subscriptions.length ? `<div class="eyebrow">Абонементы</div>${list(p.subscriptions.map(x => cell({ t: esc(x.groupName), s: plural(x.students, ['ученик', 'ученика', 'учеников']), r: `<b>${fmt(x.income)}</b>`, go: 'a.profit.sub', p: { gid: x.groupId, ym } })))}` : ''}`}
    <div class="eyebrow">Прочие доходы и расходы</div>${list(p.finance.map(f => `<div class="cell static"><span class="lead plain">${f.kind === 'income' ? '🏆' : '📉'}</span><span><div class="t">${esc(f.title)}</div></span><span class="r"><b style="color:${f.kind === 'income' ? 'var(--ok)' : 'var(--bad)'}">${f.kind === 'income' ? '+' : '−'}${fmt(f.amount)}</b> <button class="chip" style="padding:2px 8px;margin-left:6px" data-act="delFin" data-p='${esc(JSON.stringify({ id: f.id }))}' aria-label="Удалить">🗑</button></span></div>`).concat([`<div class="cell static"><span><div class="chips" style="margin:0"><button class="chip" data-act="finForm" data-p='${esc(JSON.stringify({ ym, kind: 'income' }))}'>➕ Доход</button><button class="chip" data-act="finForm" data-p='${esc(JSON.stringify({ ym, kind: 'expense' }))}'>➕ Расход</button></div></span><span></span></div>`]))}
    ${p.totals.isEmpty ? '' : totalsCard(p.totals)}` };
};
const moneyRow = (label, v, cls = '', sub = false) => `<div style="display:flex;justify-content:space-between;gap:8px${sub ? ';padding-left:14px' : ''}" class="${sub ? 'hint' : ''}"><span>${label}</span><b class="money" style="color:${cls || 'inherit'}">${v}</b></div>`;
const teacherIncomeCell = (r, ym) => cell({ lead: r.owner ? '👑' : initials(r.name), t: esc(r.name), s: `${r.groupLessons ? `👥 ${r.groupLessons} ` : ''}${r.individualLessons ? `👤 ${r.individualLessons}` : ''}${r.rent ? ` · 🏟 аренда зала ${fmt(r.rent)}` : ''}`, r: `<b>${fmt(r.income)}</b>`, go: 'a.profit.teacher', p: { period: ym, tid: r.teacherId } });
const dayCells = (days, sub) => days.length ? list(days.map(d => cell({ lead: String(+d.date.slice(8)), t: fdate(d.date), s: sub(d), r: `<b>${fmt(sub === dayIncomeSub ? d.income : d.profit)}</b>`, go: 'a.profit.day', p: { date: d.date } }))) : '<div class="empty">Занятий нет</div>';
const dayIncomeSub = d => plural(d.lessons, ['занятие', 'занятия', 'занятий']) + (d.rent ? ` · аренда ${fmt(d.rent)}` : '');
const dayProfitSub = d => `выручка ${fmt(d.income)} − зарплата ${fmt(d.salary)}`;
SCREENS['a.profit.income'] = async ({ ym }) => {
  const b = await api(`/profit/breakdown?ym=${ym}`); const t = b.totals;
  return { title: `Выручка за ${fmon(ym)}`, html: `
    <div class="card pad money" style="display:grid;gap:6px"><div style="font-size:24px;font-weight:800;letter-spacing:-.02em">${fmt(t.totalIncome)}</div>
      ${moneyRow('Занятия (оплата по педагогам)', fmt(t.lessonIncome))}${t.rentIncome ? moneyRow(`в т.ч. аренда зала, ${plural(t.rentLessons, ['занятие', 'занятия', 'занятий'])}`, fmt(t.rentIncome), '', true) : ''}
      ${moneyRow('Абонементы', fmt(t.subscriptionIncome))}${moneyRow('Прочие доходы', fmt(t.manualIncome))}</div>
    <div class="eyebrow">Занятия по педагогам</div>${b.rows.length ? list(b.rows.map(r => teacherIncomeCell(r, ym))) : '<div class="empty">Занятий нет</div>'}
    ${b.subscriptions.length ? `<div class="eyebrow">Абонементы</div>${list(b.subscriptions.map(x => cell({ t: esc(x.groupName), s: plural(x.students, ['ученик', 'ученика', 'учеников']), r: `<b>${fmt(x.income)}</b>`, go: 'a.profit.sub', p: { gid: x.groupId, ym } })))}` : ''}
    ${b.finance.some(f => f.kind === 'income') ? `<div class="eyebrow">Прочие доходы</div>${list(b.finance.filter(f => f.kind === 'income').map(f => cell({ lead: '🏆', plain: true, t: esc(f.title), r: `<b>${fmt(f.amount)}</b>` })))}` : ''}
    <div class="eyebrow">Занятия по дням</div>${dayCells(b.days, dayIncomeSub)}
    <p class="hint" style="margin-top:8px">Абонементы и прочие доходы относятся к месяцу целиком и по дням не делятся.</p>` };
};
SCREENS['a.profit.profit'] = async ({ ym }) => {
  const b = await api(`/profit/breakdown?ym=${ym}`); const t = b.totals;
  return { title: `Прибыль за ${fmon(ym)}`, html: `
    <div class="card pad money" style="display:grid;gap:6px"><div style="font-size:24px;font-weight:800;letter-spacing:-.02em;color:var(--ok)">${fmt(t.profit)}</div>
      ${moneyRow('Выручка', fmt(t.totalIncome))}${moneyRow('занятия', fmt(t.lessonIncome), '', true)}${moneyRow('абонементы', fmt(t.subscriptionIncome), '', true)}${moneyRow('прочие доходы', fmt(t.manualIncome), '', true)}
      ${moneyRow('− Зарплата педагогов', fmt(t.salary), 'var(--bad)')}${t.ownerIncome ? moneyRow('👑 руководитель остаётся в прибыли', fmt(t.ownerIncome), '', true) : ''}
      ${moneyRow('− Расходы', fmt(t.manualExpenses), 'var(--bad)')}</div>
    <div class="eyebrow">Зарплата по педагогам</div>${b.rows.length ? list(b.rows.map(r => cell({ lead: r.owner ? '👑' : initials(r.name), t: esc(r.name), s: `выручка ${fmt(r.income)} → прибыль ${fmt(r.profit)}`, r: r.owner ? pill('в прибыли', 'warn') : `<b style="color:var(--bad)">− ${fmt(r.salary)}</b>`, go: 'a.profit.teacher', p: { period: ym, tid: r.teacherId } }))) : '<div class="empty">Занятий нет</div>'}
    ${b.finance.some(f => f.kind === 'expense') ? `<div class="eyebrow">Расходы</div>${list(b.finance.filter(f => f.kind === 'expense').map(f => cell({ lead: '📉', plain: true, t: esc(f.title), r: `<b style="color:var(--bad)">− ${fmt(f.amount)}</b>` })))}` : ''}
    <div class="eyebrow">Прибыль по дням</div>${dayCells(b.days, dayProfitSub)}
    <p class="hint" style="margin-top:8px">По дням учтены только занятия. Абонементы, прочие доходы и расходы добавляются к месяцу целиком.</p>` };
};
SCREENS['a.profit.day'] = async ({ date }) => {
  const days = []; for (let i = 0; i < 10; i++) { const x = new Date(); x.setDate(x.getDate() - i); days.push(x.toISOString().slice(0, 10)); }
  const d = date || days[0]; if (!days.includes(d)) days.unshift(d);
  const p = await api(`/profit/day?date=${d}`);
  return { title: 'Прибыль за день', html: `<div class="chips">${days.map(x => `<button class="chip" aria-pressed="${x === d}" data-go="a.profit.day" data-p='${esc(JSON.stringify({ date: x }))}' data-replace="1">${+x.slice(8)} ${MON_SHORT[+x.slice(5, 7) - 1]}</button>`).join('')}</div><div class="h2">${fdate(d)}</div>${p.rows.length ? `<div class="list">${p.rows.map(r => profitRow(r, d)).join('')}</div>${totalsCard(p.totals)}<p class="hint" style="margin-top:8px">За день — только занятия; абонементы и ручные записи считаются по месяцу.</p>` : '<div class="empty">Тарифицируемых занятий нет</div>'}` };
};
SCREENS['a.profit.sub'] = async ({ gid, ym }) => {
  const d = await api(`/profit/subscription/${gid}?ym=${ym}`);
  const st = s => s.status === 'paid' ? pill('✅ оплачено', 'ok') : s.status === 'partial' ? pill(`⏳ доплата ${fmt(s.remainder)}`, 'warn') : s.status === 'unpaid' ? pill('⬜ не оплачено', 'bad') : pill('освобождён', 'mute');
  const rest = Math.max(d.accrued - d.paid, 0);
  return { title: d.groupName, html: `<div class="card pad"><div style="font-weight:800;font-size:16px">${esc(d.groupName)}</div><div class="hint">${MON_NOM[+ym.slice(5) - 1]} · абонемент ${fmt(d.price)} в месяц</div></div>
    <div class="eyebrow">Состав · оплачено</div>${d.students.length ? list(d.students.map(s => cell({ lead: initials(s.name), t: esc(s.name) + (s.active ? '' : ' <span class="hint">· вне группы</span>'), s: s.accrued ? `начислено ${fmt(s.accrued)}` : 'без начисления', r: `<b>${fmt(s.paid)}</b><br>${st(s)}`, go: 'a.student', p: { id: s.studentId } }))) : '<div class="empty">В группе никого нет</div>'}
    <div class="card" style="margin-top:10px"><div class="pad money" style="display:grid;gap:6px"><div style="display:flex;justify-content:space-between"><span>Начислено</span><b>${fmt(d.accrued)}</b></div><div style="display:flex;justify-content:space-between"><span>Оплачено</span><b style="color:var(--ok)">${fmt(d.paid)}</b></div><div style="display:flex;justify-content:space-between"><span>Остаток</span><b style="color:${rest ? 'var(--bad)' : 'inherit'}">${fmt(rest)}</b></div></div></div>` };
};
const profitLessonLine = x => `<button class="lesson-line pick" data-go="a.lesson" data-p='${esc(JSON.stringify({ id: x.lessonId }))}'><span>${x.lessonType === 'group' ? '👥' : '👤'}</span><span>${fdate(x.date)} · ${x.durationMin} мин${x.rent ? ' · 🏟 аренда' : ''}<div class="d">${esc(x.lessonType === 'group' ? `${x.groupName || 'Группа'}${x.students.length ? ` · ${plural(x.students.length, ['ученик', 'ученика', 'учеников'])}` : ''}` : x.students.join(' + ') || '—')}</div></span><span class="amt">${fmt(x.income)} − ${fmt(x.salary)}</span></button>`;
SCREENS['a.profit.teacher'] = async ({ period, tid }) => {
  const t = await api(`/profit/teacher/${tid}?period=${period}`);
  return { title: t.name, html: `${t.owner ? '<div class="card pad" style="background:var(--warn-soft);border-color:var(--warn-soft)">👑 Руководитель: зарплата не вычитается, остаётся в прибыли</div><div style="height:10px"></div>' : ''}${t.lessons.length ? `<div class="list">${t.lessons.map(profitLessonLine).join('')}</div><div class="card" style="margin-top:10px"><div class="total"><span>Выручка ${fmt(t.income)} · зарплата ${fmt(t.salary)}</span><span class="big">${fmt(t.profit)}</span></div></div>` : '<div class="empty">Нет тарифицируемых занятий</div>'}` };
};
const salaryLineCell = x => cell({ lead: x.kind === 'shift' ? '🕒' : x.kind === 'override' ? '✍️' : x.kind === 'in_shift' ? '↳' : '📘', plain: true, t: `${fdate(x.date)} · ${esc(x.label)}`, s: x.kind === 'in_shift' ? 'в смене — отдельно не оплачивается' : x.minutes ? `${x.minutes} мин` : '', r: `<b>${fmt(x.amount)}</b>` });
SCREENS['a.salaries'] = async ({ ym }) => {
  ym = ym || lastPeriods(1)[0];
  const s = await api(`/salaries?ym=${ym}`);
  return { title: 'Зарплаты', html: `${monthChips('a.salaries', ym, {})}${list(s.teachers.map(t => cell({ lead: initials(t.name), t: esc(t.name), s: t.directPay ? 'прямая оплата: инд. — 0, группы — как обычно' : plural(t.lessons, ['занятие', 'занятия', 'занятий']), r: `<b>${fmt(t.accrued)}</b>`, go: 'a.salary.t', p: { tid: t.id, ym } })))}<div class="card" style="margin-top:10px"><div class="total"><span>Итого за ${MON_NOM[+ym.slice(5) - 1].toLowerCase()}</span><span class="big">${fmt(s.total)}</span></div></div>` };
};
SCREENS['a.salary.t'] = async ({ tid, ym }) => {
  const t = await api(`/salaries/${tid}?ym=${ym}`);
  return { title: `${t.name} · ${MON_NOM[+ym.slice(5) - 1]}`, html: `${t.lines.length ? list(t.lines.map(salaryLineCell)) : '<div class="empty">Начислений нет</div>'}<div class="card" style="margin-top:10px"><div class="total"><span>Начислено</span><span class="big">${fmt(t.total)}</span></div></div>` };
};
SCREENS['a.payouts'] = async ({ ym }) => {
  ym = ym || lastPeriods(1)[0];
  const p = await api(`/payouts?ym=${ym}`);
  const icon = { paid: '🟢', partial: '🟡', none: '🔴' };
  return { title: 'Выплатить зарплату', html: `${monthChips('a.payouts', ym, {})}<div class="hint" style="margin-bottom:10px">начислено ${fmt(p.accrued)} · выплачено ${fmt(p.paid)} · остаток ${fmt(p.accrued - p.paid)}</div>${p.teachers.length ? list(p.teachers.map(t => cell({ lead: icon[t.status], plain: true, t: esc(t.name), s: `выплачено ${fmt(t.paid)} из ${fmt(t.accrued)}`, r: t.accrued - t.paid > 0 ? pill(`остаток ${fmt(t.accrued - t.paid)}`, 'warn') : pill('✓', 'ok'), go: 'a.payout', p: { tid: t.id, ym } }))) : '<div class="empty">Начислений нет</div>'}` };
};
SCREENS['a.payout'] = async ({ tid, ym }) => {
  const t = await api(`/payouts/${tid}?ym=${ym}`);
  const rest = Math.max(t.accrued - t.paid, 0);
  return { title: t.name, html: `
    <div class="kpis">${kpi(fmt(t.accrued), `начислено за ${MON_NOM[+ym.slice(5) - 1].toLowerCase()}`)}${kpi(fmt(t.paid), 'уже выплачено', t.paid >= t.accrued && t.accrued ? 'ok' : '')}</div>
    <div style="margin-top:12px">${btn(`✅ Выплатить остаток ${fmt(rest)}`, 'payoutRest', { tid, ym, amount: rest, name: t.name }, rest ? '' : 'sec')}${btn('Произвольная сумма (аванс)', 'payoutForm', { tid, ym, name: t.name }, 'ghost')}${btn('🕒 Нестандартный день', 'ovForm', { tid, ym, name: t.name }, 'ghost')}</div>
    ${t.payouts.length ? `<div class="eyebrow">Выплаты</div>${list(t.payouts.map(p => cell({ lead: '💸', plain: true, t: fmt(p.amount), s: `${fdate(p.date)}${p.comment ? ' · ' + esc(p.comment) : ''}` })))}` : ''}
    ${t.overrides.length ? `<div class="eyebrow">Нестандартные дни</div>${list(t.overrides.map(o => `<div class="cell static"><span class="lead plain">✍️</span><span><div class="t">${fdate(o.date)} · ${o.minutes} мин</div>${o.comment ? `<div class="s">${esc(o.comment)}</div>` : ''}</span><span class="r"><button class="chip" style="padding:2px 8px" data-act="delOverride" data-p='${esc(JSON.stringify({ id: o.id }))}' aria-label="Удалить">🗑</button></span></div>`))}` : ''}
    <div class="eyebrow">Строки начисления</div>${t.lines.length ? list(t.lines.map(salaryLineCell)) : '<div class="empty">Начислений нет</div>'}` };
};
SCREENS['a.payhist.search'] = async () => {
  const q = state.ui.q2 || '';
  const d = await api(`/payhist?q=${encodeURIComponent(q)}`);
  return { title: 'История оплат', html: `<input class="search" id="q2" placeholder="Фамилия ученика" value="${esc(q)}" autocomplete="off">${d.students.length ? list(d.students.map(s => cell({ lead: initials(s.name), t: esc(s.name), s: plural(s.payments, ['оплата', 'оплаты', 'оплат']), go: 'a.payhist.months', p: { sid: s.id } }))) : '<div class="empty">Никого не нашли</div>'}` };
};
SCREENS['a.payhist.months'] = async ({ sid }) => {
  const d = await api(`/payhist/${sid}`);
  return { title: d.student.name, html: `${d.months.length ? list(d.months.map(m => cell({ lead: m.pending ? '⏳' : m.paid ? '✅' : '•', plain: true, t: fmon(m.period), s: `оплачено ${fmt(m.paid)}${m.pending ? ` · ожидает ${fmt(m.pending)}` : ''}`, go: 'a.payhist.month', p: { sid, ym: m.period } }))) : '<div class="empty">Счетов и оплат пока нет</div>'}<div class="card" style="margin-top:10px"><div class="total"><span>Всего оплачено</span><span class="big">${fmt(d.totalPaid)}</span></div></div>` };
};
SCREENS['a.payhist.month'] = async ({ sid, ym }) => {
  const d = await api(`/payhist/${sid}/${ym}`);
  const row = p => cell({ lead: '✅', plain: true, t: `${esc(p.teacherName)} — ${fmt(p.amount)}`, s: `${p.paidAt ? fdate(p.paidAt.slice(0, 10)) + ' · ' : ''}${p.byTgId === 0 ? 'ЮКасса' : (METHOD[p.method] || p.method || 'вручную')}${p.comment && !['чек', 'ЮКасса'].includes(p.comment) ? ' · ' + esc(p.comment) : ''}` });
  return { title: `${d.student.name} · ${MON_NOM[+ym.slice(5) - 1]}`, html: `${d.paid.length ? `<div class="eyebrow">Оплачено</div>${list(d.paid.map(row))}` : ''}${d.pending.length ? `<div class="eyebrow">Ожидает</div>${list(d.pending.map(p => cell({ lead: '⏳', plain: true, t: `${esc(p.teacherName)} — ${fmt(p.amount)}`, s: 'остаток к оплате' })))}` : ''}${!d.paid.length && !d.pending.length ? '<div class="empty">За этот месяц записей нет</div>' : ''}` };
};
Object.assign(ACT, {
  finForm: ({ ym, kind }) => sheet(`<h3>${kind === 'income' ? '➕ Доход' : '➕ Расход'} · ${fmon(ym)}</h3>${field('fin-t', 'Название', '', kind === 'income' ? 'placeholder="Турнир, аренда костюмов…"' : 'placeholder="Аренда зала, реклама…"')}${field('fin-a', 'Сумма, ₽', '', 'inputmode="numeric"')}<div style="margin-top:12px">${btn('💾 Сохранить', 'addFin', { ym, kind })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`),
  addFin: async ({ ym, kind }) => { const title = val('fin-t').trim(), amount = +val('fin-a'); if (!title || !amount) { toast('Нужны название и сумма'); return; } try { await api('/finance', { method: 'POST', body: { periodMonth: ym, kind, title, amount } }); closeSheet(); render(); toast('Записано'); } catch (e) { toast(errText(e)); } },
  delFin: async ({ id }) => { try { await api(`/finance/${id}`, { method: 'DELETE' }); render(); toast('Запись удалена'); } catch (e) { toast(errText(e)); } },
  payoutRest: ({ tid, ym, amount, name }) => { if (!amount) return; sheet(`<h3>Выплатить остаток?</h3><div class="hint">${esc(name)} · ${fmon(ym)}</div><div class="money" style="font-size:26px;font-weight:800;margin:10px 0">${fmt(amount)}</div>${btn('💸 Выплатить', 'doPayout', { tid, ym, amount, comment: 'остаток' })}${btn('Отмена', 'closeSheet', {}, 'ghost')}`); },
  payoutForm: ({ tid, ym, name }) => sheet(`<h3>Аванс · ${esc(name)}</h3>${field('po-a', 'Сумма, ₽', '', 'inputmode="numeric"')}${field('po-c', 'Комментарий', '', 'placeholder="аванс"')}<div style="margin-top:12px">${btn('💸 Выплатить', 'doPayoutCustom', { tid, ym })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`),
  doPayoutCustom: ({ tid, ym }) => { const amount = +val('po-a'); if (!amount) { toast('Укажите сумму'); return; } ACT.doPayout({ tid, ym, amount, comment: val('po-c').trim() || 'аванс' }); },
  doPayout: async ({ tid, ym, amount, comment }) => { try { await api('/payouts', { method: 'POST', body: { teacherId: tid, periodMonth: ym, amount, comment } }); closeSheet(); render(); toast(`Выплачено ${fmt(amount)}`); } catch (e) { toast(errText(e)); } },
  ovForm: ({ tid, ym, name }) => sheet(`<h3>Нестандартный день</h3><div class="hint">${esc(name)}. Минуты за дату заменяют расчёт по группам в этот день.</div>${field('ov-d', 'Дата', lastPeriods(1)[0] === ym ? new Date().toISOString().slice(0, 10) : ym + '-01', 'type="date"')}${field('ov-m', 'Минуты смены', '', 'inputmode="numeric" placeholder="например, 120"')}${field('ov-c', 'Комментарий', '', 'placeholder="замена, короткий день…"')}<div style="margin-top:12px">${btn('💾 Сохранить', 'addOverride', { tid })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`),
  addOverride: async ({ tid }) => { const date = val('ov-d'), minutes = +val('ov-m'); if (!date || !(minutes >= 0)) { toast('Укажите дату и минуты'); return; } try { await api('/salary-overrides', { method: 'POST', body: { teacherId: tid, date, minutes, comment: val('ov-c').trim() } }); closeSheet(); render(); toast('День сохранён'); } catch (e) { toast(errText(e)); } },
  delOverride: async ({ id }) => { try { await api(`/salary-overrides/${id}`, { method: 'DELETE' }); render(); toast('Удалено'); } catch (e) { toast(errText(e)); } },
  remind: ({ n, total }) => sheet(`<h3>Напомнить должникам?</h3><div class="hint">Родители ${plural(n, ['ученика', 'учеников', 'учеников'])} получат сумму долга за закрытые месяцы (${fmt(total)}) и ссылку на счета.</div><div style="margin-top:12px">${btn('📤 Отправить', 'doRemind', {})}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`),
  doRemind: async () => { try { const r = await api('/debtors/remind', { method: 'POST' }); closeSheet(); toast(`Доставлено ${r.sent}${r.failed ? `, не доставлено ${r.failed}` : ''}${r.skipped ? `, без бота ${r.skipped}` : ''}`); } catch (e) { toast(errText(e)); } },
});


/* ── занятия за день ─────────────────────────────────────────────────── */
const lessonCell = ls => cell({ lead: ls.type === 'group' ? '👥' : ls.students.length > 1 ? '👫' : '👤', plain: true, t: esc(ls.type === 'group' ? ls.groupName || 'группа' : ls.students.join(' + ')), s: `${esc(ls.teacherName)} · ${ls.durationMin} мин${ls.type === 'group' && ls.students.length ? ` · ${plural(ls.students.length, ['ученик', 'ученика', 'учеников'])}` : ''}${ls.locked ? ' · 🔒' : ''}`, r: ls.earned ? `<b>${fmt(ls.earned)}</b>` : '', go: 'a.lesson', p: { id: ls.id } });
SCREENS['a.lessons.day'] = async ({ date }) => {
  const days = []; for (let i = 0; i < 7; i++) { const x = new Date(); x.setDate(x.getDate() - i); days.push(x.toISOString().slice(0, 10)); }
  const d = date || days[0];
  const r = await api(`/lessons?date=${d}`);
  return { title: 'Занятия за день', html: `<div class="chips">${days.map(x => `<button class="chip" aria-pressed="${x === d}" data-go="a.lessons.day" data-p='${esc(JSON.stringify({ date: x }))}' data-replace="1">${+x.slice(8)} ${MON_SHORT[+x.slice(5, 7) - 1]}</button>`).join('')}</div><div class="h2">${fdate(d)}</div>${r.lessons.length ? list(r.lessons.map(lessonCell)) + `<div class="card" style="margin-top:10px"><div class="total"><span>${plural(r.lessons.length, ['занятие', 'занятия', 'занятий'])} · зарплата педагогов</span><span class="big">${fmt(r.earned)}</span></div></div>` : '<div class="empty">В этот день занятий не отмечено</div>'}` };
};
SCREENS['a.lesson'] = async ({ id }) => {
  const l = await api(`/lessons/${id}`);
  return { title: 'Занятие', html: `
    <div class="card pad"><div style="font-weight:800;font-size:16px">${esc(l.type === 'group' ? l.groupName || 'Группа' : l.attendees.map(a => a.name).join(' + '))}</div><div class="hint">${fdate(l.date)} · ${l.durationMin} мин · ${esc(l.teacherName)}${l.recordedAt ? ` · отмечено ${l.recordedAt.slice(11, 16)}` : ''}</div>${l.locked ? '<div style="margin-top:8px">' + pill('🔒 период сдан', 'mute') + '</div>' : ''}</div>
    ${l.attendees.length ? `<div class="eyebrow">${l.type === 'group' ? 'Посетили' : 'Ученики'}</div>${list(l.attendees.map(a => cell({ lead: initials(a.name), t: esc(a.name), r: a.amount === null ? '' : a.amount ? `<b>${fmt(a.amount)}</b>` : esc(l.freeLabel || 'абонемент'), go: 'a.student', p: { id: a.studentId } })))}` : '<div class="empty">Посещаемость не отмечалась</div>'}
    <div class="card" style="margin-top:10px"><div class="total"><span>Зарплата педагога</span><span class="big">${fmt(l.earned)}</span></div></div>
    <div style="margin-top:12px">${btn(l.locked ? '🗑 Удалить (период сдан)' : '🗑 Удалить занятие', 'delLesson', { id, locked: l.locked }, 'danger')}</div>
    <p class="hint" style="margin-top:8px">Правка полей не поддерживается — как в боте: удалить и отметить заново.</p>` };
};
ACT.delLesson = ({ id, locked }) => sheet(`<h3>Удалить занятие?</h3><div class="hint">Начисления родителям и зарплата педагога по нему исчезнут.${locked ? ' Период сдан — вы удаляете как администратор.' : ''}</div><div style="margin-top:12px">${btn('🗑 Удалить', 'doDelLesson', { id }, 'danger')}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
ACT.doDelLesson = async ({ id }) => { try { await api(`/lessons/${id}`, { method: 'DELETE' }); closeSheet(); back(); toast('Занятие удалено'); } catch (e) { toast(errText(e)); } };

/* ── этап 3: школа — филиалы и группы, биллинг, состав, карточки ───────── */
const modeLabel = m => MODE[m] || m;
const chipsAct = (act, cur, items, extra = {}) => `<div class="chips">${items.map(([v, n]) => `<button class="chip" aria-pressed="${cur === v}" data-act="${act}" data-p='${esc(JSON.stringify({ ...extra, v }))}'>${n}</button>`).join('')}</div>`;
const nextPeriods = n => { const out = []; const d = new Date(); for (let i = 0; i < n; i++) { const x = new Date(d.getFullYear(), d.getMonth() + i, 1); out.push(`${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}`); } return out; };
const monthLabel = ym => ym === '*' ? 'постоянно' : ym ? `${MON_NOM[+ym.slice(5) - 1]} ${ym.slice(0, 4)}` : '—';

SCREENS['a.school'] = async () => ({ title: 'Школа', html: `${list([cell({ lead: '👩‍🏫', plain: true, t: 'Педагоги', s: 'ставки, группы, сданные периоды', go: 'a.teachers' }), cell({ lead: '🏢', plain: true, t: 'Филиалы и группы', s: 'биллинг, составы, архив', go: 'a.branches' }), cell({ lead: '📝', plain: true, t: 'Отметить занятие за педагога', s: 'группа, пара, солисты', go: 'a.record' })])}<div class="eyebrow">Добавить</div>${list([cell({ lead: '➕', plain: true, t: 'Педагога', go: 'a.teacher.add' }), cell({ lead: '➕', plain: true, t: 'Ученика', go: 'a.student.add' })])}` });

SCREENS['a.branches'] = async () => {
  const d = await api('/branches');
  return { title: 'Филиалы и группы', html: `${d.branches.map(b => `<div class="eyebrow" style="display:flex;justify-content:space-between;align-items:center"><span>${esc(b.name)}</span><span><button class="chip" style="padding:1px 8px;text-transform:none;letter-spacing:0" data-act="branchRename" data-p='${esc(JSON.stringify({ id: b.id, name: b.name }))}'>✏️</button> <button class="chip" style="padding:1px 8px" data-act="branchDelete" data-p='${esc(JSON.stringify({ id: b.id, name: b.name, groups: b.groups.length }))}'>🗑</button></span></div>${b.groups.length ? list(b.groups.map(g => cell({ lead: g.archived ? '📦' : '💃', plain: true, t: esc(g.name), s: `${modeLabel(g.mode)}${g.priceFull ? ' · ' + fmt(g.priceFull) : ''} · ${plural(g.students, ['ученик', 'ученика', 'учеников'])}${g.teachers.length ? ' · ' + esc(g.teachers.map(n => n.split(' ')[0]).join(', ')) : ''}`, go: 'a.group', p: { id: g.id } }))) : '<div class="empty">Групп нет</div>'}`).join('')}<div style="margin-top:12px">${btn('➕ Группа', 'groupAddForm', { branches: d.branches.map(b => [b.id, b.name]) }, 'sec')}${btn('➕ Филиал', 'branchAddForm', {}, 'ghost')}</div>` };
};

SCREENS['a.group'] = async ({ id }) => {
  const g = await api(`/groups/${id}`);
  const active = g.members.filter(m => !m.left), left = g.members.filter(m => m.left);
  const memberRow = m => `<div class="cell static"><span class="lead">${initials(m.name)}</span><button class="cell nolead" style="padding:0;border:0;display:block;text-align:left" data-go="a.student" data-p='${esc(JSON.stringify({ id: m.id }))}'><div class="t">${esc(m.name)}</div><div class="s">${g.mode === 'subscription' ? `с ${m.joined ? monthLabel(m.joined).toLowerCase() : 'начала'}${m.left ? ` · ушёл с ${monthLabel(m.left).toLowerCase()}` : ''}` : (m.hasParent ? '' : 'без родителя')}</div></button><span class="r">${g.mode === 'subscription' ? `<button class="chip" style="padding:2px 8px" data-go="a.member" data-p='${esc(JSON.stringify({ gid: id, sid: m.id, name: m.name, joined: m.joined, left: m.left }))}'>📅</button> ` : ''}<button class="chip" style="padding:2px 8px" data-act="memberRemove" data-p='${esc(JSON.stringify({ gid: id, sid: m.id, name: m.name, sub: g.mode === 'subscription' }))}' aria-label="Убрать">✖</button></span></div>`;
  return { title: g.name, html: `
    <div class="card pad"><div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start"><div><div style="font-weight:800;font-size:16px">${esc(g.name)}${g.archived ? ' 📦' : ''}</div><div class="hint">${esc(g.branchName)} · ${g.teachers.filter(t => t.assigned).map(t => esc(t.name.split(' ')[0])).join(', ') || 'педагог не назначен'}</div></div>${pill(modeLabel(g.mode), 'acc')}</div>${g.priceFull ? `<div class="money" style="margin-top:8px;font-size:20px;font-weight:800">${fmt(g.priceFull)} <span class="hint" style="font-size:13px;font-weight:500">${g.mode === 'subscription' ? 'в месяц с ученика' : `за посещение${g.priceShort ? ` · ${fmt(g.priceShort)} за ${g.durationShort} мин` : ''}`}</span></div>` : ''}</div>
    <div class="eyebrow">Состав · ${active.length}</div>${active.length ? `<div class="list">${active.map(memberRow).join('')}</div>` : '<div class="empty">Состав пуст</div>'}
    ${left.length ? `<div class="eyebrow">Ушли</div><div class="list">${left.map(memberRow).join('')}</div>` : ''}
    <div style="margin-top:10px">${goBtn('➕ Добавить ученика', 'a.group.addst', { id, name: g.name }, 'sec')}</div>
    <div class="eyebrow">Настройки</div>${list([cell({ lead: '💳', plain: true, t: 'Биллинг ученикам', s: `${modeLabel(g.mode)}${g.priceFull ? ' · ' + fmt(g.priceFull) : ''}${g.overrides.length ? ` · переопределений: ${g.overrides.length}` : ''}`, go: 'a.billing', p: { id } }), cell({ lead: '👩‍🏫', plain: true, t: 'Педагоги группы', s: g.teachers.filter(t => t.assigned).map(t => esc(t.name)).join(', ') || 'не назначены', go: 'a.group.teachers', p: { id } }), cell({ lead: '🧾', plain: true, t: 'Счета всей группе', s: 'за текущий месяц', go: 'a.pay.students', p: { ym: lastPeriods(1)[0], g: id, gname: g.name, bill: true } })])}
    <div style="margin-top:12px">${btn('✏️ Переименовать', 'groupRename', { id, name: g.name }, 'ghost')}${btn(g.archived ? '📤 Вернуть из архива' : '📦 В архив', 'groupArchive', { id, archived: !g.archived }, 'ghost')}${btn('🗑 Удалить группу', 'groupDelete', { id, name: g.name, students: active.length }, 'danger')}</div>
    <p class="hint" style="margin-top:8px">Архивная группа пропадает из выбора при записи и счетах, но история занятий, счетов и зарплат не меняется.</p>` };
};

SCREENS['a.group.addst'] = async ({ id, name }) => {
  const q = state.ui.q3 || '';
  const d = await api(`/students?q=${encodeURIComponent(q)}`);
  const g = await api(`/groups/${id}`); const inGroup = new Set(g.members.filter(m => !m.left).map(m => m.id));
  const sts = d.students.filter(s => !inGroup.has(s.id));
  return { title: 'Добавить в группу', html: `<div class="hint" style="margin-bottom:8px">${esc(name || g.name)}</div><input class="search" id="q3" placeholder="Фамилия ученика" value="${esc(q)}" autocomplete="off">${sts.length ? list(sts.slice(0, 40).map(s => `<button class="cell" data-act="memberAdd" data-p='${esc(JSON.stringify({ gid: id, sid: s.id, name: s.name }))}'><span class="lead">${initials(s.name)}</span><span><div class="t">${esc(s.name)}</div><div class="s">${esc(s.groups.join(', ')) || 'без группы'}</div></span><span class="r">➕</span></button>`)) : '<div class="empty">Никого не нашли</div>'}<div style="margin-top:12px">${goBtn('Нового ученика — создать', 'a.student.add', { groupId: id }, 'ghost')}</div>` };
};

SCREENS['a.group.teachers'] = async ({ id }) => {
  const g = await api(`/groups/${id}`);
  return { title: 'Педагоги группы', html: `<div class="hint" style="margin-bottom:10px">${esc(g.name)}. Группу могут вести несколько педагогов — занятие принадлежит тому, кто его отметил.</div>${list(g.teachers.map(t => `<button class="cell" data-act="groupTeacherToggle" data-p='${esc(JSON.stringify({ gid: id, tid: t.id, assigned: !t.assigned }))}'><span class="mark ${t.assigned ? 'on' : ''}">${t.assigned ? '✓' : ''}</span><span><div class="t">${esc(t.name)}</div></span><span></span></button>`))}` };
};

SCREENS['a.billing'] = async ({ id }) => {
  const g = await api(`/groups/${id}`);
  const ui = state.ui.bill && state.ui.bill.id === id ? state.ui.bill : (state.ui.bill = { id, mode: g.mode, eff: nextPeriods(2)[1] });
  const modeItems = [['none', 'без оплаты'], ['per_visit', 'по посещению'], ['subscription', 'абонемент']];
  return { title: 'Биллинг ученикам', html: `
    <div class="hint" style="margin-bottom:8px">${esc(g.name)} · сейчас: ${modeLabel(g.mode)}${g.priceFull ? ' · ' + fmt(g.priceFull) : ''}</div>
    <div class="eyebrow">Режим</div>${chipsAct('billMode', ui.mode, modeItems)}
    ${ui.mode === 'subscription' ? `${field('b-full', 'Цена в месяц с ученика, ₽', g.mode === 'subscription' ? g.priceFull : '', 'inputmode="numeric"')}<div class="eyebrow">Новая цена действует с</div>${chipsAct('billEff', ui.eff, nextPeriods(3).map(m => [m, MON_NOM[+m.slice(5) - 1]]))}<p class="hint">Смена цены — только вперёд: прошлые активные месяцы автоматически фиксируются старой ценой (при первом включении — нулём).</p>` : ''}
    ${ui.mode === 'per_visit' ? `${field('b-full', `Цена за посещение (${g.durationFull} мин), ₽`, g.priceFull || '', 'inputmode="numeric"')}${field('b-short', `Короткое занятие (${g.durationShort} мин), ₽ — только детсадовские группы`, g.priceShort || '', 'inputmode="numeric" placeholder="0 — нет"')}<p class="hint">Цена попадает в новые занятия; уже отмеченные хранят свою.</p>` : ''}
    ${ui.mode === 'none' ? '<p class="hint">Занятия отмечаются, но родителям не начисляются.</p>' : ''}
    <div style="margin-top:8px">${btn('💾 Сохранить', 'billSave', { id }, 'sec')}</div>
    ${g.mode === 'subscription' || ui.mode === 'subscription' ? `<div class="eyebrow">Переопределения цены · ${g.overrides.length}</div>${g.overrides.length ? list(g.overrides.map(o => `<div class="cell static"><span class="lead plain">${o.amount ? '💳' : '🚫'}</span><span><div class="t">${monthLabel(o.periodMonth)} · ${o.studentId ? esc(o.studentName || o.studentId) : 'вся группа'}</div><div class="s">${o.amount ? fmt(o.amount) : 'не начислять'}</div></span><span class="r"><button class="chip" style="padding:2px 8px" data-act="ovrDelete" data-p='${esc(JSON.stringify({ gid: id, periodMonth: o.periodMonth, studentId: o.studentId }))}'>✖</button></span></div>`)) : '<div class="empty">Переопределений нет</div>'}<div style="margin-top:8px">${btn('➕ Переопределение', 'ovrForm', { gid: id, members: g.members.filter(m => !m.left).map(m => [m.id, m.name]) }, 'ghost')}</div><p class="hint">Приоритет: ученик/месяц → постоянное правило ученика → группа/месяц → цена группы. 0 = не начислять.</p>` : ''}` };
};

SCREENS['a.member'] = async ({ gid, sid, name, joined, left }) => ({ title: 'Месяцы членства', html: `<div class="card pad"><div style="font-weight:800">${esc(name)}</div><div class="hint">Абонемент начисляется с месяца вступления и до месяца ухода (не включая).</div></div><div class="eyebrow">Вступил с</div>${chipsAct('memberJoined', joined || '', [['', 'с начала'], ...lastPeriods(6).reverse().map(m => [m, MON_NOM[+m.slice(5) - 1]])], { gid, sid })}<div class="eyebrow">Ушёл с</div>${chipsAct('memberLeft', left || '', [['', 'не ушёл'], ...nextPeriods(3).map(m => [m, MON_NOM[+m.slice(5) - 1]])], { gid, sid })}` });

SCREENS['a.student.add'] = async ({ groupId }) => {
  const ui = state.ui.ns || (state.ui.ns = { groups: new Set(groupId ? [groupId] : []) });
  if (!state.ui.groupsCache) state.ui.groupsCache = (await api('/pay/groups')).branches;
  return { title: 'Новый ученик', html: `${field('ns-name', 'Фамилия и имя', ui.name || '', 'placeholder="Фамилия первой — так ищет бот"')}<div class="eyebrow">Группы</div>${state.ui.groupsCache.map(b => `<div class="hint" style="margin:6px 0 4px">${esc(b.name)}</div><div class="chips">${b.groups.map(g => `<button class="chip" aria-pressed="${ui.groups.has(g.id)}" data-act="nsGroup" data-p='${esc(JSON.stringify({ g: g.id }))}'>${esc(g.name)}</button>`).join('')}</div>`).join('')}<p class="hint">Абонементная группа — членство с текущего месяца. Партнёра и родителя назначите в карточке.</p><div style="margin-top:12px">${btn('💾 Создать ученика', 'studentAdd', {})}</div>` };
};

SCREENS['a.partner'] = async ({ id, name }) => {
  const d = await api(`/students/${id}/partner-candidates`);
  return { title: 'Партнёр', html: `<div class="hint" style="margin-bottom:10px">${esc(name)} · кандидаты из общих групп; связь симметричная — у партнёра тоже обновится.</div>${d.noGroups ? '<div class="empty">Ученик не состоит в группах — сначала добавьте в группу</div>' : d.candidates.length ? list(d.candidates.map(c => `<button class="cell" data-act="partnerSet" data-p='${esc(JSON.stringify({ id, pid: c.id, name: c.name }))}'><span class="mark"></span><span><div class="t">${esc(c.name)}</div><div class="s">${c.hasPartner ? 'уже в паре — пара распадётся' : 'свободен'}</div></span><span></span></button>`)) : '<div class="empty">В общих группах никого нет</div>'}${d.partnerId ? `<div style="margin-top:12px">${btn('Убрать партнёра', 'partnerClear', { id }, 'danger')}</div>` : ''}` };
};

SCREENS['a.student.groups'] = async ({ id, name }) => {
  const s = await api(`/students/${id}`); const mine = new Set(s.groups.map(g => g.id));
  const d = await api('/branches');
  return { title: 'Группы ученика', html: `<div class="hint" style="margin-bottom:10px">${esc(name || s.name)}. Выход из абонементной группы — с указанием месяца.</div>${d.branches.map(b => `<div class="eyebrow">${esc(b.name)}</div>${list(b.groups.filter(g => !g.archived || mine.has(g.id)).map(g => `<button class="cell" data-act="studentGroupToggle" data-p='${esc(JSON.stringify({ sid: id, gid: g.id, gname: g.name, member: !mine.has(g.id), sub: g.mode === 'subscription' }))}'><span class="mark ${mine.has(g.id) ? 'on' : ''}">${mine.has(g.id) ? '✓' : ''}</span><span><div class="t">${esc(g.name)}${g.archived ? ' 📦' : ''}</div><div class="s">${modeLabel(g.mode)}${g.priceFull ? ' · ' + fmt(g.priceFull) : ''}</div></span><span></span></button>`))}`).join('')}` };
};

SCREENS['a.parent.link'] = async ({ id, name }) => {
  const q = state.ui.q4 || '';
  const d = await api(`/clients?q=${encodeURIComponent(q)}`);
  return { title: 'Родитель', html: `<div class="hint" style="margin-bottom:8px">${esc(name)} · карточка клиента: имя и телефон для связи. Доступ в бот родитель получает сам — по ссылке группы.</div><div class="eyebrow">Из списка</div><input class="search" id="q4" placeholder="Имя или телефон" value="${esc(q)}" autocomplete="off">${d.clients.length ? list(d.clients.slice(0, 30).map(c => `<button class="cell" data-act="clientLink" data-p='${esc(JSON.stringify({ sid: id, cid: c.id, name: c.name }))}'><span class="lead">${initials(c.name)}</span><span><div class="t">${esc(c.name)}</div><div class="s">${esc(c.phone || 'без телефона')}${c.students.length ? ' · ' + esc(c.students.map(n => n.split(' ')[0]).join(', ')) : ''}</div></span><span class="r">🔗</span></button>`)) : '<div class="empty">Никого не нашли</div>'}<div class="eyebrow">Новый родитель</div>${field('np-name', 'Имя', '', 'placeholder="как обращаться"')}${field('np-phone', 'Телефон', '', 'placeholder="+7 ••• ••• •• ••" inputmode="tel"')}<div style="margin-top:12px">${btn('➕ Создать и привязать', 'clientCreate', { sid: id }, 'sec')}</div>` };
};

SCREENS['a.teacher.add'] = async () => ({ title: 'Новый педагог', html: `${field('nt-tg', 'Telegram ID (можно позже)', '', 'inputmode="numeric" placeholder="число из @userinfobot"')}${field('nt-name', 'Фамилия и имя')}<div class="eyebrow">Ставки за 45 минут</div>${field('nt-g', 'Группа, ₽', '', 'inputmode="numeric"')}${field('nt-t', 'Индивидуальное — педагогу, ₽', '', 'inputmode="numeric"')}${field('nt-s', 'Индивидуальное — цена для ученика, ₽', '', 'inputmode="numeric"')}<div style="margin-top:12px">${btn('💾 Создать педагога', 'teacherAdd', {})}</div><p class="hint" style="margin-top:8px">С Telegram ID педагог сразу получит доступ к своему кабинету в боте.</p>` });

SCREENS['a.teacher.groups'] = async ({ id, name }) => {
  const t = await api(`/teachers/${id}`); const mine = new Set(t.groups.map(g => g.id));
  const d = await api('/branches');
  return { title: 'Группы педагога', html: `<div class="hint" style="margin-bottom:10px">${esc(name || t.name)}. Видимость учеников педагогу = его группы ∩ группы ученика.</div>${d.branches.map(b => `<div class="eyebrow">${esc(b.name)}</div>${list(b.groups.filter(g => !g.archived || mine.has(g.id)).map(g => `<button class="cell" data-act="teacherGroupToggle" data-p='${esc(JSON.stringify({ tid: id, gid: g.id, assigned: !mine.has(g.id) }))}'><span class="mark ${mine.has(g.id) ? 'on' : ''}">${mine.has(g.id) ? '✓' : ''}</span><span><div class="t">${esc(g.name)}${g.archived ? ' 📦' : ''}</div></span><span></span></button>`))}`).join('')}` };
};

Object.assign(ACT, {
  branchAddForm: () => sheet(`<h3>Новый филиал</h3>${field('nb-name', 'Название', '', 'placeholder="например, Коммунарка"')}<div style="margin-top:12px">${btn('💾 Создать', 'branchAdd', {})}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`),
  branchAdd: async () => { const name = val('nb-name').trim(); if (!name) { toast('Введите название'); return; } try { await api('/branches', { method: 'POST', body: { name } }); closeSheet(); render(); toast('Филиал создан'); } catch (e) { toast(errText(e)); } },
  branchRename: ({ id, name }) => sheet(`<h3>Переименовать филиал</h3>${field('nb-name', 'Название', name)}<div style="margin-top:12px">${btn('💾 Сохранить', 'doBranchRename', { id })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`),
  doBranchRename: async ({ id }) => { const name = val('nb-name').trim(); if (!name) return; try { await api(`/branches/${id}`, { method: 'PATCH', body: { name } }); closeSheet(); render(); toast('Переименовано'); } catch (e) { toast(errText(e)); } },
  branchDelete: ({ id, name, groups }) => { if (groups) { toast('Сначала удалите или перенесите группы филиала'); return; } sheet(`<h3>Удалить филиал «${esc(name)}»?</h3><div style="margin-top:12px">${btn('🗑 Удалить', 'doBranchDelete', { id }, 'danger')}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`); },
  doBranchDelete: async ({ id }) => { try { await api(`/branches/${id}`, { method: 'DELETE' }); closeSheet(); render(); toast('Филиал удалён'); } catch (e) { toast(e.code === 'has_groups' ? 'В филиале есть группы' : errText(e)); } },
  groupAddForm: ({ branches }) => { state.ui.ngb = branches[0] && branches[0][0]; sheet(`<h3>Новая группа</h3>${field('ng-name', 'Название', '', 'placeholder="БП Латина — Громов"')}<div class="eyebrow">Филиал</div>${chipsAct('ngBranch', state.ui.ngb, branches)}<p class="hint">Режим оплаты и цену зададите в карточке группы → «Биллинг ученикам».</p><div style="margin-top:12px">${btn('💾 Создать', 'groupAdd', {})}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`); },
  ngBranch: ({ v }) => { state.ui.ngb = v; document.querySelectorAll('.sheet .chip').forEach(c => c.setAttribute('aria-pressed', String(JSON.parse(c.dataset.p).v === v))); },
  groupAdd: async () => { const name = val('ng-name').trim(); if (!name || !state.ui.ngb) { toast('Введите название'); return; } try { const r = await api('/groups', { method: 'POST', body: { branchId: state.ui.ngb, name } }); closeSheet(); state.stack.pop(); go('a.group', { id: r.id }); toast('Группа создана'); } catch (e) { toast(errText(e)); } },
  groupRename: ({ id, name }) => sheet(`<h3>Переименовать группу</h3>${field('gn', 'Название', name)}<div style="margin-top:12px">${btn('💾 Сохранить', 'doGroupRename', { id })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`),
  doGroupRename: async ({ id }) => { const name = val('gn').trim(); if (!name) return; try { await api(`/groups/${id}`, { method: 'PATCH', body: { name } }); closeSheet(); render(); toast('Переименовано'); } catch (e) { toast(errText(e)); } },
  groupArchive: async ({ id, archived }) => { try { await api(`/groups/${id}`, { method: 'PATCH', body: { archived } }); render(); toast(archived ? 'Группа в архиве' : 'Группа возвращена'); } catch (e) { toast(errText(e)); } },
  groupDelete: ({ id, name, students }) => sheet(`<h3>Удалить «${esc(name)}»?</h3><div class="hint">${students ? `${plural(students, ['ученик выйдет', 'ученика выйдут', 'учеников выйдут'])} из состава, ` : ''}педагоги отвяжутся; прошлые занятия и счета останутся. Если нужна история в списках — лучше архив.</div><div style="margin-top:12px">${btn('🗑 Удалить группу', 'doGroupDelete', { id }, 'danger')}${btn('📦 Лучше в архив', 'groupArchiveClose', { id }, 'sec')}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`),
  doGroupDelete: async ({ id }) => { try { await api(`/groups/${id}`, { method: 'DELETE' }); closeSheet(); state.stack = [{ n: 'a.school' }, { n: 'a.branches' }]; render(); toast('Группа удалена'); } catch (e) { toast(errText(e)); } },
  groupArchiveClose: ({ id }) => { closeSheet(); ACT.groupArchive({ id, archived: true }); },
  groupTeacherToggle: async ({ gid, tid, assigned }) => { try { await api(`/groups/${gid}/teachers`, { method: 'PUT', body: { teacherId: tid, assigned } }); render(); } catch (e) { toast(errText(e)); } },
  teacherGroupToggle: async ({ tid, gid, assigned }) => { try { await api(`/teachers/${tid}/groups`, { method: 'PUT', body: { groupId: gid, assigned } }); render(); } catch (e) { toast(errText(e)); } },
  memberAdd: async ({ gid, sid, name }) => { try { await api(`/groups/${gid}/members`, { method: 'POST', body: { studentId: sid } }); state.ui.q3 = ''; back(); toast(`${name} — в группе`); } catch (e) { toast(errText(e)); } },
  memberRemove: ({ gid, sid, name, sub }) => { if (!sub) { sheet(`<h3>Убрать ${esc(name)} из группы?</h3><div style="margin-top:12px">${btn('✖ Убрать', 'doMemberRemove', { gid, sid }, 'danger')}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`); return; } const [cur, nxt] = nextPeriods(2); sheet(`<h3>Убрать ${esc(name)}?</h3><div class="hint">Абонементная группа: выберите, с какого месяца ученик ушёл — строка сохранится с датой ухода.</div><div style="margin-top:12px">${btn(`С этого месяца (${MON_NOM[+cur.slice(5) - 1].toLowerCase()}) — его не оплачивает`, 'doMemberRemove', { gid, sid, left: cur })}${btn(`Со следующего (${MON_NOM[+nxt.slice(5) - 1].toLowerCase()}) — этот оплачивает`, 'doMemberRemove', { gid, sid, left: nxt }, 'sec')}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`); },
  doMemberRemove: async ({ gid, sid, left }) => { try { const r = await api(`/groups/${gid}/members/${sid}`, { method: 'DELETE', body: left ? { leftPeriod: left } : {} }); closeSheet(); render(); toast(r.result === 'marked' ? `Помечен ушедшим с ${monthLabel(left).toLowerCase()}` : 'Убран из группы'); } catch (e) { toast(errText(e)); } },
  memberJoined: async ({ gid, sid, v }) => { try { await api(`/groups/${gid}/members/${sid}`, { method: 'PUT', body: { joinedPeriod: v || '' } }); const p = cur().p; p.joined = v; render(); } catch (e) { toast(e.code === 'bad_request' ? 'Для «с начала» очистить нельзя — выберите месяц' : errText(e)); } },
  memberLeft: async ({ gid, sid, v }) => { try { await api(`/groups/${gid}/members/${sid}`, { method: 'PUT', body: { leftPeriod: v } }); const p = cur().p; p.left = v; render(); toast(v ? `Ушёл с ${monthLabel(v).toLowerCase()}` : 'Пометка ухода снята'); } catch (e) { toast(errText(e)); } },
  billMode: ({ v }) => { state.ui.bill.mode = v; render(); },
  billEff: ({ v }) => { state.ui.bill.eff = v; render(); },
  billSave: async ({ id }) => { const ui = state.ui.bill; const body = { mode: ui.mode }; if (ui.mode !== 'none') { body.priceFull = +val('b-full') || 0; if (!body.priceFull) { toast('Укажите цену'); return; } } if (ui.mode === 'per_visit') body.priceShort = +val('b-short') || 0; if (ui.mode === 'subscription') body.effectivePeriod = ui.eff; try { const r = await api(`/groups/${id}/billing`, { method: 'PUT', body }); state.ui.bill = null; back(); toast(`Сохранено${r.pinned ? ` · прошлых месяцев зафиксировано: ${r.pinned}` : ''}`); } catch (e) { toast(errText(e)); } },
  ovrForm: ({ gid, members }) => { state.ui.ovr = { gid, sid: '', ym: lastPeriods(1)[0] }; const render_ = () => { const u = state.ui.ovr; return `<h3>Переопределение цены</h3><div class="eyebrow">Кому</div><select class="search" id="ovr-s"><option value="">вся группа</option>${members.map(([id, n]) => `<option value="${id}" ${u.sid === id ? 'selected' : ''}>${esc(n)}</option>`).join('')}</select><div class="eyebrow">Месяц</div>${chipsAct('ovrMonth', u.ym, [...lastPeriods(1), ...nextPeriods(3).slice(1)].map(m => [m, MON_NOM[+m.slice(5) - 1]]).concat([['*', 'постоянно (только ученику)']]))}${field('ovr-a', 'Сумма, ₽ (0 — не начислять)', '0', 'inputmode="numeric"')}<div style="margin-top:12px">${btn('💾 Сохранить', 'ovrSave', {})}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`; }; state.ui.ovrRender = render_; sheet(render_()); },
  ovrMonth: ({ v }) => { state.ui.ovr.ym = v; state.ui.ovr.sid = val('ovr-s'); const a = val('ovr-a'); closeSheet(); sheet(state.ui.ovrRender()); const f = document.getElementById('ovr-a'); if (f) f.value = a; },
  ovrSave: async () => { const u = state.ui.ovr; const sid = val('ovr-s') || null; const amount = +val('ovr-a') || 0; if (u.ym === '*' && !sid) { toast('«Постоянно» — только для конкретного ученика'); return; } try { await api(`/groups/${u.gid}/overrides`, { method: 'PUT', body: { periodMonth: u.ym, studentId: sid, amount } }); closeSheet(); render(); toast(amount ? `Цена ${fmt(amount)}` : 'Освобождение сохранено'); } catch (e) { toast(errText(e)); } },
  ovrDelete: async ({ gid, periodMonth, studentId }) => { try { await api(`/groups/${gid}/overrides`, { method: 'DELETE', body: { periodMonth, studentId } }); render(); toast('Переопределение снято'); } catch (e) { toast(errText(e)); } },
  nsGroup: ({ g }) => { const s = state.ui.ns.groups; s.has(g) ? s.delete(g) : s.add(g); state.ui.ns.name = val('ns-name'); render(); },
  studentAdd: async () => { const name = val('ns-name').trim(); if (!name) { toast('Введите имя'); return; } try { const r = await api('/students', { method: 'POST', body: { name, groupIds: [...state.ui.ns.groups] } }); state.ui.ns = null; state.stack.pop(); go('a.student', { id: r.id }); toast('Ученик создан'); } catch (e) { toast(errText(e)); } },
  studentRename: ({ id, name }) => sheet(`<h3>Переименовать</h3>${field('rn', 'Новое имя', name)}<p class="hint">В прошлых занятиях и счетах останется старое написание — так устроен бот.</p><div style="margin-top:12px">${btn('💾 Сохранить', 'doStudentRename', { id })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`),
  doStudentRename: async ({ id }) => { const name = val('rn').trim(); if (!name) return; try { await api(`/students/${id}`, { method: 'PATCH', body: { name } }); closeSheet(); render(); toast('Переименовано'); } catch (e) { toast(errText(e)); } },
  studentDelete: ({ id, name }) => sheet(`<h3>Удалить ${esc(name)}?</h3><div class="hint">Партнёр отвяжется, членства снимутся; отмеченные занятия останутся в истории.</div><div style="margin-top:12px">${btn('🗑 Удалить', 'doStudentDelete', { id }, 'danger')}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`),
  doStudentDelete: async ({ id }) => { try { await api(`/students/${id}`, { method: 'DELETE' }); closeSheet(); state.stack = [{ n: 'a.students' }]; render(); toast('Ученик удалён'); } catch (e) { toast(errText(e)); } },
  partnerSet: async ({ id, pid, name }) => { try { await api(`/students/${id}/partner`, { method: 'PUT', body: { partnerId: pid } }); back(); toast(`Пара: с ${name.split(' ')[0]}`); } catch (e) { toast(errText(e)); } },
  partnerClear: async ({ id }) => { try { await api(`/students/${id}/partner`, { method: 'PUT', body: { partnerId: null } }); back(); toast('Партнёр убран'); } catch (e) { toast(errText(e)); } },
  studentGroupToggle: ({ sid, gid, gname, member, sub }) => { if (member || !sub) { ACT.doStudentGroup({ sid, gid, member }); return; } const [cur_, nxt] = nextPeriods(2); sheet(`<h3>Выйти из «${esc(gname)}»?</h3><div class="hint">Абонементная группа: с какого месяца ученик ушёл.</div><div style="margin-top:12px">${btn(`С этого месяца (${MON_NOM[+cur_.slice(5) - 1].toLowerCase()})`, 'doStudentGroup', { sid, gid, member: false, left: cur_ })}${btn(`Со следующего (${MON_NOM[+nxt.slice(5) - 1].toLowerCase()})`, 'doStudentGroup', { sid, gid, member: false, left: nxt }, 'sec')}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`); },
  doStudentGroup: async ({ sid, gid, member, left }) => { try { await api(`/students/${sid}/groups`, { method: 'PUT', body: { groupId: gid, member, leftPeriod: left } }); closeSheet(); render(); } catch (e) { toast(errText(e)); } },
  tierToggle: async ({ id }) => { try { const r = await api(`/students/${id}/tier`, { method: 'POST' }); render(); toast(r.tier === 'short' ? 'Тариф: короткое занятие' : 'Тариф: полное занятие'); } catch (e) { toast(e.code === 'no_per_visit_group' ? 'Тариф есть только в группах «по посещению»' : errText(e)); } },
  clientLink: async ({ sid, cid, name }) => { try { await api(`/students/${sid}/client`, { method: 'PUT', body: { clientId: cid } }); state.ui.q4 = ''; back(); toast(`Родитель: ${name}`); } catch (e) { toast(errText(e)); } },
  clientCreate: async ({ sid }) => { const name = val('np-name').trim(), phone = val('np-phone').trim(); if (!phone) { toast('Нужен телефон'); return; } try { await api(`/students/${sid}/client`, { method: 'PUT', body: { name, phone } }); state.ui.q4 = ''; back(); toast('Родитель создан и привязан'); } catch (e) { toast(errText(e)); } },
  athleteUnlink: ({ id }) => sheet(`<h3>Отвязать Telegram спортсмена?</h3><div class="hint">Записи дневника останутся; ученик сможет привязаться заново через «Я спортсмен».</div><div style="margin-top:12px">${btn('🚫 Отвязать', 'doAthleteUnlink', { id }, 'danger')}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`),
  doAthleteUnlink: async ({ id }) => { try { await api(`/students/${id}/athlete`, { method: 'DELETE' }); closeSheet(); render(); toast('Telegram отвязан'); } catch (e) { toast(errText(e)); } },
  teacherAdd: async () => { const name = val('nt-name').trim(); const rates = { group: +val('nt-g') || 0, teacher: +val('nt-t') || 0, student: +val('nt-s') || 0 }; const tg = val('nt-tg').trim(); if (!name) { toast('Введите имя'); return; } try { const r = await api('/teachers', { method: 'POST', body: { name, rates, tgId: tg ? +tg : null } }); state.stack.pop(); go('a.teacher', { id: r.id }); toast(r.linked ? 'Педагог создан, аккаунт привязан' : 'Педагог создан'); } catch (e) { toast(errText(e)); } },
  ratesForm: ({ id, rates }) => sheet(`<h3>Ставки за 45 минут</h3>${field('r-g', 'Группа, ₽', rates.group, 'inputmode="numeric"')}${field('r-t', 'Индивидуальное — педагогу, ₽', rates.teacher, 'inputmode="numeric"')}${field('r-s', 'Индивидуальное — цена для ученика, ₽', rates.student, 'inputmode="numeric"')}<p class="hint">Новые ставки применяются к занятиям с этого момента; чтобы прошлые месяцы считались по старым, добавьте строку в лист teacher_rate_history.</p><div style="margin-top:12px">${btn('💾 Сохранить', 'ratesSave', { id })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`),
  ratesSave: async ({ id }) => { const rates = { group: +val('r-g') || 0, teacher: +val('r-t') || 0, student: +val('r-s') || 0 }; try { await api(`/teachers/${id}`, { method: 'PATCH', body: { rates } }); closeSheet(); render(); toast('Ставки обновлены'); } catch (e) { toast(errText(e)); } },
  openPeriod: ({ id, ym }) => sheet(`<h3>Открыть ${monthLabel(ym).toLowerCase()}?</h3><div class="hint">Педагог снова сможет добавлять и удалять занятия месяца; счета родителям за него перестанут быть окончательными.</div><div style="margin-top:12px">${btn('🔓 Открыть период', 'doOpenPeriod', { id, ym }, 'danger')}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`),
  doOpenPeriod: async ({ id, ym }) => { try { await api(`/teachers/${id}/periods/${ym}/open`, { method: 'POST' }); closeSheet(); render(); toast('Период открыт'); } catch (e) { toast(errText(e)); } },
  teacherDelete: ({ id, name }) => sheet(`<h3>Удалить ${esc(name)}?</h3><div class="hint">Связи с группами и доступ к боту удалятся; занятия останутся в истории под его именем.</div><div style="margin-top:12px">${btn('🗑 Удалить', 'doTeacherDelete', { id }, 'danger')}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`),
  doTeacherDelete: async ({ id }) => { try { await api(`/teachers/${id}`, { method: 'DELETE' }); closeSheet(); state.stack = [{ n: 'a.school' }, { n: 'a.teachers' }]; render(); toast('Педагог удалён'); } catch (e) { toast(errText(e)); } },
});

/* ── запись занятия за педагога (мастер как в боте) ─────────────────────── */
const KIND_LABEL = { group: 'Группа', pair: 'Пара', shared: 'Несколько солистов в одном занятии', soloist: 'Солисты по отдельности', rshare: 'Индивидуальное' };
const KIND_HINT = { group: 'занятие по расписанию группы', pair: 'счёт делится между партнёрами', shared: 'от 2 до 4 учеников, одно занятие', soloist: 'каждому — своё занятие', rshare: 'от 1 до 3 участниц, цена за каждую' };
const rw = () => state.ui.rw;
function rwReset(tid, name, keep = {}) { state.ui.rw = { tid, name, step: 0, date: keep.date || new Date().toISOString().slice(0, 10), kind: null, dur: null, gid: null, ask: null, ids: new Set(), tiers: {}, opts: keep.opts || null }; }
const stepBar = (n, total) => `<div class="step">${Array.from({ length: total }, (_, i) => `<i class="${i <= n ? 'on' : ''}"></i>`).join('')}</div>`;
const rwHeader = w => `<div class="hint" style="margin-bottom:8px">${esc(w.name)} · ${fdate(w.date)}${w.kind ? ' · ' + KIND_LABEL[w.kind] : ''}${w.dur ? ' · ' + w.dur + ' мин' : ''}</div>`;
const actCell = ({ lead, t, s, act, p }) => `<button class="cell" data-act="${act}" data-p='${esc(JSON.stringify(p))}'><span class="lead plain">${lead}</span><span><div class="t">${t}</div>${s ? `<div class="s">${s}</div>` : ''}</span><span class="r"><span class="chev">›</span></span></button>`;
const pick = (id, on, t, s, act, p) => `<button class="cell" data-act="${act}" data-p='${esc(JSON.stringify(p))}'><span class="mark ${on ? 'on' : ''}">${on ? '✓' : ''}</span><span><div class="t">${esc(t)}</div>${s ? `<div class="s">${s}</div>` : ''}</span><span></span></button>`;

SCREENS['a.record'] = async () => { const d = await api('/teachers'); return { title: 'За педагога', html: `<div class="hint" style="margin-bottom:10px">Выберите педагога — дальше обычный мастер записи; замок сданного периода у администратора обходится.</div>${list(d.teachers.map(t => cell({ lead: initials(t.name), t: esc(t.name), s: esc(t.groups.join(', ')) || 'групп нет', go: 'a.record.w', p: { tid: t.id, name: t.name } })))}` }; };

SCREENS['a.record.w'] = async ({ tid, name }) => {
  if (!rw() || rw().tid !== tid) rwReset(tid, name);
  const w = rw();
  if (!w.opts) w.opts = await api(`/record/options?teacher=${tid}`);
  const o = w.opts; const total = 5;
  if (w.step === 0) return { title: 'Отметить занятие', html: stepBar(0, total) + rwHeader(w) + `<div class="h2">Когда было занятие?</div><div class="chips"><button class="chip" aria-pressed="${w.date === o.today}" data-act="rwDate" data-p='${esc(JSON.stringify({ v: o.today }))}'>Сегодня</button><button class="chip" aria-pressed="${w.date === yesterdayOf(o.today)}" data-act="rwDate" data-p='${esc(JSON.stringify({ v: yesterdayOf(o.today) }))}'>Вчера</button></div>${field('rw-date', 'Другая дата', w.date, `type="date" max="${o.today}"`)}<div style="margin-top:12px">${btn('Дальше', 'rwNext', {})}</div>` };
  if (w.step === 1) return { title: 'Тип занятия', html: stepBar(1, total) + rwHeader(w) + list(o.kinds.map(k => actCell({ lead: { group: '👥', pair: '👫', shared: '🎯', soloist: '👤', rshare: '👤' }[k], t: KIND_LABEL[k], s: KIND_HINT[k], act: 'rwKind', p: { v: k } }))) };
  if (w.step === 2) { const opts = w.kind === 'rshare' ? o.rshareDurations : o.durations; return { title: 'Длительность', html: stepBar(2, total) + rwHeader(w) + `<div class="h2">Сколько минут?</div><div class="chips">${opts.map(m => `<button class="chip" style="padding:12px 16px;font-size:16px" aria-pressed="${w.dur === m}" data-act="rwDur" data-p='${esc(JSON.stringify({ v: m }))}'>${m}</button>`).join('')}</div><p class="hint">Цена для родителя = ставка × минуты / 45; в группах «по посещению» — цена группы.</p>` }; }
  if (w.step === 3) {
    if (w.kind === 'group' && !w.gid) {
      const byBranch = {}; o.groups.forEach(g => (byBranch[g.branchName] = byBranch[g.branchName] || []).push(g));
      return { title: 'Группа', html: stepBar(3, total) + rwHeader(w) + (o.groups.length ? Object.entries(byBranch).map(([b, gs]) => `<div class="eyebrow">${esc(b)}</div>${list(gs.map(g => actCell({ lead: '💃', t: esc(g.name), s: `${MODE[g.mode]}${g.priceFull ? ' · ' + fmt(g.priceFull) : ''} · ${plural(g.roster.length, ['ученик', 'ученика', 'учеников'])}`, act: 'rwGroup', p: { v: g.id } })))}`).join('') : '<div class="empty">У педагога нет групп</div>') };
    }
    if (w.kind === 'group') {
      const g = o.groups.find(x => x.id === w.gid);
      if (g.mode === 'per_visit' && w.ask === null) return { title: 'Посещаемость', html: stepBar(3, total) + rwHeader(w) + `<div class="card pad"><div style="font-weight:800">${esc(g.name)}</div><div class="hint">Отметить присутствующих? В группе «по посещению» счёт получают только отмеченные.</div></div><div style="margin-top:12px">${btn('✅ Да, отметить', 'rwAsk', { v: true })}${btn('Нет — записать без посещаемости', 'rwAsk', { v: false }, 'ghost')}</div>` };
      if (g.mode === 'per_visit' && w.ask) {
        const hasShort = g.priceShort > 0;
        return { title: 'Кто был?', html: stepBar(3, total) + rwHeader(w) + `<div class="hint" style="margin-bottom:8px">${esc(g.name)} · ${fmt(g.priceFull)} за посещение${hasShort ? ` · короткое ${g.durationShort} мин — ${fmt(g.priceShort)}` : ''}<br>Кнопка справа — тариф на это занятие, по кругу до «🆓 пробное» (0 ₽).</div><div class="chips"><button class="chip" data-act="rwAll" data-p='${esc(JSON.stringify({ ids: g.roster.map(s => s.id) }))}'>${w.ids.size === g.roster.length ? 'Снять всех' : 'Отметить всех'}</button></div><div class="list">${g.roster.map(s => { const tier = w.tiers[s.id] || s.tier; return `<div class="cell static" style="padding-right:8px"><button class="mark ${w.ids.has(s.id) ? 'on' : ''}" style="border:1.5px solid var(--line-strong)" data-act="rwToggle" data-p='${esc(JSON.stringify({ v: s.id }))}'>${w.ids.has(s.id) ? '✓' : ''}</button><button class="cell nolead" style="padding:0;border:0;display:block;text-align:left" data-act="rwToggle" data-p='${esc(JSON.stringify({ v: s.id }))}'><div class="t">${esc(s.name)}</div></button><span class="r"><button class="chip" style="padding:2px 8px" data-act="rwTier" data-p='${esc(JSON.stringify({ v: s.id }))}'>${tier === 'trial' ? '🆓 пробное' : `${tier === 'short' && hasShort ? g.durationShort : g.durationFull} мин`} ↕</button></span></div>`; }).join('')}</div><div style="margin-top:12px">${btn(`Дальше (${w.ids.size})`, 'rwNext', {}, w.ids.size ? '' : 'sec')}</div>` };
      }
    }
    if (w.kind === 'rshare') { const pool = []; const seen = new Set(); o.groups.filter(g => g.id !== o.rshareGroupId).forEach(g => g.roster.forEach(s => { if (!seen.has(s.id)) { seen.add(s.id); pool.push({ ...s, group: g.name }); } })); return { title: 'Участницы', html: stepBar(3, total) + rwHeader(w) + `<div class="hint" style="margin-bottom:8px">Отметьте от 1 до 3 участниц. Отмечено: ${w.ids.size}</div>${list(pool.map(s => pick(s.id, w.ids.has(s.id), s.name, esc(s.group), 'rwToggle', { v: s.id, max: 3 })))}<div style="margin-top:12px">${btn(`Дальше (${w.ids.size})`, 'rwNext', {}, w.ids.size ? '' : 'sec')}</div>` }; }
    if (w.kind === 'pair') return { title: 'Пары', html: stepBar(3, total) + rwHeader(w) + (o.pairs.length ? `<div class="hint" style="margin-bottom:8px">Отметьте пары, которые занимались.</div>${list(o.pairs.map(p => pick(p.aId, w.ids.has(p.aId), `${p.aName} ↔ ${p.bName}`, '', 'rwToggle', { v: p.aId })))}<div style="margin-top:12px">${btn(`Дальше (${w.ids.size})`, 'rwNext', {}, w.ids.size ? '' : 'sec')}</div>` : '<div class="empty">У педагога нет сформированных пар</div>') };
    const max = w.kind === 'shared' ? 4 : 99;
    return { title: w.kind === 'shared' ? 'Солисты вместе' : 'Солисты', html: stepBar(3, total) + rwHeader(w) + `<div class="hint" style="margin-bottom:8px">${w.kind === 'shared' ? 'Отметьте от 2 до 4 учеников — одно занятие, счёт делится поровну.' : 'Отметьте учеников — каждому запишется своё занятие. Можно отметить и ученика из пары, если он пришёл один.'} Отмечено: ${w.ids.size}</div>${list(o.students.map(s => pick(s.id, w.ids.has(s.id), s.name, s.partnerId ? 'в паре' : '', 'rwToggle', { v: s.id, max })))}<div style="margin-top:12px">${btn(`Дальше (${w.ids.size})`, 'rwNext', {}, w.ids.size ? '' : 'sec')}</div>` };
  }
  // подтверждение
  const g = w.gid ? o.groups.find(x => x.id === w.gid) : null;
  const names = w.kind === 'pair' ? o.pairs.filter(p => w.ids.has(p.aId)).map(p => `${p.aName} ↔ ${p.bName}`) : [...w.ids].map(id => { const all = [...o.students, ...o.groups.flatMap(x => x.roster)]; const s = all.find(x => x.id === id); return s ? s.name : id; });
  return { title: 'Проверьте', html: stepBar(4, total) + `<div class="card pad"><div style="font-weight:800;font-size:16px">${esc(g ? g.name : KIND_LABEL[w.kind])}</div><div class="hint">${esc(w.name)} · ${fdate(w.date)} · ${w.dur} мин</div></div>${names.length ? `<div class="eyebrow">${w.kind === 'pair' ? 'Пары' : 'Ученики'} · ${names.length}</div>${list(names.map(n => cell({ t: esc(n) })))}` : g ? '<p class="hint" style="margin-top:8px">Без отметки посещаемости — ' + (g.mode === 'per_visit' ? 'счета никому не выставятся' : MODE[g.mode]) + '.</p>' : ''}<div style="margin-top:12px">${btn('💾 Сохранить занятие', 'rwSave', {})}${btn('Отмена', 'rwCancel', {}, 'ghost')}</div>` };
};
function yesterdayOf(today) { const d = new Date(today + 'T00:00:00'); d.setDate(d.getDate() - 1); return d.toISOString().slice(0, 10); }
Object.assign(ACT, {
  rwDate: ({ v }) => { rw().date = v; render(); },
  rwKind: ({ v }) => { const w = rw(); w.kind = v; w.ids = new Set(); w.tiers = {}; w.gid = v === 'rshare' ? w.opts.rshareGroupId : null; w.ask = null; if (v === 'rshare') { w.dur = 60; w.step = 3; } else w.step = 2; render(); },
  rwDur: ({ v }) => { const w = rw(); w.dur = v; w.step = 3; if (w.kind === 'group' && w.opts.groups.length === 1) { w.gid = w.opts.groups[0].id; } render(); },
  rwGroup: ({ v }) => { const w = rw(); w.gid = v; w.ask = null; w.ids = new Set(); const g = w.opts.groups.find(x => x.id === v); if (g.mode !== 'per_visit') { w.step = 4; } render(); },
  rwAsk: ({ v }) => { const w = rw(); w.ask = v; if (!v) { w.ids = new Set(); w.step = 4; } render(); },
  rwToggle: ({ v, max }) => { const w = rw(); if (w.ids.has(v)) w.ids.delete(v); else if (!max || w.ids.size < max) w.ids.add(v); else toast(`Не больше ${max}`); render(); },
  rwAll: ({ ids }) => { const w = rw(); if (w.ids.size === ids.length) w.ids = new Set(); else w.ids = new Set(ids); render(); },
  rwTier: ({ v }) => { const w = rw(); const g = w.opts.groups.find(x => x.id === w.gid); const s = g.roster.find(x => x.id === v); const order = g.priceShort > 0 ? ['full', 'short', 'trial'] : ['full', 'trial']; const i = order.indexOf(w.tiers[v] || s.tier); w.tiers[v] = order[(i + 1) % order.length]; if (!w.ids.has(v)) w.ids.add(v); render(); },
  rwNext: () => { const w = rw(); if (w.step === 0) { const d = val('rw-date'); if (d) w.date = d; if (w.date > w.opts.today) { toast('Дата в будущем'); return; } w.step = 1; } else if (w.step === 3) { if (w.kind === 'shared' && w.ids.size < 2) { toast('Нужно от 2 до 4 учеников'); return; } if (!w.ids.size) return; w.step = 4; } render(); },
  rwCancel: () => { state.ui.rw = null; back(); },
  rwSave: async () => { const w = rw(); const body = { teacherId: w.tid, kind: w.kind, date: w.date, durationMin: w.dur, groupId: w.gid, studentIds: [...w.ids], tiers: w.tiers }; try { const r = await api('/record', { method: 'POST', body }); const keep = { date: w.date, opts: w.opts }; rwReset(w.tid, w.name, keep); rw().step = 1; render(); toast(r.created > 1 ? `Создано занятий: ${r.created}` : `Записано: ${r.label}`); } catch (e) { toast(e instanceof ApiError && e.code === 'conflict' ? 'Соло с этим учеником на эту дату уже записано' : errText(e)); } },
});

/* ── рендер ─────────────────────────────────────────────────────────── */
async function render() {
  const seq = ++renderSeq; const s = cur();
  const content = document.getElementById('content');
  const rootN = state.stack[0].n;
  document.getElementById('tabs').innerHTML = TABS[ROLE].map(([n, label, ic]) => `<button role="tab" aria-selected="${rootN === n}" data-root="${n}"><svg viewBox="0 0 24 24">${ICON[ic]}</svg>${label}</button>`).join('');
  const deep = state.stack.length > 1;
  document.getElementById('back').classList.toggle('on', deep && !tg);
  if (tg) { try { deep ? tg.BackButton.show() : tg.BackButton.hide(); } catch (_) { /* нет BackButton */ } }
  content.innerHTML = skeleton();
  let scr;
  try { scr = await SCREENS[s.n](s.p || {}); }
  catch (e) { scr = { title: 'Ошибка', html: `<div class="card pad"><div style="font-weight:700">${esc(errText(e))}</div></div><div style="margin-top:12px">${btn('Повторить', 'retry', {}, 'sec')}</div>` }; }
  if (seq !== renderSeq) return;
  document.getElementById('title').innerHTML = `${esc(scr.title)}<span class="sub">${ROLE_TITLE[ROLE]}</span>`;
  content.innerHTML = `<div class="fade">${scr.html}</div>`; content.scrollTop = 0;
  const q = document.getElementById('q') || document.getElementById('q2') || document.getElementById('q3') || document.getElementById('q4'); const qid = q ? q.id : null; const qkey = qid || 'q';
  if (q) { let t; q.addEventListener('input', e => { state.ui[qkey] = e.target.value; clearTimeout(t); t = setTimeout(() => { const pos = e.target.selectionStart; render().then(() => { const nq = document.getElementById(qid); if (nq) { nq.focus(); nq.setSelectionRange(pos, pos); } }); }, 250); }); }
}
ACT.retry = () => render();
ACT.sfToggle = ({ k }) => { state.ui.sf[k] = !state.ui.sf[k]; render(); };
ACT.sfReset = () => { state.ui.sf = { group: '', noparent: false, debt: false }; state.ui.q = ''; render(); };
document.addEventListener('change', e => { if (e.target.id === 'sf-group') { state.ui.sf.group = e.target.value; render(); } });

document.addEventListener('click', e => {
  const stop = e.target.closest('[data-stop]'); const wrap = e.target.closest('.sheet-wrap');
  if (wrap && !stop) { closeSheet(); return; }
  if (e.target.closest('select') || e.target.closest('input') || e.target.closest('label')) return;
  const el = e.target.closest('[data-go],[data-act],[data-root]'); if (!el) return;
  if (el.dataset.root) { closeSheet(); root(el.dataset.root); return; }
  const p = el.dataset.p ? JSON.parse(el.dataset.p) : {};
  if (el.dataset.go) { if (el.dataset.replace) state.stack.pop(); go(el.dataset.go, p); return; }
  if (el.dataset.act && ACT[el.dataset.act]) ACT[el.dataset.act](p);
});
document.getElementById('back').addEventListener('click', () => { closeSheet(); back(); });
if (tg) { try { tg.BackButton.onClick(() => { closeSheet(); back(); }); } catch (_) { /* нет BackButton */ } }

ACT.switchRole = async ({ to }) => {
  const prev = ROLE; ROLE = to;
  try { state.me = await api('/me'); state.ui = {}; root(ROLE_HOME[ROLE]); }
  catch (e) { ROLE = prev; toast(errText(e)); }
};

(async () => {
  try { state.me = await api('/me'); }
  catch (e) {
    if (!(e instanceof ApiError) || e.status !== 403) return fail(e);
    ROLE = 'teacher';                       // не администратор — пробуем кабинет педагога
    try { state.me = await api('/me'); } catch (e2) { return fail(e2); }
  }
  state.stack = [{ n: ROLE_HOME[ROLE] }];
  render();
  function fail(e) {
    document.getElementById('tabs').innerHTML = '';
    document.getElementById('content').innerHTML = `<div class="card pad" style="margin-top:20px"><div style="font-weight:800;font-size:16px">Нет доступа</div><div class="hint" style="margin-top:6px">${esc(errText(e))}</div></div>`;
  }
})();
