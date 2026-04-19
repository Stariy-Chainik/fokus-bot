# fokus-bot — список UX/архитектурных исправлений

Задачи получены после ревизии кода `fokus-bot` (aiogram 3.x, Google Sheets backend). Проанализированы все хендлеры в `bot/handlers/`, все клавиатуры в `bot/keyboards/`, все FSM-состояния в `bot/states/`.

Задачи отсортированы по приоритету. Для каждой указаны: файл, строки, диагноз, предлагаемый фикс и критерии приёмки.

---

## 📋 Порядок работы для Claude Code

1. Сначала чини **🔴 Критические** (№1–5) — они ломают функциональность или теряют данные
2. Затем **🟡 Средние** (№6–13) — UX-трения
3. Потом **🟢 Мелкие** (№14–20) — полировка
4. После каждого пункта — запускай линтер/тесты, отмечай галочку ниже

### Чек-лист прогресса

- [ ] №1 — student_requests → Google Sheets
- [ ] №2 — «Отметить занятие» в меню педагога
- [ ] №3 — Пагинация в kb_student_list
- [ ] №4 — Команда /menu
- [ ] №5 — Кнопка «Отмена» во всех FSM-экранах
- [ ] №6 — Контекст-сохранение в bills/salaries «Назад»
- [ ] №7 — Кнопка «В меню» после успешной записи занятия
- [ ] №8 — Переименовать «Готово» → «Назад» в чекбокс-экранах
- [ ] №9 — Salary «Назад» к списку периодов
- [ ] №10 — Фидбек-экран после отправки счёта
- [ ] №11 — Пункт «Заявки» в меню админа
- [ ] №12 — Сохранение контекста в t_add_pick
- [ ] №13 — Параметризация back_cb в kb_my_student_card
- [ ] №14 — Унификация префиксов пагинации
- [ ] №15 — Безопасное экранирование поискового запроса
- [ ] №16 — Убрать legacy-комментарий про attendance
- [ ] №17 — Перенести noop-хендлер в common
- [ ] №18 — Исправить иконки в kb_confirm
- [ ] №19 — Убрать дубль инфо в confirm_add_student
- [ ] №20 — «Переключить режим» для admin+teacher

---

# 🔴 КРИТИЧЕСКИЕ ИСПРАВЛЕНИЯ

## №1. Вынести `student_requests` из памяти в Google Sheets

**Что не так:** Заявки педагогов на создание новых учеников хранятся в `dict` в оперативной памяти бота. При рестарте (деплой, падение, shutdown бесплатного хостинга) все необработанные заявки теряются без уведомления ни педагога, ни админов.

**Файлы:**
- `bot/__main__.py:91` — объявление `dp["student_requests"] = {}`
- `bot/handlers/admin/students.py:823–966` — все 4 хендлера (`req_approve`, `req_create_new`, `req_link_existing`, `req_reject`)
- `bot/handlers/teacher/partners.py:1117–1124` — создание заявки

**Фикс:**

1. Создать новую таблицу в Google Sheets: `StudentRequests` с колонками:
   - `request_id` (str, PK)
   - `teacher_id` (str)
   - `teacher_tg_id` (int)
   - `teacher_name` (str)
   - `student_name` (str)
   - `group_id` (str)
   - `status` (enum: `pending` / `approved` / `rejected`)
   - `created_at` (iso datetime)
   - `resolved_at` (iso datetime, nullable)
   - `resolved_by_tg_id` (int, nullable)
   - `admin_msg_refs` (JSON list of `[chat_id, message_id]`, для обновления push-сообщений)

2. Создать `bot/models/student_request.py`:
   ```python
   from dataclasses import dataclass
   from enum import Enum
   
   class RequestStatus(str, Enum):
       PENDING = "pending"
       APPROVED = "approved"
       REJECTED = "rejected"
   
   @dataclass
   class StudentRequest:
       request_id: str
       teacher_id: str
       teacher_tg_id: int
       teacher_name: str
       student_name: str
       group_id: str
       status: RequestStatus
       created_at: str
       resolved_at: str | None = None
       resolved_by_tg_id: int | None = None
       admin_msg_refs: list[tuple[int, int]] | None = None
   ```

3. Создать `bot/repositories/student_request_repo.py` по образцу существующих (`student_repo.py`). Методы: `add`, `get_by_id`, `get_pending`, `set_status`, `update_admin_msgs`.

