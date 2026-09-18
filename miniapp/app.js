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
const kpi = (v, l, kind = '', go, p) => go ? `<button class="kpi ${kind}" ${attr(go, p)}><div class="v">${v}</div><div class="l">${l} ›</div></button>` : `<div class="kpi ${kind}"><div class="v">${v}</div><div class="l">${l}</div></div>`;
const restPill = x => x.total === 0 ? pill('нет начислений') : x.rest === 0 ? pill('✓ оплачено', 'ok') : x.paid ? pill(`к доплате ${fmt(x.rest)}`, 'warn') : pill(`к оплате ${fmt(x.rest)}`, 'warn');
const skeleton = () => '<div class="skeleton w60"></div><div class="skeleton tall"></div><div class="skeleton"></div><div class="skeleton tall"></div>';

/* ── навигация ───────────────────────────────────────────────────────── */
const TABS = [['a.home', 'Сводка', 'home'], ['a.payhub', 'Оплаты', 'card'], ['a.students', 'Ученики', 'users'], ['a.teachers', 'Педагоги', 'teacher'], ['a.finance', 'Финансы', 'chart']];
const ICON = {
  home: '<path d="M3 11 12 4l9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>', card: '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18M7 15h4"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M21.5 20a6.5 6.5 0 0 0-5-6.3"/>', chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  teacher: '<circle cx="12" cy="7" r="3.5"/><path d="M5 21a7 7 0 0 1 14 0M3 3h4M17 3h4"/>',
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
    ${list([cell({ lead: '💾', plain: true, t: 'Подтвердить оплату', s: 'ученик → педагог → занятия', go: 'a.pay' }), cell({ lead: '⚠️', plain: true, t: 'Должники', s: 'закрытые месяцы', go: 'a.debtors' }), cell({ lead: '🧾', plain: true, t: 'Счёт ученика', s: 'просмотр и отправка родителям', go: 'a.pay', p: { bill: true } })])}` };
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
    ${d.students.length ? list(d.students.map(s => cell({ lead: initials(s.name), t: esc(s.name), s: esc(s.groups.join(', ')) || 'без группы', r: s.hasParent ? '' : pill('без родителя', 'warn'), go: 'a.student', p: { id: s.id } }))) : '<div class="empty">Никого не нашли</div>'}` };
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

