---
name: miniapp-only
description: ЛК реализуем только как Telegram Mini App — отдельный сайт и phone OTP исключены (2026-09-06)
metadata: 
  node_type: memory
  type: project
  originSessionId: 818bb821-44ed-49da-8902-f073fbe4d8da
  modified: 2026-09-06T07:28:17.684Z
---

**ОТЛОЖЕНО 2026-09-06:** Mini App пока не делаем — родители работают в боте
(клиентская роль полная: занятия, счета, оплата ЮКассой). API-мост уже на проде:
`/api/me/bills`, `/api/me/pay` (bot/api/miniapp.py) — пригодится при возврате к теме.
Блокер был: нужен домен с HTTPS для BotFather.

Решение от 2026-09-06: личный кабинет — **только Telegram Mini App**, ветка
«обычный сайт» убрана из плана. Вход всех ролей — исключительно проверенный
Telegram `initData`; phone-OTP-флоу и модель AuthChallenge не реализуются.
Код живёт внутри репозитория fokus-bot (папка `web/`).

**Why:** родители и так в Telegram (привязка по ссылкам групп), второй канал
входа удваивает поверхность auth без пользы.

**How to apply:** не предлагать SMS/OTP, browser-версию и отдельный деплой
фронта; интеграции (ЮКасса/СБП, см. [[payments-roadmap]]) проектировать под
открытие из Mini App.