4. Зарегистрировать репозиторий в DI в `bot/__main__.py` и убрать `student_requests: dict`.

5. Везде заменить `student_requests: dict` на `request_repo: StudentRequestRepository` и `student_requests.get(req_id)` на `await request_repo.get_by_id(req_id)`.

6. Проверять статус: если `status != PENDING`, показывать «Заявка уже обработана».

**Критерии приёмки:**
- [ ] Заявки переживают рестарт бота
- [ ] После рестарта кнопки в старых push-сообщениях всё ещё работают корректно
- [ ] Если заявка уже approved/rejected, повторный клик показывает alert, а не создаёт дубль

---

## №2. Добавить «Отметить занятие» в главное меню педагога

**Что не так:** Основная рабочая функция педагога (запись занятия) спрятана на два уровня — доступна только через «Мои занятия» → «➕ Добавить занятие».

**Файлы:**
- `bot/keyboards/teacher.py:7–15` — `kb_teacher_menu()`

**Фикс:**

```python
def kb_teacher_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Отметить занятие", callback_data="teacher:record_lesson")],
        [InlineKeyboardButton(text="💃 Мои пары", callback_data="teacher:my_pairs")],
        [InlineKeyboardButton(text="🎯 Мои солисты", callback_data="teacher:my_soloists")],
        [InlineKeyboardButton(text="🏢 Мои группы", callback_data="teacher:my_groups")],
        [InlineKeyboardButton(text="📋 Мои занятия", callback_data="teacher:my_lessons")],
        [InlineKeyboardButton(text="📊 Моя статистика", callback_data="teacher:my_stats")],
        [InlineKeyboardButton(text="📤 Сдать период", callback_data="teacher:submit_period")],
    ])
```

Кнопка «➕ Добавить занятие» в `cb_my_lessons` (`my_lessons.py:163`) **оставить** — это хороший shortcut из контекста.

**Критерии приёмки:**
- [ ] Педагог видит «Отметить занятие» первой кнопкой главного меню
- [ ] Клик запускает существующий FSM-флоу записи

---

## №3. Добавить пагинацию в `kb_student_list`

**Что не так:** Используется в `students:delete`, `bills:confirm_payment`, зарплатах. Telegram молча обрезает inline-клавиатуру после ~100 кнопок. Танцевальная школа с 300+ учениками = `students:delete` перестаёт работать без сообщения об ошибке.

**Файлы:**
- `bot/keyboards/admin.py:112–118` — `kb_student_list`
- Все её вызовы: `students.py:528`, `bills.py`, `salaries.py`

**Фикс:** переписать по образцу `kb_student_paged` (admin.py:41–56), добавив параметры `page`, `total`, опциональный `query`:

```python
_STUDENT_LIST_PAGE_SIZE = 20

def kb_student_list(
    students: list,
    action_prefix: str,
    page: int = 0,
    total: int | None = None,
    back_cb: str = "admin:students",
) -> InlineKeyboardMarkup:
    total = total if total is not None else len(students)
    start = page * _STUDENT_LIST_PAGE_SIZE
    page_students = students[start:start + _STUDENT_LIST_PAGE_SIZE]
    
    buttons = [
        [InlineKeyboardButton(text=s.name, callback_data=f"{action_prefix}:{s.student_id}")]
        for s in page_students
    ]
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="← Пред.",
            callback_data=f"slist_page:{action_prefix}:{page - 1}",
        ))
    if (page + 1) * _STUDENT_LIST_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            text="След. →",
            callback_data=f"slist_page:{action_prefix}:{page + 1}",
        ))
    if nav:
        buttons.append(nav)
    
    buttons.append([InlineKeyboardButton(text="« Отмена", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
```

Добавить общий хендлер пагинации в `admin/students.py`:

```python
@router.callback_query(F.data.startswith("slist_page:"))
async def cb_student_list_page(
    callback: CallbackQuery,
    user: User | None,
    student_repo: StudentRepository,
) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, action_prefix, page_str = callback.data.split(":", 2)
    page = int(page_str)
    students = sorted(await student_repo.get_all(), key=lambda s: s.name)
    await callback.message.edit_reply_markup(
        reply_markup=kb_student_list(students, action_prefix, page=page, total=len(students))
    )
    await callback.answer()
```

**Критерии приёмки:**
- [ ] На тестовых данных из 300+ учеников `students:delete` работает
- [ ] Пагинация не теряет `back_cb` при листании
- [ ] Обратно совместимо: вызовы без `page=` продолжают работать