SCREENS['a.finance'] = async () => ({ title: 'Финансы', html: `<div class="eyebrow">Школа</div>${list([cell({ lead: '📊', plain: true, t: 'Прибыль', s: 'месяц или день; доходы и расходы', go: 'a.profit', p: {} })])}<div class="eyebrow">Педагоги</div>${list([cell({ lead: '💰', plain: true, t: 'Зарплаты', s: 'начислено педагогам, строки', go: 'a.salaries', p: {} }), cell({ lead: '💸', plain: true, t: 'Выплатить зарплату', s: 'остаток, аванс, нестандартный день', go: 'a.payouts', p: {} })])}<p class="hint" style="margin-top:12px">Филиалы и группы, редактирование карточек и занятий — следующий этап; пока из бота.${state.me ? ` Вы вошли как администратор (id ${state.me.tgId}).` : ''}</p>` });

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
    <div class="kpis">${kpi(fmt(p.totals.totalIncome), 'выручка')}${kpi(fmt(p.totals.profit), 'прибыль', 'ok')}</div>
    <div class="eyebrow">Педагоги</div><div class="list">${p.rows.map(r => profitRow(r, ym)).join('')}</div>
    ${p.subscriptions.length ? `<div class="eyebrow">Абонементы</div>${list(p.subscriptions.map(x => cell({ t: esc(x.groupName), s: plural(x.students, ['ученик', 'ученика', 'учеников']), r: `<b>${fmt(x.income)}</b>` })))}` : ''}`}
    <div class="eyebrow">Прочие доходы и расходы</div>${list(p.finance.map(f => `<div class="cell static"><span class="lead plain">${f.kind === 'income' ? '🏆' : '📉'}</span><span><div class="t">${esc(f.title)}</div></span><span class="r"><b style="color:${f.kind === 'income' ? 'var(--ok)' : 'var(--bad)'}">${f.kind === 'income' ? '+' : '−'}${fmt(f.amount)}</b> <button class="chip" style="padding:2px 8px;margin-left:6px" data-act="delFin" data-p='${esc(JSON.stringify({ id: f.id }))}' aria-label="Удалить">🗑</button></span></div>`).concat([`<div class="cell static"><span><div class="chips" style="margin:0"><button class="chip" data-act="finForm" data-p='${esc(JSON.stringify({ ym, kind: 'income' }))}'>➕ Доход</button><button class="chip" data-act="finForm" data-p='${esc(JSON.stringify({ ym, kind: 'expense' }))}'>➕ Расход</button></div></span><span></span></div>`]))}
    ${p.totals.isEmpty ? '' : totalsCard(p.totals)}` };
};
SCREENS['a.profit.day'] = async ({ date }) => {
  const days = []; for (let i = 0; i < 10; i++) { const x = new Date(); x.setDate(x.getDate() - i); days.push(x.toISOString().slice(0, 10)); }
  const d = date || days[0];
  const p = await api(`/profit/day?date=${d}`);
  return { title: 'Прибыль за день', html: `<div class="chips">${days.map(x => `<button class="chip" aria-pressed="${x === d}" data-go="a.profit.day" data-p='${esc(JSON.stringify({ date: x }))}' data-replace="1">${+x.slice(8)} ${MON_SHORT[+x.slice(5, 7) - 1]}</button>`).join('')}</div><div class="h2">${fdate(d)}</div>${p.rows.length ? `<div class="list">${p.rows.map(r => profitRow(r, d)).join('')}</div>${totalsCard(p.totals)}<p class="hint" style="margin-top:8px">За день — только занятия; абонементы и ручные записи считаются по месяцу.</p>` : '<div class="empty">Тарифицируемых занятий нет</div>'}` };
};
SCREENS['a.profit.teacher'] = async ({ period, tid }) => {
  const t = await api(`/profit/teacher/${tid}?period=${period}`);
  return { title: t.name, html: `${t.owner ? '<div class="card pad" style="background:var(--warn-soft);border-color:var(--warn-soft)">👑 Руководитель: зарплата не вычитается, остаётся в прибыли</div><div style="height:10px"></div>' : ''}${t.lessons.length ? `<div class="list">${t.lessons.map(x => `<div class="lesson-line"><span>${x.lessonType === 'group' ? '👥' : '👤'}</span><span>${fdate(x.date)} · ${x.durationMin} мин${x.rent ? ' · 🏟 аренда' : ''}</span><span class="amt">${fmt(x.income)} − ${fmt(x.salary)}</span></div>`).join('')}</div><div class="card" style="margin-top:10px"><div class="total"><span>Выручка ${fmt(t.income)} · зарплата ${fmt(t.salary)}</span><span class="big">${fmt(t.profit)}</span></div></div>` : '<div class="empty">Нет тарифицируемых занятий</div>'}` };
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
    ${l.attendees.length ? `<div class="eyebrow">${l.type === 'group' ? 'Посетили' : 'Ученики'}</div>${list(l.attendees.map(a => cell({ lead: initials(a.name), t: esc(a.name), r: a.amount === null ? '' : a.amount ? `<b>${fmt(a.amount)}</b>` : 'абонемент', go: 'a.student', p: { id: a.studentId } })))}` : '<div class="empty">Посещаемость не отмечалась</div>'}
    <div class="card" style="margin-top:10px"><div class="total"><span>Зарплата педагога</span><span class="big">${fmt(l.earned)}</span></div></div>
    <div style="margin-top:12px">${btn(l.locked ? '🗑 Удалить (период сдан)' : '🗑 Удалить занятие', 'delLesson', { id, locked: l.locked }, 'danger')}</div>
    <p class="hint" style="margin-top:8px">Правка полей не поддерживается — как в боте: удалить и отметить заново.</p>` };
};
ACT.delLesson = ({ id, locked }) => sheet(`<h3>Удалить занятие?</h3><div class="hint">Начисления родителям и зарплата педагога по нему исчезнут.${locked ? ' Период сдан — вы удаляете как администратор.' : ''}</div><div style="margin-top:12px">${btn('🗑 Удалить', 'doDelLesson', { id }, 'danger')}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
ACT.doDelLesson = async ({ id }) => { try { await api(`/lessons/${id}`, { method: 'DELETE' }); closeSheet(); back(); toast('Занятие удалено'); } catch (e) { toast(errText(e)); } };

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
  const q = document.getElementById('q') || document.getElementById('q2'); const qid = q ? q.id : null; const qkey = qid === 'q2' ? 'q2' : 'q';
  if (q) { let t; q.addEventListener('input', e => { state.ui[qkey] = e.target.value; clearTimeout(t); t = setTimeout(() => { const pos = e.target.selectionStart; render().then(() => { const nq = document.getElementById(qid); if (nq) { nq.focus(); nq.setSelectionRange(pos, pos); } }); }, 250); }); }
}
ACT.retry = () => render();
ACT.sfToggle = ({ k }) => { state.ui.sf[k] = !state.ui.sf[k]; render(); };
ACT.sfReset = () => { state.ui.sf = { group: '', noparent: false, debt: false }; state.ui.q = ''; render(); };
document.addEventListener('change', e => { if (e.target.id === 'sf-group') { state.ui.sf.group = e.target.value; render(); } });

document.addEventListener('click', e => {
  const stop = e.target.closest('[data-stop]'); const wrap = e.target.closest('.sheet-wrap');
  if (wrap && !stop) { closeSheet(); return; }
  if (e.target.closest('select')) return;
  const el = e.target.closest('[data-go],[data-act],[data-root]'); if (!el) return;
  if (el.dataset.root) { closeSheet(); root(el.dataset.root); return; }
  const p = el.dataset.p ? JSON.parse(el.dataset.p) : {};
  if (el.dataset.go) { if (el.dataset.replace) state.stack.pop(); go(el.dataset.go, p); return; }
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
