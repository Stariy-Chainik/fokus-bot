# Найденные баги (НЕ исправлять в рамках рефакторинга)

Замеченные по ходу поведение-сохраняющего рефакторинга дефекты. Каждый — только зафиксирован, не пофикшен.
Формат: **симптом** — где (`file:line`) — почему баг — как воспроизвести.

## B1. proxy_record_go не проверяет, что разрешение выдано именно этому пользователю
- **Где:** [bot/handlers/teacher/record_lesson.py](../bot/handlers/teacher/record_lesson.py) — `cb_proxy_record_go` (callback `proxy_record_go:{teacher_id}`).
- **Симптом:** любой пользователь-педагог, отправивший callback `proxy_record_go:TCH-XXXX`, начинает запись от имени этого педагога без одобрения админа.
- **Почему баг:** кнопка появляется только после `proxy_approve`, но сам хендлер факт одобрения (и адресата) не проверяет — авторизация держится лишь на том, что callback_data «трудно угадать». Проверка `_is_teacher` пропускает любого педагога, не только Клецову.
- **Репро:** от аккаунта любого педагога отправить боту callback `proxy_record_go:TCH-0005` (например, через клиентский API) — откроется wizard записи за Никишина.

## B2. В хлебных крошках wizard'а тип «shared» показывается сырым словом
- **Где:** [bot/handlers/teacher/record_lesson.py](../bot/handlers/teacher/record_lesson.py) — `_KIND_LABEL` (нет ключа `shared`) + `_header()`.
- **Симптом:** при записи разового совместного занятия в заголовке экранов выбора показывается «shared» вместо русской подписи (у group/pair/soloist — «Группа/Пара/Соло»).
- **Почему баг:** `_KIND_LABEL.get(kind, kind)`-семантика подставляет сам ключ, словарь не пополнили при добавлении типа shared.
- **Репро:** запись занятия → тип «Разовое совместное» → на экране длительности/выбора в шапке виден англ. «shared».

## B3. Мёртвая ветка pair_from_soloists и перекрытый cb_noop
- **Где:** [bot/handlers/teacher/record_lesson.py](../bot/handlers/teacher/record_lesson.py) — флаг `pair_from_soloists` (ставится только в `False`), `_show_pair_soloists_in_group`, состояние `picking_pair_soloists`, хендлеры `pso_toggle`/`pso_confirm` для него; дубль `cb_noop` (перехватывается более ранним `common_router`).
- **Симптом:** пользователю не виден — код недостижим.
- **Почему баг:** скорее незавершённая фича «пара из солистов», чем осознанный код; мёртвые ветки затрудняют декомпозицию P5.
- **Репро:** нет (недостижимо). Кандидат на удаление отдельным коммитом «Remove dead code» после подтверждения, что фича не планируется.

## B4. Webhook ЮКассы не верифицирует запрос (безопасность) — ✅ ИСПРАВЛЕНО
- **Где:** [bot/handlers/client/payments.py](../bot/handlers/client/payments.py) — `make_yookassa_webhook_handler`.
- **Симптом (был):** любой, кто знает URL `/yookassa-webhook`, мог POST-запросом с телом
  `{"event":"payment.succeeded","object":{"metadata":{"student_id":"STU-…","period_month":"YYYY-MM"}}}`
  перевести все счета периода в PAID без оплаты.
- **Почему баг:** тело запроса принималось на веру — ни IP-allowlist ЮКассы, ни перепроверки
  статуса платежа через API ЮКассы.
- **Исправлено (2026-07):** `process_yookassa_event` берёт из тела только `object.id` и
  перепроверяет платёж напрямую у API ЮКассы (`Payment.find_one`); статус/metadata/сумма —
  из ответа API. Подтверждение — только при реальном `succeeded`; сетевая ошибка проверки →
  HTTP 500 (ЮКасса ретраит). Покрыто тестами [tests/test_yookassa_webhook.py](../tests/test_yookassa_webhook.py).
- **Остаток (в веб-версии):** хранение внешнего `payment_id` для сверки/ручной перепроверки —
  модель `ExternalPayment` в [MINIAPP_BUILD.md §6.1](MINIAPP_BUILD.md).