---

## №4. Команда `/menu` для быстрого возврата в главное меню

**Что не так:** Единственный способ вернуться в меню из глубоких FSM — `/start`. Он же запускает регистрационную логику (рассылка админам про «нового пользователя», хотя юзер уже давно зарегистрирован).

**Файлы:**
- `bot/handlers/common.py` — добавить хендлер
- `bot/__main__.py` — прописать команды в BotFather при старте (опционально)

**Фикс:**

В `bot/handlers/common.py` добавить:

```python
from aiogram.filters import Command

@router.message(Command("menu"))
async def cmd_menu(message: Message, user: User | None, state: FSMContext) -> None:
    await state.clear()
    if user is None:
        await message.answer("Сначала отправьте /start для регистрации.")
        return
    
    if user.is_admin and user.teacher_id:
        await message.answer("Выберите режим работы:", reply_markup=kb_mode_select())
        return
    if user.is_admin:
        await message.answer("Меню администратора:", reply_markup=kb_admin_menu())
        return
    if user.teacher_id:
        await message.answer("Меню педагога:", reply_markup=kb_teacher_menu())
        return
    
    await message.answer("Ожидайте, пока администратор назначит вам роль.")
```

В функции стартапа бота (`bot/__main__.py`) после инициализации dispatcher-а:

```python
from aiogram.types import BotCommand

await bot.set_my_commands([
    BotCommand(command="start", description="Запуск / главное меню"),
    BotCommand(command="menu", description="Главное меню"),
])
```

**Критерии приёмки:**
- [ ] `/menu` работает из любой точки — очищает FSM и показывает меню
- [ ] `/menu` не запускает регистрационную логику
- [ ] Команда появляется в автокомплите Telegram

---

## №5. Кнопка «Отмена» во всех FSM-экранах с текстовым вводом

**Что не так:** Часть FSM-состояний при запросе ввода не предоставляют клавиатуру — пользователь не может отменить операцию кроме как через `/start`.

**Файлы (где отсутствует кнопка отмены):**

| Файл | Строка | FSM-состояние |
|---|---|---|
| `bot/handlers/admin/students.py` | 391 | `AddStudentStates.entering_name` |
| `bot/handlers/admin/students.py` | 284–288 | `StudentListStates.searching` |
| `bot/handlers/admin/teachers.py` | 225–229 | `AddTeacherStates.entering_tg_id` |
| `bot/handlers/admin/teachers.py` | 472 | `EditTeacherRatesStates.entering_rate` |

**Фикс:** везде добавлять `reply_markup=kb_back("admin:students")` (или соответствующее меню).

Например, `cb_add_student_start`:

```python
@router.callback_query(F.data == "students:add")
async def cb_add_student_start(callback: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AddStudentStates.entering_name)
    await callback.message.edit_text(
        "<b>Добавление ученика</b>\nВведите Фамилию Имя ученика:",
        reply_markup=kb_back("admin:students"),  # ← добавить
    )
    await callback.answer()
```

Для `cb_students_list` — добавить отмену в состоянии поиска:

```python
await callback.message.edit_text(
    "<b>Поиск ученика</b>\n"
    "Введите имя или часть имени ученика для поиска.\n"
    "Чтобы показать всех — отправьте <b>*</b>",
    reply_markup=kb_back("admin:students"),  # ← добавить
)
```

**Важный нюанс:** кнопка «« Назад» должна очищать state. Убедиться что хендлеры, на которые она ведёт (например `admin:students`), делают `await state.clear()`. Для большинства уже это так, но перепроверить.

**Критерии приёмки:**
- [ ] Из каждого FSM-состояния с текстовым вводом есть кнопка «Отмена/Назад»
- [ ] Нажатие отмены очищает state и не оставляет зависших FSM-переходов
- [ ] Пользователь может прервать любой ввод без `/start`

---

# 🟡 СРЕДНИЕ ИСПРАВЛЕНИЯ

## №6. Сохранять контекст при «Назад» в bills и salaries

**Что не так:** После просмотра счёта ученика (или зарплаты педагога) кнопка «Назад» ведёт в главное меню. Чтобы посмотреть другой месяц того же ученика или другого ученика в группе — пять кликов заново.

