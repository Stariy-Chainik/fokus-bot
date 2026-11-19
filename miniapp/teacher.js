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
    // аренда зала за прошлый месяц ещё не перечислена школе (прямая оплата, с месяца начала учёта)
    h.rentDue ? cell({ lead: '🏠', plain: true, t: `Аренда зала за ${MON_NOM[+h.rentDue.period.slice(5) - 1].toLowerCase()}`, s: 'перечислите школе одной суммой', r: `<b>${fmt(h.rentDue.rest)}</b>`, go: 't.money', p: { ym: h.rentDue.period } }) : '',
    h.newChild ? cell({ lead: '🆕', plain: true, t: 'Заявки родителей на детей', s: 'ребёнка нет в списке группы — добавить или отклонить', r: pill(h.newChild, 'warn'), go: 't.nchild' }) : '',
    h.unrated ? cell({ lead: '📓', plain: true, t: 'Оценить тренировки', s: `${plural(h.unrated, ['запись', 'записи', 'записей'])} спортсменов без оценки`, r: pill(h.unrated, 'warn'), go: 't.diary' }) : '',
    h.periodSubmit && !h.prevSubmitted ? cell({ lead: '📤', plain: true, t: `${prevMon} не сдан`, s: 'сдайте период, чтобы счёт родителям стал окончательным', go: 't.money', p: { ym: h.prevPeriod } }) : '',
  ].filter(Boolean);
  const recordLesson = goBtn('✏️ Отметить занятие', 'a.record.w', { tid: state.me.teacherId, name: state.me.name });
  const today = h.lessonsToday ? `
    <div class="kpis">${kpi(plural(h.lessonsToday, ['занятие', 'занятия', 'занятий']), 'отмечено сегодня', '', 't.lessons', { key: h.today })}${kpi(fmt(h.earnedToday), h.directToday ? `от школы · ещё ${fmt(h.directToday)} напрямую` : 'заработано сегодня', 'ok', 't.lessons', { key: h.today })}</div>
    <div style="margin-top:10px">${list(h.todayLessons.map(x => tLessonCell(x)))}</div>
    <div style="margin-top:10px">${recordLesson}</div>` : `<div class="card pad">
      <div class="hint">Занятий сегодня ещё не отмечено</div>
      <div style="margin-top:10px">${recordLesson}</div>
    </div>`;
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
    ${h.schedulePilot ? `<div style="margin-top:12px">${list([cell({ lead: '📋', plain: true, t: 'Расписание из Telegram', go: 't.schedule.drafts' })])}</div>` : ''}
    <div class="eyebrow">${mon}</div>
    ${list([
      cell({ lead: '💰', plain: true, t: 'Зарплата', s: `${monthLine}${h.paidMonth ? ` · выплачено ${fmt(h.paidMonth)}` : ''}`, r: `<b>${fmt(h.earnedMonth)}</b>`, go: 't.money', p: { ym: h.period } }),
      ...(h.directMonth ? [cell({ lead: '🤝', plain: true, t: 'Напрямую от родителей', s: 'индивидуальные — платят вам лично', r: `<b class="direct">${fmt(h.directMonth)}</b>`, go: 't.money', p: { ym: h.period } })] : []),
      // две цифры рядом: зарплата от школы и аренда зала школе (решение владельца 27.10.2026)
      ...(h.rentMonth ? [cell({ lead: '🏠', plain: true, t: 'Аренда зала школе', s: 'индивидуальные — перечисляете в конце месяца', r: `<b>${fmt(h.rentMonth)}</b>`, go: 't.money', p: { ym: h.period } })] : []),
    ])}
    ${state.me.isAdmin ? `<div style="margin-top:14px">${btn('🛠 Режим администратора', 'switchRole', { to: 'admin' }, 'ghost')}</div>` : ''}` };
};

/* ── Предварительное расписание ────────────────────────────────────── */
const schedulePlan = id => state.ui.scheduleDrafts?.find(d => d.id === id);
const scheduleDraft = () => state.ui.scheduleEdit;
const scheduleStatus = { held: 'В журнале', cancelled: 'Отменено', pending: 'Проверить запись' };
const scheduleTitle = s => s.kind === 'free' ? 'Свободное окно' : s.kind === 'group' ?
  (s.groupId ? s.text : 'Групповое занятие') : s.students.join(' + ') || s.text;
const scheduleTime = s => `<span class="schedule-time"><time>${esc(s.time)}</time>${s.end ? `<time class="schedule-end">${esc(s.end)}</time>` : ''}</span>`;
const scheduleRows = (slots, id = null) => slots.length ? `<ol class="schedule-list">${slots.map(s => {
  const free = s.kind === 'free';
  const type = !free && s.kind === 'individual' && !s.studentIds?.length ? 'Уточнить участников' :
    s.kind === 'group' && !s.groupId ? 'Выбрать группу' : '';
  const duration = s.duration ? `${s.duration} мин${s.suggested ? ' по сетке' : ''}` : free ? '' : 'Уточнить длительность';
  const label = scheduleStatus[s.status];
  const inner = `${scheduleTime(s)}<span class="schedule-slot-text"><strong>${esc(scheduleTitle(s))}</strong>
    ${type || duration ? `<span class="hint">${esc([type, duration].filter(Boolean).join(' · '))}</span>` : ''}
    ${label ? `<span class="schedule-state ${esc(s.status)}">${label}</span>` : ''}</span>`;
  const style = `schedule-slot${free || s.status === 'cancelled' ? ' schedule-free' : ''}`;
  return `<li>${id === null ? `<div class="${style}">${inner}</div>` :
    `<button type="button" class="${style}" data-act="scheduleTap" data-p='${esc(JSON.stringify({ id, time: s.time }))}'>
      ${inner}<span class="chev" aria-hidden="true">›</span></button>`}</li>`;
}).join('')}</ol>` : '<p class="hint schedule-empty">В черновике пока нет строк.</p>';

async function loadSchedule() {
  const { drafts } = await api('/schedule-drafts');
  state.ui.scheduleDrafts = drafts;
}

SCREENS['t.schedule.drafts'] = async () => {
  await loadSchedule();
  const drafts = state.ui.scheduleDrafts;
  const day = d => {
    const hasMarks = d.slots.some(s => s.status && s.status !== 'planned');
    const date = esc(fdate(d.date));
    const toMark = d.slots.some(s => s.kind !== 'free' && (!s.status || s.status === 'planned' || s.status === 'pending'));
    return `<section class="card schedule-day">
      <div class="schedule-head"><div><h2>${hasMarks ? date :
        `<button type="button" class="schedule-date-link" data-act="scheduleChangeDate" data-p='{"id":${d.id}}'>${date}</button>`}</h2>
        <div class="hint">Черновик</div></div></div>
      ${scheduleRows(d.slots, d.id)}
      <div class="schedule-notice">
        ${toMark ? btn('Подвести итог дня', 'scheduleReviewOpen', { id: d.id }) : ''}
        ${btn('Добавить строку', 'scheduleTap', { id: d.id }, 'sec')}
      </div>
      ${d.sourceUpdated ? `<div class="schedule-notice"><div class="hint">Пост в Telegram изменился. Твои правки и отметки сохранены.</div>
        ${d.telegram ? btn('Посмотреть новую версию', 'scheduleSource', { id: d.id }, 'sec') : ''}</div>` : ''}
      <a class="schedule-source" href="${esc(d.source)}" target="_blank" rel="noopener">Открыть пост в Telegram</a>
    </section>`;
  };
  return { title: 'Расписание из Telegram', html: drafts.length ? drafts.map(day).join('') :
    empty('Черновиков пока нет', '<p class="hint">Опубликуйте в группе расписание одного дня, например с заголовком «Четверг:».</p>') };
};

SCREENS['t.schedule.source'] = async ({ id }) => {
  const source = schedulePlan(id)?.telegram;
  return { title: 'Новая версия из Telegram', html: source ? `
    <section class="card schedule-day"><div class="schedule-head"><h2>${esc(fdate(source.date))}</h2></div>
      ${scheduleRows(source.slots)}</section>
    <div class="schedule-actions">${btn('Применить версию из Telegram', 'scheduleUseSource', { id })}</div>` :
    '<div class="card pad hint">Новой версии пока нет</div>' };
};

ACT.scheduleSource = ({ id }) => go('t.schedule.source', { id });

function scheduleSelect(id, label, selected, options, none) {
  const choices = options.map(o => `<option value="${esc(o.id)}"${o.id === selected ? ' selected' : ''}>${esc(o.name)}</option>`);
  return `<label class="schedule-field"><span class="hint">${label}</span>
    <select class="search" id="${id}"><option value="">${none}</option>${choices.join('')}</select></label>`;
}

async function scheduleOptions() {
  if (!state.ui.scheduleOptions) state.ui.scheduleOptions = await api('/record/options');
  return state.ui.scheduleOptions;
}

ACT.scheduleTap = async ({ id, time }) => {
  const plan = schedulePlan(id);
  if (!plan || state.ui.scheduleSaving || state.ui.scheduleOpening) return;
  const index = time ? plan.slots.findIndex(s => s.time === time) : -1;
  if (time && index < 0) return;
  const slot = plan.slots[index];
  if (slot?.status === 'held') return go('t.lesson', { id: slot.lessonId });
  if (slot?.status === 'cancelled') return sheet(`<h3>${esc(scheduleTitle(slot))}</h3>
    <p class="hint">${esc(time)} · Отменено</p>${btn('Вернуть в план', 'scheduleSingleStatus', { id, time, status: 'planned' })}`);
  if (slot?.status === 'pending') return ACT.scheduleReviewOpen({ id, time });
  state.ui.scheduleOpening = true;
  try {
    state.ui.scheduleEdit = { ...plan, slots: plan.slots.map(s => ({ ...s })), options: await scheduleOptions() };
    scheduleSlotForm(index);
  } catch (e) { toast(errText(e)); } finally { state.ui.scheduleOpening = false; }
};

function scheduleSlotForm(index) {
  const d = scheduleDraft();
  const s = d.slots[index] || { time: '', duration: null, kind: 'individual', studentIds: [], students: [], groupId: null, text: '' };
  const people = new Map(d.options.students.map(p => [p.id, p]));
  (s.studentIds || []).forEach((sid, i) => {
    if (!people.has(sid)) people.set(sid, { id: sid, name: s.students[i] || s.text });
  });
  const options = [...people.values()];
  const kinds = [{ id: 'individual', name: 'Индивидуальное или парное' },
    { id: 'group', name: 'Групповое' }, { id: 'free', name: 'Свободное окно' }];
  sheet(`<h3>${index < 0 ? 'Новая строка' : 'Занятие'}</h3>
    <div class="schedule-fields">
      ${field('schedule-time', 'Начало', s.time, 'type="time" required')}
      ${field('schedule-duration', 'Минуты', s.duration ?? '', 'type="number" min="1" max="1440" inputmode="numeric"')}
    </div>
    ${scheduleSelect('schedule-kind', 'Тип', s.kind, kinds, 'Выбрать тип')}
    <div id="schedule-people">
      ${scheduleSelect('schedule-student-a', 'Ученик', s.studentIds?.[0], options, 'Выбрать ученика')}
      ${scheduleSelect('schedule-student-b', 'Второй ученик', s.studentIds?.[1], options, 'Без пары')}
      <div id="schedule-text-field">${field('schedule-text', 'Имя из расписания', s.kind === 'individual' ? s.text : '', 'maxlength="300" autocomplete="off"')}</div>
    </div>
    <div id="schedule-group-field">
      ${scheduleSelect('schedule-group', 'Группа', s.groupId, d.options.groups, 'Группа без уточнения')}
    </div>
    <div class="schedule-actions">
      ${btn('Сохранить строку', 'scheduleSlotDone', { index })}
      <div id="schedule-row-held">${btn('Состоялось', 'scheduleSlotDone', { index, held: true }, 'sec')}</div>
      ${index < 0 || s.kind === 'free' ? '' : btn('Отменилось', 'scheduleSingleStatus', { id: d.id, time: s.time, status: 'cancelled' }, 'ghost')}
      ${index < 0 ? '' : btn('Убрать строку', 'scheduleSlotRemove', { index }, 'ghost')}
    </div>`);
  const kind = document.getElementById('schedule-kind');
  const update = () => {
    document.getElementById('schedule-people').hidden = kind.value !== 'individual';
    document.getElementById('schedule-group-field').hidden = kind.value !== 'group';
    document.getElementById('schedule-row-held').hidden = kind.value === 'free';
    document.getElementById('schedule-text-field').hidden = Boolean(val('schedule-student-a') || val('schedule-student-b'));
  };
  kind.addEventListener('change', update);
  for (const id of ['schedule-student-a', 'schedule-student-b']) document.getElementById(id).addEventListener('change', update);
  update();
}

async function scheduleWrite(plan, fields) {
  if (state.ui.scheduleSaving) return false;
  state.ui.scheduleSaving = true;
  try {
    await api(`/schedule-drafts/${plan.id}`, { method: 'PUT', body: { revision: plan.revision, ...fields } });
    return true;
  } catch (e) {
    toast(e.data?.message || errText(e));
    return false;
  } finally {
    state.ui.scheduleSaving = false;
  }
}
const saveScheduleRows = (plan, slots, date = plan.date) =>
  scheduleWrite(plan, { date, slots, sourceRevision: plan.sourceRevision });

ACT.scheduleSlotRemove = async ({ index }) => {
  const d = scheduleDraft();
  if (await saveScheduleRows(d, d.slots.filter((_, i) => i !== index))) {
    closeSheet();
    render();
  }
};

ACT.scheduleSlotDone = async ({ index, held }) => {
  const d = scheduleDraft();
  const kind = val('schedule-kind'), time = val('schedule-time');
  const raw = val('schedule-duration'), duration = raw ? Number(raw) : null;
  if (!time || !kind) return toast('Укажи время начала и тип');
  if (duration !== null && (!Number.isInteger(duration) || duration < 1 || duration > 1440)) return toast('Проверь длительность');
  if (d.slots.some((s, i) => i !== index && s.time === time)) return toast(`Время ${time} уже занято в черновике`);
  const studentIds = kind === 'individual' ? [val('schedule-student-a'), val('schedule-student-b')].filter(Boolean) : [];
  if (studentIds.length === 2 && studentIds[0] === studentIds[1]) return toast('Выбери двух разных учеников');
  const groupId = kind === 'group' ? val('schedule-group') || null : null;
  const text = kind === 'individual' ? val('schedule-text').trim() : '';
  if (kind === 'individual' && !studentIds.length && !text) return toast('Выбери ученика или введи имя');
  if (held && (!duration || (kind === 'group' ? !groupId : !studentIds.length))) return toast('Уточни длительность и состав');
  const row = { time, duration, kind, studentIds, groupId, text, suggested: false };
  const slots = d.slots.filter((_, i) => i !== index).concat(row);
  if (!await saveScheduleRows(d, slots)) return;
  closeSheet();
  if (held) return ACT.scheduleReviewOpen({ id: d.id, time });
  toast('Строка сохранена');
  render();
};

ACT.scheduleChangeDate = ({ id }) => {
  const plan = schedulePlan(id);
  sheet(`<h3>Дата расписания</h3>${field('schedule-date', 'Дата', plan.date, 'type="date" required')}
    <div class="schedule-actions">${btn('Сохранить', 'scheduleDateDone', { id })}</div>`);
};
ACT.scheduleDateDone = async ({ id }) => {
  const plan = schedulePlan(id), date = val('schedule-date');
  if (!date) return toast('Укажи дату расписания');
  if (await saveScheduleRows(plan, plan.slots, date)) {
    closeSheet();
    render();
  }
};

ACT.scheduleUseSource = async ({ id }) => {
  const d = schedulePlan(id);
  if (!d?.telegram || !confirm('Обновить незакрытые строки из Telegram? Отметки занятий сохранятся.')) return;
  if (await scheduleWrite(d, { sourceRevision: d.telegram.sourceRevision, useTelegram: true })) {
    back();
    toast('Версия Telegram применена');
  }
};

async function sendScheduleActions(plan, actions) {
  if (state.ui.scheduleSaving) return null;
  state.ui.scheduleSaving = true;
  try {
    return await api(`/schedule-drafts/${plan.id}/attendance`, { method: 'POST',
      body: { revision: plan.revision, sourceRevision: plan.sourceRevision, actions } });
  } catch (e) {
    toast(e.data?.message || errText(e));
    return null;
  } finally { state.ui.scheduleSaving = false; }
}

ACT.scheduleSingleStatus = async ({ id, time, status }) => {
  const plan = schedulePlan(id);
  if (!plan) return;
  const result = await sendScheduleActions(plan, [{ time, status }]);
  if (!result) return;
  const row = result.results[0];
  if (row.error) return toast(row.error);
  closeSheet();
  toast(status === 'cancelled' ? 'Занятие отменено' : 'Занятие возвращено в план');
  render();
};

ACT.scheduleReviewOpen = async ({ id, time }) => {
  if (state.ui.scheduleOpening || state.ui.scheduleSaving) return;
  state.ui.scheduleOpening = true;
  try {
    closeSheet();
    await loadSchedule();
    const plan = schedulePlan(id);
    if (!plan) return toast('Черновик не найден');
    state.ui.scheduleReview = { id, time, options: await scheduleOptions(), choices: {}, errors: {} };
    go('t.schedule.review');
  } catch (e) { toast(errText(e)); } finally { state.ui.scheduleOpening = false; }
};

const scheduleReview = () => state.ui.scheduleReview;
const reviewKey = time => time.replace(':', '');
function scheduleAttendanceFields(slot, group, key, selected, choice) {
  if (!group?.attendance || slot.status === 'pending') return '';
  const chosen = selected?.studentIds || [], tiers = selected?.tiers || {};
  return `<fieldset class="schedule-attendees" id="schedule-attendees-${key}" ${choice === 'held' ? '' : 'hidden'}><legend>Кто был</legend>
    ${group.roster.map(s => `<div class="schedule-attendee"><label>
      <input type="checkbox" data-schedule-present="${key}" value="${esc(s.id)}"${chosen.includes(s.id) ? ' checked' : ''}> ${esc(s.name)}</label>
      ${group.fixedPrice ? '' : `<select class="search" data-schedule-tier="${esc(s.id)}" aria-label="Тариф · ${esc(s.name)}">
        ${[['', 'По карточке'], ['full', 'Полное'], ...(group.priceShort ? [['short', 'Короткое']] : []), ['trial', 'Пробное']]
          .map(([v, label]) => `<option value="${esc(v)}"${tiers[s.id] === v ? ' selected' : ''}>${label}</option>`).join('')}
      </select>`}</div>`).join('') || '<p class="hint">В группе нет учеников</p>'}</fieldset>`;
}

SCREENS['t.schedule.review'] = async () => {
  const review = scheduleReview(), plan = schedulePlan(review.id);
  const slots = (plan?.slots || []).filter(s => s.kind !== 'free' && (!review.time || review.time === s.time)
    && (!s.status || s.status === 'planned' || s.status === 'pending'));
  const rows = slots.map(s => {
    const key = reviewKey(s.time), error = review.errors[s.time], selected = review.choices[s.time];
    const choice = selected?.status || (review.time || s.status === 'pending' ? 'held' : '');
    const group = review.options.groups.find(g => g.id === s.groupId);
    const details = !s.duration ? 'Уточни длительность в черновике' :
      s.kind === 'group' && !s.groupId ? 'Выбери группу в черновике' :
      s.kind === 'individual' && !s.studentIds?.length ? 'Выбери состав в черновике' : '';
    return `<section class="card schedule-review-row">
      <div class="schedule-slot">${scheduleTime(s)}<div class="schedule-slot-text"><strong>${esc(scheduleTitle(s))}</strong>
        <span class="hint">${esc(details || `${s.duration} мин`)}</span></div></div>
      <div class="schedule-choice">
        <label class="schedule-field"><span class="hint">Итог занятия</span>
          <select class="search" data-schedule-choice="${esc(s.time)}" id="schedule-choice-${key}">
            ${s.status === 'pending' ? '<option value="held">Проверить запись</option>' :
              [[ '', 'Оставить в плане' ], [ 'held', 'Состоялось' ], [ 'cancelled', 'Отменилось' ]]
                .map(([v, label]) => `<option value="${v}"${choice === v ? ' selected' : ''}>${label}</option>`).join('')}
          </select></label>
        ${scheduleAttendanceFields(s, group, key, selected, choice)}
        ${error ? `<p class="hint schedule-review-error" role="alert">${esc(error.error)}</p>
          ${(error.matches || []).map(m => btn(`Связать · ${m.durationMin} мин · внесено в ${esc(m.recordedAt.slice(-8,-3))}`,
            'scheduleResolve', { time: s.time, lessonId: m.id }, 'sec')).join('')}
          ${['existing','same_day'].includes(error.code) ? btn('Записать ещё одно занятие', 'scheduleResolve', { time: s.time, allowNew: true }, 'ghost') : ''}` : ''}
      </div></section>`;
  });
  return { title: review.time ? 'Отметить занятие' : 'Подвести итог дня', html: slots.length ?
    `${rows.join('')}<div class="schedule-actions">${btn('Сохранить итог', 'scheduleReviewSave')}</div>` :
    '<div class="card pad hint">Все выбранные занятия отмечены.</div>' };
};

document.addEventListener('change', event => {
  const select = event.target.closest?.('[data-schedule-choice]');
  if (!select) return;
  const box = document.getElementById(`schedule-attendees-${reviewKey(select.dataset.scheduleChoice)}`);
  if (box) box.hidden = select.value !== 'held';
});

function scheduleReviewActions() {
  return [...document.querySelectorAll('[data-schedule-choice]')].filter(el => el.value).map(el => {
    const time = el.dataset.scheduleChoice, key = reviewKey(time);
    const selected = [...document.querySelectorAll(`[data-schedule-present="${key}"]:checked`)];
    const studentIds = selected.map(x => x.value);
    const tiers = Object.fromEntries(selected.map(x => [x.value, x.closest('.schedule-attendee').querySelector('select')?.value])
      .filter(([, v]) => v));
    return { time, status: el.value, studentIds, tiers, attendanceConfirmed: studentIds.length > 0 };
  });
}

async function saveScheduleReview(actions) {
  const review = scheduleReview(), plan = schedulePlan(review.id);
  if (!actions.length) return toast('Выбери хотя бы одно занятие');
  review.choices = { ...review.choices, ...Object.fromEntries(actions.map(a => [a.time, a])) };
  const response = await sendScheduleActions(plan, actions);
  if (!response) return;
  for (const row of response.results) {
    if (row.error) review.errors[row.time] = row;
    else delete review.errors[row.time];
  }
  const errors = response.results.filter(r => r.error);
  if (!errors.length && !Object.keys(review.errors).length) {
    back();
    toast('Итог сохранён');
    return;
  }
  try {
    await loadSchedule();
    render();
  } catch (e) { toast(errText(e)); }
}

ACT.scheduleReviewSave = () => saveScheduleReview(scheduleReviewActions());
ACT.scheduleResolve = ({ time, lessonId, allowNew }) => {
  const action = scheduleReviewActions().find(a => a.time === time);
  if (!action || action.status !== 'held') return toast('Выбери «Состоялось»');
  if (allowNew && !confirm('Записать ещё одно проведённое занятие в журнал и в счёт?')) return;
  return saveScheduleReview([{ ...action, lessonId, allowNew }]);
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
  // чипы: сегодня, вчера, текущий и прошлый месяц — занятия задним числом видны без календаря
  const cur = d0.slice(0, 7), prevD = new Date(today.getFullYear(), today.getMonth() - 1, 1), prev = `${prevD.getFullYear()}-${String(prevD.getMonth() + 1).padStart(2, '0')}`;
  const chips = [[d0, 'Сегодня'], [d1, 'Вчера'], [cur, MON_NOM[+cur.slice(5) - 1]], [prev, MON_NOM[+prev.slice(5) - 1]]];
  if (![cur, prev, d0, d1].includes(k)) chips.push([ym, MON_NOM[+ym.slice(5) - 1]]);
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
      lead: initials(a.name), t: esc(a.name), s: l.direct ? 'Оплата напрямую педагогу' : (T_PAY[a.payStatus] ? T_PAY[a.payStatus][0] : ''),
      r: a.amount === null ? '' : a.amount ? `<b class="${l.direct ? 'direct' : ''}">${fmt(a.amount)}</b>` : esc(l.freeLabel || 'абонемент'),
      go: 't.student', p: { id: a.studentId },
    })))}` : '<div class="empty">Посещаемость не отмечалась</div>'}
    <div class="card" style="margin-top:10px">${l.direct
      ? `<div class="total"><span>Платят родители напрямую</span><span class="big direct">${fmt(l.directAmount)}</span></div>
         ${l.rent ? `<div class="total"><span>Аренда зала школе</span><span class="big">${fmt(l.rent)}</span></div>` : ''}
         ${l.rent ? `<div class="total"><span>Остаётся вам</span><span class="big direct">${fmt(l.directAmount - l.rent)}</span></div>` : ''}
         <div class="pad hint" style="padding-top:0">Оплата педагогу напрямую, в счёт школы не входит.</div>`
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

/* Строка состава: имя ведёт в карточку, «✖» — «Ушёл из группы» (только без долгов; решение владельца 02.10.2026).
   Это div: кнопка внутри кнопки-cell ломает разметку. */
const tMemberRow = (g, gid, s) => `<div class="cell"><span class="lead${payRosterLead(g.pay, s.id) ? ' plain' : ''}">${payRosterLead(g.pay, s.id) || initials(s.name)}</span><span data-go="t.student" data-p='${esc(JSON.stringify({ id: s.id }))}'><div class="t">${esc(s.name)}</div><div class="s">${[payRosterNote(g.pay, s.id), g.kindergarten ? (s.kgroup ? `🏫 гр. ${esc(s.kgroup)}` : '🏫 группа в саду не указана') : s.partnerId ? 'в паре' : 'солист'].filter(Boolean).join(' · ')}</div></span><span class="r"><button class="chip" style="padding:2px 8px" data-act="tLeaveAsk" data-p='${esc(JSON.stringify({ gid, sid: s.id, name: s.name, group: g.name, options: g.leaveOptions || [], mode: g.mode }))}' aria-label="Ушёл из группы">✖</button></span></div>`;
/* «Добавить ученика»: сначала поиск по базе (чтобы не завести дубль), затем существующий или новая карточка. */
ACT.tAddAsk = ({ gid, group }) => sheet(`<h3>Добавить ученика</h3><div class="hint">${esc(plainName(group))}. Введите фамилию и имя — сначала проверим, нет ли ученика в базе. Администратор получит сообщение.</div>
  ${field('add-n', 'Фамилия и имя', '', 'placeholder="Иванова Мария" autocomplete="off"')}
  <div id="add-res"></div>
  <div style="margin-top:12px">${btn('🔎 Найти', 'tAddFind', { gid })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
ACT.tAddFind = async ({ gid }) => {
  const name = val('add-n').trim().replace(/\s+/g, ' ');
  if (name.length < 3) { toast('Введите фамилию и имя'); return; }
  try {
    const d = await api(`/students/search?q=${encodeURIComponent(name.split(' ')[0])}`);
    document.getElementById('add-res').innerHTML = (d.students.length
      ? `<div class="hint" style="margin:10px 0 4px">Уже есть в базе — выберите, если это он:</div>${d.students.map(s => btn(`${esc(s.name)} · ${s.groups.length ? esc(s.groups.map(plainName).join(', ')) : 'без группы'}`, 'tAddDo', { gid, studentId: s.id }, 'ghost')).join('')}`
      : '<div class="hint" style="margin:10px 0 4px">В базе такого ученика нет.</div>')
      + btn(`➕ Создать новую карточку «${esc(name)}»`, 'tAddDo', { gid, name }, 'sec');
  } catch (e) { toast(errText(e)); }
};
ACT.tAddDo = async ({ gid, studentId, name, force }) => {
  try { const r = await api(`/groups/${gid}/members`, { method: 'POST', body: studentId ? { studentId } : { name, force: !!force } }); closeSheet(); render(); toast(r.created ? `Добавлен новый ученик: ${r.name}` : `${r.name} — в группе`); }
  catch (e) {
    if (e.code === 'duplicate' && e.data && e.data.students) {        // тёзка уже в базе — добавить его, не новую карточку
      closeSheet();
      sheet(`<h3>Такой ученик уже есть</h3><div class="hint">Чтобы не было двух карточек одного ребёнка, добавьте в группу существующего. Новую карточку создавайте, только если это точно другой ребёнок.</div>
        ${e.data.students.map(s => btn(`➕ Добавить ${esc(s.name)} · ${s.groups && s.groups.length ? esc(s.groups.map(plainName).join(', ')) : 'без группы'}`, 'tAddDo', { gid, studentId: s.id })).join('')}
        <div style="margin-top:12px">${btn(`Это другой ребёнок — создать «${esc(name)}»`, 'tAddDo', { gid, name, force: true }, 'ghost')}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
      return;
    }
    toast(e.data && e.data.message ? e.data.message : errText(e));
  }
};
/* Группа ребёнка в детском саду — номер или название; заполняет педагог или родитель (решение владельца 10.10.2026). */
ACT.tKgAsk = ({ sid, name, value }) => sheet(`<h3>Группа в детском саду</h3><div class="hint">${esc(name)}. Номер или название группы в саду. Пусто — стереть.</div>
  ${field('t-kg', 'Номер или название', value || '', 'maxlength="40" autocomplete="off" placeholder="например, 7 или «Солнышко»"')}
  <div style="margin-top:12px">${btn('💾 Сохранить', 'tKgDo', { sid })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
ACT.tKgDo = async ({ sid }) => {
  try {
    const r = await api(`/students/${sid}/kgroup`, { method: 'PUT', body: { value: val('t-kg') } });
    closeSheet(); render(); toast(r.value ? `Группа в саду: ${r.value}` : 'Группа в саду стёрта');
  } catch (e) { toast(e.data && e.data.message ? e.data.message : errText(e)); }
};
/* Исправить фамилию и имя ученика своих групп (решение владельца 07.10.2026). */
ACT.tRenameAsk = ({ sid, name }) => sheet(`<h3>Изменить имя ученика</h3>${field('t-rn', 'Фамилия и имя', name, 'maxlength="60" autocomplete="off"')}
  <p class="hint">Сначала фамилия, потом имя. В уже записанных индивидуальных занятиях останется старое написание. Администратор получит сообщение.</p>
  <div style="margin-top:12px">${btn('💾 Сохранить', 'tRenameDo', { sid })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
ACT.tRenameDo = async ({ sid, force }) => {
  const name = val('t-rn').trim().replace(/\s+/g, ' ');
  if (name.length < 3) { toast('Введите фамилию и имя'); return; }
  try {
    const r = await api(`/students/${sid}`, { method: 'PATCH', body: { name, force: !!force } });
    closeSheet(); render(); toast(r.unchanged ? 'Имя не изменилось' : 'Имя исправлено');
  } catch (e) {
    if (e.code === 'duplicate' && confirm(`Ученик «${e.data.students.map(x => x.name).join('», «')}» уже есть в базе. Всё равно сохранить такое имя?`)) { ACT.tRenameDo({ sid, force: true }); return; }
    if (e.code !== 'duplicate') toast(e.data && e.data.message ? e.data.message : errText(e));
  }
};
/* «🔄 Перевести в другую группу» (решение владельца 16.10.2026): из своей группы в свою одним действием —
   добавить в новую (абонемент с этого месяца) и убрать из старой (абонемент — с выбранного месяца). */
ACT.tMoveAsk = async ({ sid, name }) => {
  try {
    const d = await api(`/students/${sid}/transfer`);
    if (!d.to.length) { toast('Переводить некуда: других ваших групп нет'); return; }
    state.ui.tMove = { sid, name, d, from: d.from[0].id, to: d.to.length === 1 ? d.to[0].id : '', left: d.leaveOptions[0].ym };
    tMoveSheet();
  } catch (e) { toast(errText(e)); }
};
const tMoveSheet = () => {
  const m = state.ui.tMove, d = m.d;
  const from = d.from.find(g => g.id === m.from) || d.from[0];
  const chips = (items, key, cur, label) => `<div class="chips">${items.map(x => `<button class="chip" aria-pressed="${x[0] === cur}" data-act="tMovePick" data-p='${esc(JSON.stringify({ [key]: x[0] }))}'>${esc(label(x[1]))}</button>`).join('')}</div>`;
  // при выборе чипа лист перерисовывается на месте (sheet() добавил бы второй поверх)
  const open = document.querySelector('.sheet-wrap .sheet');
  const show = html => { if (open) open.innerHTML = `<div class="grab"></div>${html}`; else sheet(html); };
  show(`<h3>Перевести в другую группу</h3><div class="hint">${esc(m.name)}. Ученик добавится в новую группу с этого месяца и уйдёт из старой; история занятий и оплат сохранится. Администратор получит сообщение.</div>
    ${d.from.length > 1 ? `<div class="eyebrow">Из какой группы</div>${chips(d.from.map(g => [g.id, g.name]), 'from', m.from, plainName)}` : `<div class="hint" style="margin-top:10px">Из: <b>${esc(plainName(from.name))}</b></div>`}
    <div class="eyebrow">В какую группу</div>${chips(d.to.map(g => [g.id, g.name]), 'to', m.to, plainName)}
    ${from.subscription ? `<div class="eyebrow">Уходит из старой</div>${chips(d.leaveOptions.map(o => [o.ym, o.label]), 'left', m.left, x => x)}` : ''}
    <div style="margin-top:12px">${btn('🔄 Перевести', 'tMoveDo', {}, m.to ? '' : 'sec')}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
};
ACT.tMovePick = p => { Object.assign(state.ui.tMove, p); tMoveSheet(); };
ACT.tMoveDo = async () => {
  const m = state.ui.tMove;
  if (!m || !m.to) { toast('Выберите группу'); return; }
  try {
    const r = await api(`/students/${m.sid}/transfer`, { method: 'POST', body: { fromGroupId: m.from, toGroupId: m.to, leftPeriod: m.left } });
    state.ui.tMove = null; closeSheet(); render();
    toast(`Переведён в «${plainName(r.toName)}»${r.result === 'marked' ? ` · из старой с ${fmon(r.leftPeriod).toLowerCase()}` : ''}`);
  } catch (e) { toast(e.data && e.data.message ? e.data.message : errText(e)); }
};
// «Удалить ученика» у педагога — тот же архив, что у администратора (решение владельца 21.10.2026)
ACT.tArchiveAsk = async ({ sid, name }) => {
  let p;
  try { p = await api(`/students/${sid}/archive`); } catch (e) { toast(errText(e)); return; }
  sheet(archiveSheetHtml(p, name, 'tArchiveDo', { sid }));
};
ACT.tArchiveDo = async ({ sid, confirmDebt }) => {
  try {
    const r = await api(`/students/${sid}/archive`, { method: 'POST', body: { confirmDebt: !!confirmDebt } });
    closeSheet(); back();                                   // ученик больше не виден педагогу
    toast(`${r.name} ушёл из школы${r.debt.total ? `, неоплачено ${fmt(r.debt.total)}` : ''}`);
  } catch (e) { closeSheet(); toast(e.data && e.data.message ? e.data.message : errText(e)); }
};
ACT.tLeaveAsk = ({ gid, sid, name, group, options, mode }) => sheet(`<h3>Ушёл из группы?</h3><div class="hint">${esc(name)} · ${esc(plainName(group))}. Карточка ученика сохранится, история занятий и оплат тоже. Если есть долг, он останется за учеником. Администратор получит сообщение.</div>
  <div style="margin-top:12px">${mode === 'subscription'
    ? options.map(o => btn(o.label, 'tLeaveDo', { gid, sid, ym: o.ym }, 'sec')).join('')
    : btn('✖ Убрать из группы', 'tLeaveDo', { gid, sid, ym: (options[0] || {}).ym }, 'sec')}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
ACT.tLeaveDo = async ({ gid, sid, ym }) => {
  try { const r = await api(`/groups/${gid}/members/${sid}/leave`, { method: 'PUT', body: { leftPeriod: ym } }); closeSheet(); render(); toast(r.result === 'marked' ? `Ученик ушёл с ${fmon(r.leftPeriod)}` : 'Ученик убран из группы'); }
  catch (e) { closeSheet(); toast(e.data && e.data.message ? e.data.message : errText(e)); }
};
SCREENS['t.group'] = async ({ id, ym }) => {
  const g = await api(`/groups/${id}`);
  const tab = state.ui.tGroupTab || 'all';
  if (tab === 'pay' && state.me.canBill) return tGroupPay(g, id, ym);
  const rows = tab === 'pairs'
    ? (g.pairs.length ? list(g.pairs.map(p => cell({ lead: '💃', plain: true, t: `${esc(p.aName)} ↔ ${esc(p.bName)}`, go: 't.student', p: { id: p.aId } }))) : '<div class="empty">Пар нет</div>')
    : tab === 'solo'
      ? (g.soloists.length ? list(g.soloists.map(s => cell({ lead: initials(s.name), t: esc(s.name), go: 't.student', p: { id: s.id } }))) : '<div class="empty">Солистов нет</div>')
      : (g.students.length ? list(g.students.map(s => tMemberRow(g, id, s))) : '<div class="empty">В группе никого нет</div>')
        + `<div style="margin-top:12px">${btn('🔗 Пригласить родителей', 'inviteOpen', { gid: id })}${goBtn('📋 Домашнее задание', 't.hw.new', { gid: id }, 'sec')}${goBtn('📒 Домашние задания и оценки', 't.hw', { gid: id }, 'sec')}${btn('➕ Добавить ученика', 'tAddAsk', { gid: id, group: g.name }, 'sec')}</div>`;
  return { title: g.name, html: `
    <div class="card pad"><div style="font-weight:800;font-size:16px">${esc(g.name)}</div><div class="hint">${MODE[g.mode] || g.mode}${g.mode === 'per_visit' ? ` · ${fmt(g.priceFull)} за посещение` : g.mode === 'subscription' ? ` · ${fmt(g.priceFull)} в месяц` : ''}</div>${payRosterLine(g.pay) ? `<div class="hint" style="margin-top:4px">${payRosterLine(g.pay)}</div>` : ''}</div>
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
  // статус оплаты у каждого ученика — как на экране неоплат: ✅ оплачено · 🟡 частично · ⬜ не оплачено
  const billed = d.students.filter(s => s.total > 0), paidAll = billed.filter(s => !s.rest).length;
  const lead = s => !s.total ? '—' : !s.rest ? '✅' : s.paid ? '🟡' : '⬜';
  const note = s => (!s.total ? 'начислений нет' : !s.rest ? `оплачено ${fmt(s.paid)}` : s.paid ? `оплачено ${fmt(s.paid)} · остаток ${fmt(s.rest)}` : `не оплачено · ${fmt(s.rest)}`) + (s.hasParent ? '' : ' · родитель не в боте');
  const sorted = [...d.students].sort((a, b) => (b.rest || 0) - (a.rest || 0) || a.name.localeCompare(b.name));
  return { title: g.name, html: `
    <div class="card pad"><div style="font-weight:800;font-size:16px">${esc(g.name)}</div>
      <div class="hint">${fmon(period)} · начислено ${fmt(d.students.reduce((a, s) => a + s.total, 0))}${rest ? ` · к оплате ${fmt(rest)}` : ' · всё оплачено'}${billed.length ? ` · оплатили ${paidAll} из ${billed.length}` : ''}</div></div>
    ${chipsAct('tGroupTab', 'pay', [['all', `Состав (${g.students.length})`], ['pairs', `Пары (${g.pairs.length})`], ['solo', `Солисты (${g.soloists.length})`], ['pay', '💳 Оплата']])}
    ${monthChips('t.group', period, { id })}
    ${sorted.length ? list(sorted.map(s => cell({
      lead: lead(s), plain: true, t: esc(s.name), s: note(s),
      r: `<b class="${s.rest ? 'money bad' : ''}">${fmt(s.rest || s.total)}</b>`, go: 't.bill', p: { sid: s.id, ym: period },
    }))) : '<div class="empty">В группе никого нет</div>'}
    <div style="margin-top:12px">${btn(`📨 Отправить счета всей группе (${toSend})`, 'tBillGroupAsk', { gid: id, ym: period, count: toSend, name: g.name }, toSend ? '' : 'ghost')}</div>` };
}

/* «Сколько должен ученик всего» (педагог со счетами, решение владельца 06.11.2026): итог и остаток по месяцам
   с начала истории родителя, свежие сверху; тап по месяцу — счёт с той же суммой. */
const tDebtBlock = (d, sid) => `<div class="eyebrow">Оплата</div>
  <div class="card pad"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><div style="font-weight:700">${d.total ? 'Не оплачено всего' : 'Всё оплачено'}</div><div class="money ${d.total ? 'bad' : ''}" style="font-size:20px;font-weight:800">${d.total ? fmt(d.total) : '✓'}</div></div>
  <div class="hint">по вашим направлениям за все месяцы · тап по месяцу — счёт</div></div>
  ${d.months.length ? list(d.months.map(m => cell({ lead: m.rest ? (m.paid ? '🟡' : '⬜') : '✅', plain: true, t: monthLabel(m.ym), s: `начислено ${fmt(m.total)}${m.paid ? ` · оплачено ${fmt(m.paid)}` : ''}`, r: m.rest ? `<b class="money bad">${fmt(m.rest)}</b>` : '', go: 't.bill', p: { sid, ym: m.ym } }))) : '<div class="empty">Начислений ещё нет</div>'}`;
SCREENS['t.student'] = async ({ id, ym }) => {
  const s = await api(`/students/${id}${ym ? `?ym=${ym}` : ''}`);
  return { title: s.name, html: `
    <div class="card pad"><div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start"><div><div style="font-weight:800;font-size:16px">${esc(s.name)}</div>
      <div class="hint">${s.groups.length ? esc(s.groups.join(', ')) : 'без группы'}${s.partner ? ` · пара: ${esc(s.partner.name)}` : ''}</div>
      ${s.kindergarten ? `<div class="hint" style="margin-top:4px">🏫 Группа в саду: <b>${s.kgroup ? esc(s.kgroup) : 'не указана'}</b> <button class="chip" style="padding:1px 8px;margin-left:4px" data-act="tKgAsk" data-p='${esc(JSON.stringify({ sid: id, name: s.name, value: s.kgroup || '' }))}' aria-label="Группа в саду">✏️</button></div>` : ''}${birthRow(id, s.name, s.birth)}${danceRow(id, s.name, s.dance)}</div>
      <button class="chip" style="padding:4px 10px;white-space:nowrap" data-act="tRenameAsk" data-p='${esc(JSON.stringify({ sid: id, name: s.name }))}'>✏️ Имя</button></div></div>
    ${s.debt ? tDebtBlock(s.debt, id) : ''}
    ${(s.tariffs || []).length ? `<div class="eyebrow">Абонемент</div>${list(s.tariffs.map(g => `<div class="cell static"><span class="lead plain">💃</span><span><div class="t">${esc(g.name)}</div><div class="s">${fmt(g.freq.times === 2 ? g.freq.priceTwice : g.freq.priceThrice)} в месяц${g.freq.times === 2 && g.freq.since ? ` с ${monthLabel(g.freq.since).toLowerCase()}` : ''}</div>
      <div class="chips" style="margin:6px 0 0">${[2, 3].map(n => `<button class="chip" aria-pressed="${g.freq.times === n}" data-act="freqAsk" data-p='${esc(JSON.stringify({ sid: id, gid: g.id, times: n, name: g.name, price: n === 2 ? g.freq.priceTwice : g.freq.priceThrice, cur: g.freq.times }))}'>${n} раза в неделю</button>`).join('')}</div></span><span></span></div>`))}` : ''}
    ${(s.subscriptions || []).length ? `<div class="eyebrow">Абонемент за ${MON_NOM[+s.period.slice(5) - 1].toLowerCase()}</div>${list(s.subscriptions.map(x => `<div class="cell static"><span class="lead plain">💳</span><span><div class="t">${esc(x.name)}</div><div class="s">начислено ${fmt(x.total)}${x.paid ? ` · оплачено ${fmt(x.paid)}` : ''}</div></span><span class="r"><button class="chip" style="padding:4px 10px" data-act="tSubAsk" data-p='${esc(JSON.stringify({ sid: id, ym: s.period, gid: x.groupId, total: x.total, paid: x.paid, student: s.name }))}' aria-label="Абонемент за этот месяц">✏️</button></span></div>`))}<p class="hint" style="margin-top:6px">Пришёл не с начала месяца — поставьте сумму за этот месяц. Месяц меняется чипами ниже.</p>` : ''}
    ${compBlock(s.competitions)}
    <div style="margin-top:10px">${list([cell({ lead: '📅', plain: true, t: 'Все занятия ученика', s: state.me.canBill ? 'по месяцам: кто вёл, группа, сумма, оплата' : 'по месяцам: кто вёл, группа', go: 'a.student.lessons', p: { id, name: s.name } })])}</div>
    <div class="eyebrow">Мои занятия с учеником</div>
    ${monthChips('t.student', s.period, { id })}
    ${s.lessons.length ? list(s.lessons.map(l => tLessonCell(l, true))) : '<div class="empty">В этом месяце занятий не было</div>'}
    ${(s.groupIds || []).length ? `<div style="margin-top:12px">${goBtn('📋 Домашнее задание', 't.hw.new', { gid: s.groupIds[0], only: id }, 'sec')}${btn('🔄 Перевести в другую группу', 'tMoveAsk', { sid: id, name: s.name }, 'sec')}${btn('🗑 Удалить ученика', 'tArchiveAsk', { sid: id, name: s.name }, 'danger')}</div>` : ''}
    ${state.me.canBill ? `<div style="margin-top:12px">${goBtn(`🧾 Счёт за ${MON_NOM[+(s.period).slice(5) - 1].toLowerCase()}`, 't.bill', { sid: id, ym: s.period }, 'sec')}</div>` : ''}` };
};

/* ── Зарплата и сдача периода ────────────────────────────────────────── */
const T_LINE_ICON = { shift: '🕒', override: '✍️', in_shift: '↳', in_share: '↳', share: '💯', lesson: '📘', bonus: '🎁' };
/* Журнал зарплаты по дням: строки «в смене» / «в проценте» схлопнуты в строку смены / процента (foldSalaryLines в app.js). */
const tSalaryCells = lines => {
  const f = foldSalaryLines(lines);
  return f.rest.map(x => cell({
    lead: T_LINE_ICON[x.kind] || '📘', plain: true, t: `${fdate(x.date)}${x.label ? ` · ${esc(x.label)}` : ''}`, cls: x.kind === 'shift' ? 'wrap' : '',
    s: x.kind === 'bonus' ? 'премия сверх зарплаты' : x.kind === 'shift' ? (f.inShift[x.date] || []).join(' · ') || 'смена'
      : x.kind === 'share' ? `оплата группы за месяц${f.inShare[x.groupId || ''] ? ` · ${plural(f.inShare[x.groupId || ''], ['занятие', 'занятия', 'занятий'])}` : ''}` : x.minutes ? `${x.minutes} мин` : '',
    r: `<b>${fmt(x.amount)}</b>`, ...(x.lessonId ? { go: 't.lesson', p: { id: x.lessonId } } : {}),
  }));
};
/* Аренда зала школе (прямая оплата): сумма месяца и, с месяца начала учёта (HALL_RENT_SETTLE_SINCE_PERIOD),
   статус — педагог перечисляет её школе одной суммой в конце месяца (решение владельца 27.10.2026). */
const tRentBlock = (r, period) => {
  if (!r || (!r.accrued && !r.paid)) return '';
  const mon = MON_NOM[+period.slice(5) - 1].toLowerCase(), ended = period < lastPeriods(1)[0];
  const status = !r.settle ? '' : r.status === 'paid' ? pill(`✅ получена${r.payments.length ? ' ' + fdate(r.payments[r.payments.length - 1].date) : ''}`, 'ok')
    : r.status === 'partial' ? pill(`получено ${fmt(r.paid)} · остаток ${fmt(r.rest)}`, 'warn')
      : pill(ended ? '⚠️ не оплачена' : 'к оплате в конце месяца', ended ? 'bad' : 'warn');
  return `<div class="eyebrow">Аренда зала школе</div>
    <div class="card">
      <div class="total"><span>За ${mon} · ${plural(r.lessons, ['занятие', 'занятия', 'занятий'])}</span><span class="big">${fmt(r.accrued)}</span></div>
      ${status ? `<div class="pad" style="padding-top:0">${status}</div>` : ''}
      <div class="pad hint" style="padding-top:0">${r.settle ? `${ended ? 'Итог за месяц. ' : 'Сумма увеличивается по мере проведения занятий. '}Перечислите школе одной суммой в конце месяца — администратор отметит получение.` : 'Справочно: ставка за каждый начатый час индивидуального занятия.'}</div>
    </div>
    ${r.payments.length ? list(r.payments.map(p => cell({ lead: '🏠', plain: true, t: `получено ${fmt(p.amount)}`, s: `${fdate(p.date)}${p.comment ? ' · ' + esc(p.comment) : ''}` }))) : ''}`;
};
// Выплаты школы за месяц (решение владельца 11.11.2026): сколько уже выплачено — аванс, остаток, премии.
// Подпись «Аванс», пока сумма выплат меньше начисленного, дальше «Выплата»; парная выплата премии — «Премия».
const tPayoutsBlock = (po, period, total) => {
  if (!po) return '';
  const mon = MON_NOM[+period.slice(5) - 1].toLowerCase();
  let run = 0;
  const rows = po.items.map(p => {
    run += p.amount;
    const t = p.bonus ? 'Премия' : run < total ? 'Аванс' : 'Выплата';
    return cell({ lead: p.bonus ? '🎁' : '💸', plain: true, t, s: fdate(p.date), r: `<b>${fmt(p.amount)}</b>` });
  });
  return `<div class="eyebrow">Выплаты школы</div>
    <div class="card">
      <div class="total"><span>Выплачено за ${mon}</span><span class="big">${fmt(po.paid)}</span></div>
      ${po.paid && po.rest ? `<div class="total"><span>Остаток к выплате</span><span class="big">${fmt(po.rest)}</span></div>` : ''}
      ${!po.paid ? `<div class="pad hint" style="padding-top:0">Выплат за ${mon} ещё не было.</div>` : ''}
    </div>
    ${rows.length ? list(rows) : ''}`;
};
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
    ${tPayoutsBlock(st.payouts, period, st.total)}
    ${!canSubmitPeriod ? '' : `<div style="margin-top:12px">${sub.submitted
      ? '<div class="card pad hint">Период сдан: занятия этого месяца больше не редактируются. Открыть его может администратор.</div>'
      : btn(sub.canSubmit ? `📤 Сдать ${MON_NOM[+period.slice(5) - 1].toLowerCase()} (${plural(sub.lessons, ['занятие', 'занятия', 'занятий'])}, ${fmt(sub.total)})` : `Сдать период можно с 25 ${MON_SHORT[+period.slice(5) - 1]}`,
        'tSubmitAsk', { ym: period, lessons: sub.lessons, total: sub.total }, sub.canSubmit ? '' : 'ghost')}</div>`}
    ${salarySummary((st.summary || []).filter(r => r.kind !== 'direct'), 't.lesson')}
    ${paid.length ? `<details class="acc" style="margin-top:10px">
      <summary><span><span class="chev">›</span>📅 По дням <span class="hint">· ${plural(foldSalaryLines(paid).rest.length, ['строка', 'строки', 'строк'])}</span></span></summary>
      <div class="accbody">${tSalaryCells(paid).join('')}${d && d.lessons ? cell({
        lead: '🤝', plain: true, t: 'Индивидуальные — прямая оплата',
        s: `${plural(d.lessons, ['занятие', 'занятия', 'занятий'])} · школа не начисляет`,
        r: '<b>0 ₽</b>', go: 't.lessons', p: { key: period },
      }) : ''}</div></details>` : d ? '' : empty('Начислений нет', '<p class="hint" style="margin:0">Отметьте занятия — они появятся здесь</p>')}
    ${d ? `<div class="eyebrow">Прямая оплата</div>
      <div class="card">
        <div class="total"><span>Родители платят вам за ${MON_NOM[+period.slice(5) - 1].toLowerCase()}</span><span class="big direct">${fmt(d.total)}</span></div>
        <div class="pad hint" style="padding-top:0">Школа эти занятия не начисляет и оплату по ним не отслеживает — суммы справочные.</div>
      </div>
      ${d.students.length ? list(d.students.map(x => cell({
        lead: initials(x.name), t: esc(x.name), s: `${plural(x.lessons, ['занятие', 'занятия', 'занятий'])}${x.rent ? ` · аренда ${fmt(x.rent)}` : ''}`,
        r: `<b class="direct">${fmt(x.amount)}</b>`, go: 't.student', p: { id: x.id, ym: period },
      }))) : ''}
      ${tRentBlock(st.rent, period)}` : ''}` };
};

/* ── Дневники спортсменов ────────────────────────────────────────────── */
SCREENS['t.diary'] = async () => {
  const d = await api('/diary');
  return { title: 'Дневники', html: `
    <div class="chips"><button class="chip" data-go="t.hw" data-p='{}'>📋 Домашние задания</button><button class="chip" data-go="t.rating" data-p='${esc(JSON.stringify({ ym: d.period }))}'>🏆 Рейтинг</button></div>
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
    ${d.openTasks.length ? list(d.openTasks.map(t => cell({ lead: '📋', plain: true, cls: 'wrap', t: esc(t.exercise), s: [t.minutes ? `${t.minutes} мин` : '', t.comment ? esc(t.comment) : '', t.videos ? `📹 видео: ${t.videos}` : ''].filter(Boolean).join(' · '), r: t.videos && t.mine ? `<button class="chip" data-act="hwVideoOpen" data-p='${esc(JSON.stringify({ tid: t.id }))}'>📹 Видео</button>` : '' }))) : '<div class="empty">Открытых заданий нет</div>'}
    <div style="margin-top:12px">${btn('➕ Выдать задание', 'tTaskForm', { sid: id })}${goBtn('📋 Все задания', 't.diary.tasks', { sid: id }, 'sec')}</div>` };
};

SCREENS['t.diary.tasks'] = async ({ sid }) => {
  const d = await api(`/diary/${sid}/tasks`);
  return { title: 'Задания', html: `
    <div class="h2">${esc(d.student.name)}</div>
    ${d.tasks.length ? list(d.tasks.map(t => cell({
      lead: t.status === 'open' ? '📋' : '✅', plain: true, cls: 'wrap', t: esc(t.exercise),
      s: [t.minutes ? `${t.minutes} мин` : '', t.comment ? esc(t.comment) : '', t.doneTimes ? `сделано ${t.doneTimes} раз${t.lastDone ? `, последний ${fdate(t.lastDone)}` : ''}` : '', t.videos ? `📹 видео: ${t.videos}` : ''].filter(Boolean).join(' · '),
      r: `${t.videos && t.mine ? `<button class="chip" data-act="hwVideoOpen" data-p='${esc(JSON.stringify({ tid: t.id }))}'>📹 Видео</button> ` : ''}${t.status === 'open' ? `<button class="chip" data-act="tTaskClose" data-p='${esc(JSON.stringify({ id: t.id, sid }))}'>Закрыть</button>` : pill('закрыто', 'mute')}`,
    }))) : '<div class="empty">Заданий нет</div>'}
    ${d.tasks.some(t => t.videos) ? '<p class="hint" style="margin-top:8px">📹 — ученик прислал видео выполнения: оно пришло вам в чат с ботом, «📹 Видео» присылает его снова.</p>' : ''}
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

/* ── Домашние задания (решение владельца 15.10.2026) ────────────────────
   Задание всей группе, нескольким ученикам или одному (из карточки ученика); за выполнение — оценка
   каждому по шкале педагога (1–5, у Яковлевой 1–10). Номер задания — по порядку у группы.
   Кабинет спортсмена не нужен: задание и оценка уходят родителям, видны в их «Дневнике». */
/* Заявки родителей «ребёнка нет в списке» по своим группам — те же решения, что кнопками в Telegram (22.10.2026). */
SCREENS['t.nchild'] = async () => {
  const d = await api('/newchild');
  return { title: 'Заявки на детей', html: d.items.length ? d.items.map(a => `<div class="card pad" style="margin-bottom:10px">
      <div style="font-weight:800;font-size:16px">${esc(a.name)}</div>
      <div class="hint">${esc(plainName(a.groupName))} · родитель ${a.parent}${a.sender ? ` · ${esc(a.sender)}` : ''} · ${fdate(a.createdAt.slice(0, 10))}</div>
      ${a.existing ? `<div class="hint" style="margin-top:6px">${a.existing.chosen ? 'Родитель выбрал ребёнка из базы' : 'Ученик с таким именем уже есть'}: <b>${esc(a.existing.name)}</b>${a.existing.groups.length ? ` · ${esc(a.existing.groups.map(plainName).join(', '))}` : ''}. Новая карточка не нужна — добавим в группу и привяжем родителя.</div>` : '<div class="hint" style="margin-top:6px">В базе такого ребёнка нет — будет заведена новая карточка в вашей группе.</div>'}
      <div style="margin-top:10px">${btn(a.existing ? '✅ Добавить в группу и привязать' : '✅ Завести и привязать', 'tNchildDo', { id: a.id, approve: true })}${btn('❌ Отклонить', 'tNchildDo', { id: a.id, approve: false }, 'ghost')}</div></div>`).join('')
    : '<div class="calm">✓ Заявок нет</div>' };
};
ACT.tNchildDo = async ({ id, approve }) => {
  try {
    const r = await api(`/newchild/${id}/decide`, { method: 'POST', body: { approve } });
    render(); toast(r.rejected ? 'Заявка отклонена' : r.existing ? `${r.name} добавлен в группу, родитель привязан` : `Заведён ученик ${r.name}, родитель привязан`);
  } catch (e) { render(); toast(e.code === 'already_decided' ? 'Заявку уже решили' : errText(e)); }
};
SCREENS['t.hw'] = async ({ gid }) => {
  const d = await api(`/homework${gid ? `?gid=${gid}` : ''}`);
  const start = gid
    ? goBtn('📋 Выдать новое задание', 't.hw.new', { gid })
    : d.groups.length ? `<div class="eyebrow">Выдать задание · выберите группу</div>${list(d.groups.map(g => cell({ lead: '👥', plain: true, t: esc(g.name), go: 't.hw.new', p: { gid: g.id } })))}` : '';
  return { title: 'Домашние задания', html: `
    ${gid ? `<div class="card pad"><div style="font-weight:800;font-size:16px">${esc(d.groupName)}</div></div><div style="margin:12px 0">${start}</div>` : ''}
    <div class="eyebrow">Выданные задания</div>
    ${d.batches.length ? list(d.batches.map(b => cell({
      lead: b.graded === b.total ? '✅' : '📋', plain: true, cls: 'wrap', t: `${b.number ? `<b>№${b.number}</b> · ` : ''}${esc(b.exercise)}`,
      s: `${gid ? '' : `${esc(b.groupName)} · `}${fdate(b.createdAt.slice(0, 10))} · ${b.single ? `👤 ${esc(b.single)}` : plural(b.total, ['ученик', 'ученика', 'учеников'])}`,
      r: `${b.withVideo ? `<span class="hint">📹 ${b.withVideo}</span> ` : ''}${pill(b.graded === b.total ? 'оценено' : `оценено ${b.graded}/${b.total}`, b.graded === b.total ? 'ok' : b.graded ? 'warn' : 'mute')}`,
      go: 't.hw.b', p: { id: b.id },
    }))) : '<div class="empty">Заданий пока нет</div>'}
    ${gid ? '' : start}` };
};

SCREENS['t.hw.new'] = async ({ gid, only }) => {
  const g = await api(`/groups/${gid}`);
  const key = `${gid}:${only || ''}`;      // из карточки ученика — отмечен только он
  if (!state.ui.hwSel || state.ui.hwSel.key !== key) state.ui.hwSel = { key, ids: new Set(only ? [only] : g.students.map(s => s.id)) };
  const sel = state.ui.hwSel.ids;
  return { title: 'Домашнее задание', html: `
    <div class="card pad"><div style="font-weight:800;font-size:16px">${esc(g.name)}</div>
      <div class="hint">Задание всей группе, нескольким ученикам или одному — отметьте, кому. Сообщение уйдёт родителям (и самому ученику, если у него кабинет спортсмена). Потом вы поставите каждому оценку за выполнение.</div></div>
    ${field('hw-e', 'Что задано', '', 'placeholder="Растяжка каждый день, выучить связку…" maxlength="300" autocomplete="off"')}
    ${field('hw-m', 'Минут в день (необязательно)', '', 'inputmode="numeric" placeholder="например, 15"')}
    ${field('hw-c', 'Комментарий (необязательно)', '', 'maxlength="300" autocomplete="off"')}
    <div class="eyebrow">Кому · <span id="hw-count">${sel.size}</span> из ${g.students.length}</div>
    ${g.students.length ? `<div class="chips"><button class="chip" data-act="hwAll" data-p='{}'>☑️ Всем / ⬜ никому</button></div>
    ${list(g.students.map(s => `<button class="cell" data-act="hwPick" data-p='${esc(JSON.stringify({ id: s.id }))}'><span class="lead plain" id="hw-${esc(s.id)}">${sel.has(s.id) ? '☑️' : '⬜'}</span><span><div class="t">${esc(s.name)}</div></span><span class="r"></span></button>`))}` : '<div class="empty">В группе никого нет</div>'}
    <div style="margin-top:12px">${btn('📨 Выдать задание', 'hwSend', { gid })}</div>` };
};
const hwMark = () => {
  const sel = state.ui.hwSel.ids;
  document.querySelectorAll('[id^="hw-STU"]').forEach(el => { el.textContent = sel.has(el.id.slice(3)) ? '☑️' : '⬜'; });
  const c = document.getElementById('hw-count'); if (c) c.textContent = sel.size;
};
ACT.hwPick = ({ id }) => { const sel = state.ui.hwSel.ids; if (sel.has(id)) sel.delete(id); else sel.add(id); hwMark(); };
ACT.hwAll = () => {
  const all = [...document.querySelectorAll('[id^="hw-STU"]')].map(el => el.id.slice(3));
  const sel = state.ui.hwSel.ids;
  if (all.every(id => sel.has(id))) sel.clear(); else all.forEach(id => sel.add(id));
  hwMark();
};
let hwSending = false;
ACT.hwSend = async ({ gid }) => {
  const exercise = val('hw-e').trim(), m = val('hw-m').trim(), minutes = m ? Number(m) : 0;
  if (!exercise) { toast('Напишите, что задано'); return; }
  if (!Number.isInteger(minutes) || minutes < 0) { toast('Минуты — целым числом'); return; }
  const ids = [...state.ui.hwSel.ids];
  if (!ids.length) { toast('Отметьте хотя бы одного ученика'); return; }
  if (hwSending) return;
  hwSending = true;
  try {
    const r = await api('/homework', { method: 'POST', body: { groupId: gid, studentIds: ids, exercise, minutes, comment: val('hw-c').trim() } });
    state.ui.hwSel = null;
    state.stack.pop();
    go('t.hw.b', { id: r.id });
    toast(r.repeat ? `Задание №${r.number} уже выдано` : `Задание №${r.number} выдано: ${plural(r.count, ['ученик', 'ученика', 'учеников'])}, родителям отправлено`);
  } catch (e) { toast(e.data && e.data.message ? e.data.message : errText(e)); }
  finally { hwSending = false; }
};

SCREENS['t.hw.b'] = async ({ id }) => {
  const d = await api(`/homework/${id}`);
  return { title: d.number ? `Задание №${d.number}` : 'Задание', html: `
    <div class="card pad"><div class="hint">${d.number ? `Задание №${d.number} от ${fdate(d.createdAt.slice(0, 10))}` : ''}${d.single ? ' · индивидуальное' : ''}</div><div style="font-weight:800;font-size:16px">${esc(d.exercise)}</div>
      <div class="hint">${esc(d.groupName)} · ${fdate(d.createdAt.slice(0, 10))}${d.minutes ? ` · ${d.minutes} мин в день` : ''}${d.comment ? `<br>💬 ${esc(d.comment)}` : ''}</div></div>
    <div class="eyebrow">Оценено ${d.graded} из ${d.total}</div>
    ${list(d.students.map(s => cell({
      lead: s.grade ? '⭐' : '📋', plain: true, cls: 'wrap', t: esc(s.name),
      s: `${s.grade ? (s.gradeComment ? `📝 ${esc(s.gradeComment)}` : 'оценено') : s.status === 'closed' ? 'закрыто без оценки' : 'не оценено'}${s.videos ? ` · 📹 видео: ${s.videos}` : ''}`,
      r: s.grade ? `<b>${s.grade}/${d.scale}</b>` : pill('оценить', 'warn'),
      act: 'hwGradeAsk', p: { tid: s.taskId, name: s.name, grade: s.grade || 0, comment: s.gradeComment || '', scale: d.scale, number: d.number, videos: s.videos || 0 },
    })))}
    <p class="hint" style="margin-top:8px">Тап по ученику — оценка за выполнение от 1 до ${d.scale}. Она сразу уходит родителям; поставленную оценку можно поменять.${d.students.some(s => s.videos) ? ' 📹 — ученик прислал видео выполнения: оно пришло вам в чат с ботом, из листа оценки его можно получить снова.' : ''}</p>` };
};
ACT.hwGradeAsk = ({ tid, name, grade, comment, scale = 5, number, videos = 0 }) => {
  state.ui.tGrade = grade;
  sheet(`<h3>Оценка за задание${number ? ` №${number}` : ''}</h3><div class="hint">${esc(name)}${grade ? ` · сейчас ${grade}/${scale}` : ''} · от 1 до ${scale}</div>
  ${videos ? `<div style="margin-top:10px">${btn(`📹 Видео в Telegram (${videos})`, 'hwVideoOpen', { tid }, 'sec')}</div>` : ''}
  <div class="chips" style="margin-top:12px">${Array.from({ length: scale }, (_, i) => i + 1).map(g => `<button class="chip" data-act="tGradePick" data-p='${esc(JSON.stringify({ g }))}' id="grade-${g}" aria-pressed="${g === grade}">${g}</button>`).join('')}</div>
  ${field('hw-gc', 'Комментарий (необязательно)', comment, 'placeholder="Что получилось, что подтянуть" maxlength="300"')}
  <div style="margin-top:12px">${btn('⭐ Сохранить оценку', 'hwGradeDo', { tid, scale })}${btn('Отмена', 'closeSheet', {}, 'ghost')}</div>`);
};
/* Видео выполнения (02.11.2026): бот пересылает ролики ученика педагогу в чат — файлы хранит Telegram. */
ACT.hwVideoOpen = async ({ tid }) => {
  try {
    const r = await api(`/homework/tasks/${tid}/video/send`, { method: 'POST' });
    toast(`Отправлено в чат с ботом: ${plural(r.count, ['видео', 'видео', 'видео'])}`);
    if (tg && r.bot && tg.openTelegramLink) tg.openTelegramLink(`https://t.me/${r.bot}`);
  } catch (e) { toast(e.data && e.data.message ? e.data.message : errText(e)); }
};
ACT.hwGradeDo = async ({ tid, scale = 5 }) => {
  const grade = state.ui.tGrade;
  if (!grade) { toast(`Выберите оценку от 1 до ${scale}`); return; }
  try { await api(`/homework/tasks/${tid}/grade`, { method: 'POST', body: { grade, comment: val('hw-gc').trim() } }); state.ui.tGrade = 0; closeSheet(); render(); toast('Оценка сохранена, родителям отправлено'); }
  catch (e) { toast(e.data && e.data.message ? e.data.message : errText(e)); }
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
      <div class="hint">${fmon(b.period)}${b.groups.length ? ` · ${esc(b.groups.join(', '))}` : ''}${b.rest ? ' · отметьте галочками, что оплачено' : ''}</div>${b.rest ? billAllChip(sel, 'tbillAll') : ''}</div>
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
let subSaving = false;        // повторный тап по «Сохранить», пока идёт запрос, — не второй запрос (16.10.2026)
ACT.tSubDo = async ({ sid, ym, gid }) => {
  const amount = +val('sub-a'); if (!(amount >= 0) || val('sub-a').trim() === '') { toast('Укажите сумму'); return; }
  if (subSaving) return;
  subSaving = true;
  document.querySelectorAll('[data-act="tSubDo"]').forEach(b => { b.disabled = true; b.textContent = '⏳ Сохраняю…'; });
  try { const r = await api(`/bills/student/${sid}/subscription`, { method: 'PUT', body: { ym, groupId: gid, amount, reason: val('sub-r').trim() } }); closeSheet(); state.ui.tbill = null; render(); if (r.unchanged) { toast('Сумма не изменилась'); return; } toast(`Абонемент за месяц: ${fmt(r.old)} → ${fmt(r.amount)}`); }
  catch (e) { closeSheet(); toast(e.data && e.data.message ? e.data.message : errText(e)); }
  finally { subSaving = false; }
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
ACT.tbillAll = () => { if (state.ui.tbill) { billAllToggle(state.ui.tbill); render(); } };
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
/* Двойное нажатие «Выдать задание» (случай Лобачева 02.11.2026: два одинаковых задания через 1,8 с): кнопки листа
   блокируются на время запроса, а сервер повтор того же задания за минуту второй раз не создаёт (`repeat`). */
let taskAdding = false;
ACT.tTaskAdd = async ({ sid }) => {
  const exercise = val('task-e').trim(); const minutes = +val('task-m');
  if (!exercise || !minutes) { toast('Нужно упражнение и минуты'); return; }
  if (taskAdding) return;
  taskAdding = true;
  document.querySelectorAll('.sheet .btn').forEach(b => { b.disabled = true; });
  try {
    const r = await api(`/diary/${sid}/tasks`, { method: 'POST', body: { exercise, minutes, comment: val('task-c').trim() } });
    closeSheet(); render(); toast(r.repeat ? 'Это задание уже выдано' : 'Задание отправлено спортсмену');
  } catch (e) { toast(errText(e)); document.querySelectorAll('.sheet .btn').forEach(b => { b.disabled = false; }); }
  finally { taskAdding = false; }
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