**Файлы:**
- `bot/handlers/admin/bills.py:210, 245, 487, 514, 548, 550, 553` — все успехи и показы ведут в `admin:menu`
- `bot/handlers/admin/salaries.py:113` — итог зарплаты → `admin:salaries`

**Фикс для bills:**

В `cb_bills_show` (bills.py:189) — заменить финальную кнопку:

```python
# было
rows.append([InlineKeyboardButton(text="« Назад", callback_data="admin:menu")])

# стало (возврат к списку учеников группы)
# нужно знать group_id; это требует прокинуть его через callback_data
# или сохранить в state
```

Есть два способа:
- **Вариант A (простой):** хранить в state при `bvg:` шаге `bills_group_id` и `bills_period`; в `bvs:` его читать и подставлять в back_cb.
- **Вариант B (лучше):** изменить callback_data `bvs:` на `bvs:{period}:{group_id}:{student_id}` и при рендере финала back_cb = `bvg:{period}:{group_id}`.

Рекомендую **вариант B** — stateless, переживает перезапуски и race-conditions.

Также поправить:
- `bills.py:245` — back с bill_view → к списку учеников группы
- `bills.py:487, 548, 550, 553` — в confirm_payment flow финалы → `pcpg:{period}:{group_id}` (список учеников группы для подтверждения)

**Фикс для salaries:**

`salaries.py:113` — итоговый экран зарплаты:

```python
# было
await callback.message.edit_text("\n".join(lines), reply_markup=kb_back("admin:salaries"))

# стало
await callback.message.edit_text(
    "\n".join(lines),
    reply_markup=kb_back(f"salary_teacher:{teacher_id}"),  # ← к списку периодов
)
```

**Критерии приёмки:**
- [ ] Из счёта ученика «Назад» возвращает к списку учеников той же группы за тот же период
- [ ] Из confirm_payment финала «Назад» возвращает к списку учеников группы
- [ ] Из зарплаты «Назад» возвращает к список периодов того же педагога

---

## №7. Кнопка «В меню» на экране успешной записи занятия

**Что не так:** После `_finalize` в `record_lesson.py` показывается «Продолжим? Выберите тип следующего» с кнопками типа занятия и «Отмена». Чтобы выйти в меню, надо нажать «Отмена», что после успешного сохранения звучит подозрительно.

**Файлы:**
- `bot/keyboards/teacher.py:67–77` — `kb_lesson_type`
- `bot/handlers/teacher/record_lesson.py:749–807` — все три ветки `_finalize` (group, pair, soloist)

**Фикс:** вариант — новая клавиатура для post-save состояния:

В `keyboards/teacher.py`:

```python
def kb_lesson_type_after_save() -> InlineKeyboardMarkup:
    """Клавиатура после успешного сохранения: предложить продолжить или выйти."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Групповое", callback_data="lesson_kind:group")],
        [InlineKeyboardButton(text="💃 Парное", callback_data="lesson_kind:pair")],
        [InlineKeyboardButton(text="👤 Индивидуальное (соло)", callback_data="lesson_kind:soloist")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="teacher:menu")],
    ])
```

В `record_lesson.py` в трёх местах (`_finalize` для group, pair, soloist) — заменить `kb_lesson_type()` на `kb_lesson_type_after_save()`.

**Критерии приёмки:**
- [ ] После успешного сохранения занятия видна явная кнопка «🏠 В меню»
- [ ] Цикл записи нескольких занятий подряд продолжает работать как раньше
- [ ] Кнопка «« Отмена» в post-save больше не показывается (убрать когнитивный диссонанс)

---

## №8. Переименовать «Готово» → «Назад» в чекбокс-экранах

**Что не так:** Кнопка «💾 Готово» в чекбокс-редакторах визуально намекает что без неё изменения не сохранятся. На самом деле каждый toggle сохраняет сразу. Флоппи-иконка вводит в заблуждение.

**Файлы:**
- `bot/handlers/admin/teachers.py:129` — `_kb_teacher_groups_edit`
- `bot/handlers/admin/branches.py:76` — `_kb_group_teachers`
- `bot/handlers/admin/branches.py:87` — `_kb_group_students`
- `bot/handlers/teacher/my_groups.py:117` — `_kb_tg_students`

**Фикс:** заменить во всех четырёх местах:

```python
# было
rows.append([InlineKeyboardButton(text="💾 Готово", callback_data=f"...")])

# стало
rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"...")])
```

Альтернативно — добавить текст выше списка «ℹ️ Изменения сохраняются сразу. Нажмите «Назад» когда закончите.»

**Критерии приёмки:**
- [ ] Ни в одном чекбокс-экране нет кнопки «Готово» с флоппи-иконкой
- [ ] Пользователь понимает что тап по чекбоксу уже сохранил изменение

---

## №9. (См. №6) — входит в тот же фикс

---

## №10. Экран-фидбек после отправки счёта / рассылки счетов группе

**Что не так:** `bill_send` и `group_send_bills` показывают результат через `callback.answer(..., show_alert=True)`. Экран не меняется — пользователь остаётся на старом, где написано «Счёт не создан». Нет визуального подтверждения что действие состоялось.

**Файлы:**
- `bot/handlers/admin/bills.py:297–311` — `cb_bill_send`
- `bot/handlers/admin/branches.py:542–616` — `cb_group_send_bills`

**Фикс для `cb_bill_send`:** вместо `callback.answer` с алертом — `edit_text`:

```python
await callback.message.edit_text(
    f"📤 <b>Счёт отправлен родителю</b>\n\n"
    f"Ученик: {student.name}\n"
    f"Период: {display_period(period_month)}\n"
    f"Счетов создано: {len(invoices)}\n\n"
    f"<i>(заглушка — фактическая отправка не реализована)</i>",
    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="« К счёту",
            callback_data=f"bvs:{period_month}:{student_id}",
        )],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="admin:menu")],
    ]),
)
await callback.answer()  # короткий тост для visual feedback
```

Аналогично для `group_send_bills`.

**Критерии приёмки:**
- [ ] После отправки видно информативный экран результата
- [ ] С экрана результата можно вернуться к счёту (или к группе) и продолжить работу
- [ ] Алерт, если остаётся, — не заменяет экран, а дублирует для привлечения внимания

---

## №11. Добавить «📬 Заявки» в меню админа

**Что не так:** Заявки педагогов (pending) доступны только через push-сообщение, которое рассылается админу при создании заявки. Если сообщение удалено/потерялось или админ был офлайн — заявка невидима. В меню нет пункта для просмотра pending-заявок.

**Файлы:**
- `bot/keyboards/admin.py:4–14` — `kb_admin_menu`
- `bot/handlers/admin/students.py` — добавить новые хендлеры для списка заявок
- Зависит от **№1** (заявки должны быть в Sheets, иначе список будет пустой после рестарта)

**Фикс:**

1. В `kb_admin_menu` добавить кнопку (динамически с счётчиком):
   ```python
   [InlineKeyboardButton(text=f"📬 Заявки ({pending_count})", callback_data="admin:requests")],
   ```
   
   Поскольку клавиатура генерируется статически, лучше сделать функцию принимающую `pending_count`:
   ```python
   def kb_admin_menu(pending_requests: int = 0) -> InlineKeyboardMarkup:
       requests_label = f"📬 Заявки ({pending_requests})" if pending_requests else "📬 Заявки"
       return InlineKeyboardMarkup(inline_keyboard=[
           # ... существующие кнопки ...
           [InlineKeyboardButton(text=requests_label, callback_data="admin:requests")],
           # ...
       ])
   ```
   И в хендлерах, вызывающих `kb_admin_menu()`, подтягивать счётчик из `request_repo`.

2. Добавить новый хендлер:
   ```python
   @router.callback_query(F.data == "admin:requests")
   async def cb_admin_requests_list(
       callback: CallbackQuery, user: User | None,
       request_repo: StudentRequestRepository,
   ) -> None:
       if not _is_admin(user):
           await callback.answer("Нет доступа", show_alert=True)
           return
       pending = await request_repo.get_pending()
       if not pending:
           await callback.message.edit_text(
               "Активных заявок нет.", reply_markup=kb_back("admin:menu"),
           )
           await callback.answer()
           return
       rows = [
           [InlineKeyboardButton(
               text=f"📝 {r.teacher_name} → {r.student_name}",
               callback_data=f"req_open:{r.request_id}",
           )]
           for r in pending
       ]
       rows.append([InlineKeyboardButton(text="« Назад", callback_data="admin:menu")])
       await callback.message.edit_text(
           f"<b>Заявки педагогов ({len(pending)}):</b>",
           reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
       )
       await callback.answer()
   ```

3. Хендлер `req_open:{id}` — показывает ту же карточку заявки что и в push, с кнопками одобрить/отклонить.

**Критерии приёмки:**
- [ ] Админ видит пункт меню «📬 Заявки» со счётчиком pending
- [ ] Клик открывает список заявок
- [ ] Из списка можно одобрить/отклонить (те же хендлеры `req_approve`, `req_reject`)

---

## №12. Сохранение контекста в `t_add_pick`

**Что не так:** После успешного добавления ученика (флоу поиска) кнопка «➕ Добавить ещё» ведёт в `teacher:my_soloists` если у ученика нет group_id. Это меню выбора группы, пользователь должен снова выбирать группу → солисты → «Добавить». Три лишних клика.

**Файлы:**
- `bot/handlers/teacher/partners.py:965–967` — логика выбора `add_more_cb`

**Фикс:** сохранить в state откуда стартовал флоу добавления, и возвращаться туда:

В `cb_add_new_student_start` (строка 786) уже сохраняется `t_add_from_group`:
```python
await state.update_data(t_add_query="", t_add_from_group=group_id)
```

В `cb_add_student_pick` (строка 942) — прочитать его:

```python
@router.callback_query(F.data.startswith("t_add_pick:"))
async def cb_add_student_pick(
    callback: CallbackQuery, state: FSMContext, user: User | None,
    ts_repo: TeacherStudentRepository, student_repo: StudentRepository,
) -> None:
    # ... существующий код ...
    data = await state.get_data()
    from_group = data.get("t_add_from_group")
    
    await state.clear()
    # ... add logic ...
    
    # Определяем куда возвращаться
    if from_group:
        add_more_cb = f"t_add_from_grp:{from_group}"
    elif student.group_id:
        add_more_cb = f"t_add_from_grp:{student.group_id}"
    else:
        add_more_cb = "teacher:my_soloists"
    
    # ... остальной код ...
```

**Критерии приёмки:**
- [ ] Если педагог начал с группы X, «Добавить ещё» возвращает к ростеру группы X
- [ ] Если группа X недоступна (удалена) — корректный fallback
- [ ] Случай без group_id у ученика более не выкидывает в picker групп

---

## №13. Параметризовать `back_cb` в `kb_my_student_card`

**Что не так:** `kb_my_student_card` хардкодит кнопку «Назад» на `teacher:my_soloists`. В редком race-condition (партнёр ученика был снят между запросами) пользователь, зашедший из «Мои пары», выкидывается к «Мои солисты».

**Файлы:**
- `bot/keyboards/teacher.py:18–30` — `kb_my_student_card`
- `bot/handlers/teacher/partners.py:292–331` — `_render_student_card`

**Фикс:**

```python
def kb_my_student_card(
    student_id: str,
    has_partner: bool,
    can_manage: bool,
    back_cb: str = "teacher:my_soloists",  # ← новый параметр
) -> InlineKeyboardMarkup:
    _ = has_partner, can_manage
    rows = [
        [InlineKeyboardButton(text="✏️ Изменить имя", callback_data=f"t_rename_student:{student_id}")],
        [InlineKeyboardButton(text="🚪 Убрать из моего списка", callback_data=f"t_unlink_self:{student_id}")],
        [InlineKeyboardButton(text="« Назад", callback_data=back_cb)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
```

В `_render_student_card` (partners.py):

```python
if back_to_pairs and student.partner_id:
    kb = kb_my_pair_card(student.student_id) if can_manage else InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="« Назад к парам", callback_data="teacher:my_pairs")]]
    )
else:
    back_cb = "teacher:my_pairs" if back_to_pairs else "teacher:my_soloists"
    kb = kb_my_student_card(
        student.student_id,
        has_partner=bool(student.partner_id),
        can_manage=can_manage,
        back_cb=back_cb,  # ← передаём
    )
```

**Критерии приёмки:**
- [ ] Если пользователь пришёл из «Мои пары» — «Назад» ведёт обратно к парам
- [ ] Если из «Мои солисты» — к солистам
- [ ] Дефолтное поведение не изменилось для существующих вызовов

---

# 🟢 МЕЛКИЕ ИСПРАВЛЕНИЯ

## №14. Унификация префиксов пагинации

**Что не так:** Три разных префикса для одного паттерна: `spage:` (students search), `t_add_page:` (teacher add), `lessons_page:` (lessons).

**Файлы:**
- `bot/keyboards/admin.py:49–51`
- `bot/keyboards/teacher.py:201–204`
- `bot/keyboards/teacher.py:247–253`

**Фикс:** привести к общему виду, например `page:{scope}:{n}`:
- `spage:{q}:{n}` → `page:students:{q}:{n}`
- `t_add_page:{n}` → `page:t_add:{n}`
- `lessons_page:{n}:{tag}` → `page:lessons:{n}:{tag}`

Техдолг, не UX-проблема. Делать только если параллельно идёт рефакторинг клавиатур.

---

## №15. Безопасное экранирование поискового запроса в пагинации

**Что не так:** `students.py:320` — запрос содержащий символ `_` развалит пагинацию:
```python
q = query.replace(":", "_")
# ...
query = parts[1].replace("_", ":") if parts[1] != "" else ""
```

Если поиск «name_test», подстановка назад даст «name:test» и потеряет данные.

**Фикс:** использовать base64/urlsafe-кодирование или хранить query в state а не в callback_data:

```python
import base64

def _encode_q(q: str) -> str:
    return base64.urlsafe_b64encode(q.encode()).decode().rstrip("=")

def _decode_q(encoded: str) -> str:
    padded = encoded + "=" * ((4 - len(encoded) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode()).decode()
```

Или ещё проще — в state (уже есть `student_query`):
```python
# в kb_student_paged callback_data оставлять только page
f"spage:{page - 1}"
# в хендлере читать query из state
data = await state.get_data()
query = data.get("student_query", "")
```

**Критерии приёмки:**
- [ ] Поиск «name_test» работает с пагинацией корректно
- [ ] Callback_data не содержит сырых пользовательских строк

---

## №16. Убрать legacy-комментарий про attendance

**Что не так:** `record_lesson.py:5–6` в docstring модуля:
> «asking_attendance — legacy, теперь не используется»

Но состояние активно используется в `_ask_group_attendance` (строка 285). Комментарий устарел, введёт в заблуждение.

**Файлы:**
- `bot/handlers/teacher/record_lesson.py:5–7`
- `bot/states/lesson_states.py` — в `RecordLessonStates.asking_attendance` тоже есть похожий комментарий

**Фикс:** удалить обе приписки про legacy.

---

## №17. Перенести `noop` хендлер в common

**Что не так:** `@router.callback_query(F.data == "noop")` зарегистрирован в `my_lessons.py:365`, но callback `noop` используется также в `calendar.py` (неактивные даты, заголовки) и в `bills.py` (оплаченные счета). Если роутер `my_lessons` не подключён или идёт в другой очереди — `noop`-клики остаются без ответа, Telegram показывает крутилку.

**Файлы:**
- `bot/handlers/teacher/my_lessons.py:365–367` — убрать отсюда
- `bot/handlers/common.py` — добавить здесь

**Фикс:**

В `common.py`:
```python
@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()
```

Удалить из `my_lessons.py`.

**Критерии приёмки:**
- [ ] Клик по неактивной дате в календаре не оставляет крутилку ни в одном сценарии
- [ ] Клик по оплаченному счёту в `pay_pick_invoice` не оставляет крутилку

---

## №18. Иконки в `kb_confirm`

**Что не так:** `kb_confirm` использует «💾 Подтвердить» (флоппи-диск). Для подтверждения удаления или сброса пары флоппи нелогична.

**Файлы:**
- `bot/keyboards/admin.py:130–136` — `kb_confirm`
- `bot/keyboards/teacher.py:58–64` — `kb_t_confirm`

**Фикс:** нейтральная иконка для универсального `kb_confirm`:

```python
def kb_confirm(confirm_cb: str, cancel_cb: str, confirm_text: str = "✅ Подтвердить") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=confirm_text, callback_data=confirm_cb),
            InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_cb),
        ]
    ])
```

В местах удаления передавать `confirm_text="🗑 Удалить"`, в местах сохранения данных — можно оставить «💾 Сохранить» или «✅ Подтвердить».

**Критерии приёмки:**
- [ ] При подтверждении удаления кнопка не содержит флоппи-иконки
- [ ] Обратная совместимость: вызовы без `confirm_text` работают как раньше

---

## №19. Убрать дубль информации в `confirm_add_student`

**Что не так:** После подтверждения добавления ученика показывается экран «Ученик добавлен» с ID и именем + одновременно alert через `callback.answer(toast, show_alert=True)` с той же самой информацией. Избыточно.

**Файлы:**
- `bot/handlers/admin/students.py:501–510`

**Фикс:** оставить что-то одно. Рекомендую убрать alert и оставить только экран (он информативнее и сохраняется):

```python
# было
await callback.message.edit_text(
    f"<b>Ученик добавлен!</b>\nID: {student.student_id}\nФамилия Имя: {student.name}",
    reply_markup=kb_back("admin:students"),
)
# ...
await callback.answer(toast, show_alert=True)

# стало
await callback.message.edit_text(
    f"<b>✅ Ученик добавлен</b>\n\n"
    f"Имя: <b>{student.name}</b>\n"
    f"ID: <code>{student.student_id}</code>\n"
    f"{group_info}",  # собирается из toast
    reply_markup=kb_back("admin:students"),
)
await callback.answer()  # короткая нотификация без show_alert
```

**Критерии приёмки:**
- [ ] Не появляется двойной фидбек (экран + alert с той же инфой)

---

## №20. «Переключить режим» для админа-педагога

**Что не так:** Пользователь с `is_admin=True` и `teacher_id is not None` при `/start` выбирает режим. После — заперт в нём до следующего `/start`. Нет пункта «Переключить режим» в меню.

**Файлы:**
- `bot/keyboards/admin.py:4–14` — `kb_admin_menu`
- `bot/keyboards/teacher.py:7–15` — `kb_teacher_menu`
- `bot/handlers/common.py` — хендлеры

**Фикс:**

В оба меню добавить последней кнопкой (только если у пользователя обе роли):

```python
def kb_admin_menu(can_switch_role: bool = False) -> InlineKeyboardMarkup:
    rows = [
        # ... существующие кнопки ...
    ]
    if can_switch_role:
        rows.append([InlineKeyboardButton(text="🔄 Режим педагога", callback_data="mode:teacher")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
```

Аналогично для `kb_teacher_menu`. В хендлерах (где вызывается меню) — передавать `can_switch_role=bool(user.is_admin and user.teacher_id)`.

Хендлеры `mode:admin` и `mode:teacher` уже есть в `common.py:81–96`, дополнительной логики не требуется.

**Критерии приёмки:**
- [ ] Пользователь с обеими ролями видит кнопку переключения в обоих меню
- [ ] Обычный админ/педагог (без второй роли) её не видит
- [ ] Переключение не требует `/start`

---

# 🧪 Общие замечания по тестированию

1. **Запустить тесты** (`tests/`) после каждого крупного блока изменений
2. **Smoke-test вручную** основных флоу:
   - `/start` → регистрация → назначение админом → педагог → `/menu`
   - Админ: создать филиал → группу → педагога → ученика → назначить партнёра → записать занятие (от лица педагога) → проверить счёт → подтвердить оплату → зарплату
   - Педагог: записать 5 занятий разных типов → мои занятия → удалить одно → сдать период
   - Заявка педагога на нового ученика → админ одобряет / отклоняет / привязывает к существующему (проверить переживание рестарта после №1)
3. **Проверить что `/menu` не ломает активный FSM** — должен корректно очищать state
4. **Проверить большие списки** — `students:delete` на 100+ учениках (после №3)

---

# 📎 Дополнительный контекст для Claude Code

- Фреймворк: **aiogram 3.x**
- Backend: **Google Sheets через gspread** (все репозитории в `bot/repositories/`)
- FSM: **FSMContext** на памяти (`MemoryStorage`), так что FSM-данные тоже теряются при рестарте — это нормально для FSM, но не для `student_requests`
- DI: через `dp["key"]` в `bot/__main__.py`, хендлеры получают зависимости как именованные параметры (`user_repo: UserRepository` и т.п.)
- Middleware: `AuthMiddleware` добавляет `user: User | None` в data каждого хендлера
- Кэш Sheets: TTL 300 сек, инвалидируется на любой записи — учитывай при тестах (изменения видны сразу после write, read иногда отдаёт кэш)

**Рекомендованный порядок работы:**
1. Сначала №5 (Отмена в FSM) — простой, повышает доверие к остальным правкам
2. Потом №2, №4 (меню-changes) — быстрые UX-победы
3. Затем №1, №3 (архитектурные) — крупные и связаны с данными
4. Дальше по списку

**Не смешивать в одном коммите:** каждый пункт — отдельный commit с префиксом `fix(ux): №N — короткое описание`. Это упростит ревью и откат.
