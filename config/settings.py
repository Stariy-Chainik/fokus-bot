import json
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    bot_token: str = Field(..., alias="BOT_TOKEN")

    # Google Sheets
    google_credentials_json: str = Field(..., alias="GOOGLE_CREDENTIALS_JSON")
    spreadsheet_id: str = Field(..., alias="SPREADSHEET_ID")

    # Sheet names
    sheet_users: str = Field(default="users", alias="SHEET_USERS")
    sheet_teachers: str = Field(default="teachers", alias="SHEET_TEACHERS")
    sheet_students: str = Field(default="students", alias="SHEET_STUDENTS")
    sheet_lessons: str = Field(default="lessons", alias="SHEET_LESSONS")
    sheet_billing: str = Field(default="billing", alias="SHEET_BILLING")
    sheet_payments: str = Field(default="student_period_payments", alias="SHEET_PAYMENTS")
    sheet_teacher_period_submissions: str = Field(
        default="teacher_period_submissions", alias="SHEET_TEACHER_PERIOD_SUBMISSIONS",
    )
    sheet_branches: str = Field(default="branches", alias="SHEET_BRANCHES")
    sheet_groups: str = Field(default="groups", alias="SHEET_GROUPS")
    sheet_teacher_groups: str = Field(default="teacher_groups", alias="SHEET_TEACHER_GROUPS")
    sheet_student_groups: str = Field(default="student_groups", alias="SHEET_STUDENT_GROUPS")
    sheet_student_requests: str = Field(default="student_requests", alias="SHEET_STUDENT_REQUESTS")
    sheet_clients: str = Field(default="clients", alias="SHEET_CLIENTS")
    sheet_subscription_overrides: str = Field(
        default="subscription_overrides", alias="SHEET_SUBSCRIPTION_OVERRIDES",
    )
    sheet_finance_entries: str = Field(
        default="finance_entries", alias="SHEET_FINANCE_ENTRIES",
    )
    sheet_teacher_payouts: str = Field(default="teacher_payouts", alias="SHEET_TEACHER_PAYOUTS")
    sheet_teacher_rate_history: str = Field(
        default="teacher_rate_history", alias="SHEET_TEACHER_RATE_HISTORY",
    )
    # Бот в мессенджере MAX (кабинет родителя). Пусто — MAX не запускается.
    max_bot_token: str = Field(default="", alias="MAX_BOT_TOKEN")

    # Кабинет спортсмена: дневник тренировок и задания педагога
    sheet_training_entries: str = Field(default="training_entries", alias="SHEET_TRAINING_ENTRIES")
    sheet_athlete_tasks: str = Field(default="athlete_tasks", alias="SHEET_ATHLETE_TASKS")
    sheet_pending_actions: str = Field(default="pending_actions", alias="SHEET_PENDING_ACTIONS")

    # Payments — Telegram Payments (legacy)
    payment_provider_token: str = Field(default="", alias="PAYMENT_PROVIDER_TOKEN")

    # ЮКасса
    yookassa_shop_id: str = Field(default="", alias="YOOKASSA_SHOP_ID")
    yookassa_secret_key: str = Field(default="", alias="YOOKASSA_SECRET_KEY")
    yookassa_return_url: str = Field(default="https://t.me/fokus_bot", alias="YOOKASSA_RETURN_URL")
    # Email для фискального чека ЮКассы, когда у клиента нет телефона в базе
    yookassa_receipt_email: str = Field(default="", alias="YOOKASSA_RECEIPT_EMAIL")
    payment_webhook_port: int = Field(default=8081, alias="PAYMENT_WEBHOOK_PORT")

    # CloudKassir (онлайн-касса)
    cloudkassir_public_id: str = Field(default="", alias="CLOUDKASSIR_PUBLIC_ID")
    cloudkassir_api_secret: str = Field(default="", alias="CLOUDKASSIR_API_SECRET")

    # Контроль оплат
    # Долги считаются начиная с этого периода (YYYY-MM); пусто — за всё время.
    # Нужен, чтобы месяцы до внедрения учёта оплат не показывались как «долг».
    debtors_since_period: str = Field(default="", alias="DEBTORS_SINCE_PERIOD")
    # С какого месяца родитель видит счета в кабинете (раньше — архив школы, не его дело)
    parent_bills_since_period: str = Field(default="2026-09", alias="PARENT_BILLS_SINCE_PERIOD")

    # Секрет для ссылок-приглашений в группу (t.me/bot?start=g_...).
    # Пусто — используется BOT_TOKEN. Смена секрета отзывает все ссылки.
    group_link_secret: str = Field(default="", alias="GROUP_LINK_SECRET")
    # Mini App: tg_id, под которым принимается заголовок `Authorization: dev` (только локально; на проде пусто)
    miniapp_dev_tg_id: Optional[int] = Field(default=None, alias="MINIAPP_DEV_TG_ID")
    # Публичный HTTPS-адрес Mini App (страница /app/); пусто — кнопки кабинета в боте нет
    miniapp_url: str = Field(default="", alias="MINIAPP_URL")

    # Педагоги, которым разрешено выставлять счета ученикам своих групп
    # (teacher_id через запятую или |, например: TCH-0009). Пусто — счета только у админов.
    billing_teacher_ids: str = Field(default="", alias="BILLING_TEACHER_IDS")

    @property
    def billing_teacher_id_set(self) -> set:
        return {t.strip() for t in self.billing_teacher_ids.replace("|", ",").split(",") if t.strip()}

    # Спортивные группы: их ученики могут завести кабинет спортсмена
    # (сами находят себя по фамилии). group_id через запятую или |.
    athlete_group_ids: str = Field(default="GRP-0001", alias="ATHLETE_GROUP_IDS")

    @property
    def athlete_group_id_set(self) -> set:
        return {g.strip() for g in self.athlete_group_ids.replace("|", ",").split(",") if g.strip()}

    # Группы, где школа предпочитает наличные: в кабинете родителя этот способ
    # показывается первым и подписан как удобный (остальные способы остаются).
    cash_preferred_group_ids: str = Field(
        default="GRP-0001,GRP-0004,GRP-0002", alias="CASH_PREFERRED_GROUP_IDS",
    )

    @property
    def cash_preferred_group_id_set(self) -> set:
        return {g.strip() for g in self.cash_preferred_group_ids.replace("|", ",").split(",") if g.strip()}

    # Группы, где наличные не принимаются: способ не показывается родителю совсем.
    cash_disabled_group_ids: str = Field(default="", alias="CASH_DISABLED_GROUP_IDS")

    @property
    def cash_disabled_group_id_set(self) -> set:
        return {g.strip() for g in self.cash_disabled_group_ids.replace("|", ",").split(",") if g.strip()}

    # Педагоги, чьи ИНДИВИДУАЛЬНЫЕ занятия родители оплачивают напрямую педагогу
    # (мимо школы): не попадают в счета/долги/прибыль, зарплата школы = 0.
    direct_pay_teacher_ids: str = Field(default="", alias="DIRECT_PAY_TEACHER_IDS")

    @property
    def direct_pay_teacher_id_set(self) -> set:
        return {t.strip() for t in self.direct_pay_teacher_ids.replace("|", ",").split(",") if t.strip()}

    # Руководитель школы, ведущий занятия как педагог: его зарплата в «Прибыли»
    # не вычитается из выручки, а остаётся в прибыли (показывается отдельной строкой).
    owner_teacher_ids: str = Field(default="", alias="OWNER_TEACHER_IDS")

    @property
    def owner_teacher_id_set(self) -> set:
        return {t.strip() for t in self.owner_teacher_ids.replace("|", ",").split(",") if t.strip()}

    # Аренда зала: педагог с прямой оплатой (DIRECT_PAY_TEACHER_IDS) перечисляет
    # школе фикс. сумму с каждого своего индивидуального занятия — это выручка
    # школы в «Прибыли». Формат: TCH-0002:500
    hall_rent_per_lesson: str = Field(default="", alias="HALL_RENT_PER_LESSON")
    # Аренда считается только с этого месяца включительно (YYYY-MM); пусто — за всё время.
    hall_rent_since_period: str = Field(default="", alias="HALL_RENT_SINCE_PERIOD")

    @property
    def hall_rent_map(self) -> dict:
        out = {}
        for chunk in self.hall_rent_per_lesson.replace("|", ",").split(","):
            if ":" in chunk:
                tid, amount = chunk.split(":", 1)
                if tid.strip() and amount.strip().isdigit():
                    out[tid.strip()] = int(amount.strip())
        return out

    # Группы, где зарплата педагога = процент от сбора с учеников за занятие
    # (а не ставка × время). Формат: GRP-0020:50,GRP-0021:40
    revenue_share_groups: str = Field(default="", alias="REVENUE_SHARE_GROUPS")

    # Смена: группы внахлёст, интервалы в минутах от начала смены.
    # Зарплата за день = ставка группы × объединение интервалов проведённых групп.
    # Формат: GRP-0021:0-60,GRP-0022:0-120,GRP-0023:60-180
    shift_groups: str = Field(default="", alias="SHIFT_GROUPS")
    shift_label: str = Field(default="Смена", alias="SHIFT_LABEL")
    sheet_salary_overrides: str = Field(default="salary_day_overrides", alias="SHEET_SALARY_OVERRIDES")

    @property
    def shift_group_map(self) -> dict:
        from bot.services.salary_service import parse_shift_groups
        return parse_shift_groups(self.shift_groups)

    # Группы с фиксированной длительностью для зарплаты педагога (минуты),
    # независимо от выбранной при записи. Формат: GRP-0022:90,GRP-0023:90
    salary_duration_groups: str = Field(default="", alias="SALARY_DURATION_GROUPS")

    @property
    def salary_duration_group_map(self) -> dict:
        out = {}
        for chunk in self.salary_duration_groups.replace("|", ",").split(","):
            if ":" in chunk:
                gid, minutes = chunk.split(":", 1)
                if gid.strip() and minutes.strip().isdigit():
                    out[gid.strip()] = int(minutes.strip())
        return out

    # Своя ставка педагога за занятия конкретной группы (₽ за 45 мин, как rate_group).
    # Формат: GRP-0019:1500 — «БП Джаз»: 60 мин = 2000 ₽.
    group_salary_rates: str = Field(default="", alias="GROUP_SALARY_RATES")

    @property
    def group_salary_rate_map(self) -> dict:
        out = {}
        for chunk in self.group_salary_rates.replace("|", ",").split(","):
            if ":" in chunk:
                gid, rate = chunk.split(":", 1)
                if gid.strip() and rate.strip().isdigit():
                    out[gid.strip()] = int(rate.strip())
        return out

    @property
    def revenue_share_group_map(self) -> dict:
        out = {}
        for chunk in self.revenue_share_groups.replace("|", ",").split(","):
            if ":" in chunk:
                gid, pct = chunk.split(":", 1)
                if gid.strip() and pct.strip().isdigit():
                    out[gid.strip()] = int(pct.strip())
        return out

    # Способы оплаты
    payment_cash_enabled: bool = Field(default=True, alias="PAYMENT_CASH_ENABLED")
    # Email родителя для фискальных чеков: кнопка «✉️ Email для чеков» в меню и шаг
    # при регистрации по ссылке группы. False — родителю чеки не упоминаются.
    parent_receipt_email: bool = Field(default=True, alias="PARENT_RECEIPT_EMAIL")
    payment_bank_details: str = Field(default="", alias="PAYMENT_BANK_DETAILS")
    payment_qr_image_url: str = Field(default="", alias="PAYMENT_QR_IMAGE_URL")
    payment_qr_data: str = Field(default="", alias="PAYMENT_QR_DATA")
    payment_sbp_details: str = Field(default="", alias="PAYMENT_SBP_DETAILS")

    # Server / Railway
    # Если задан — бот запускается в webhook-режиме (рекомендуется для продакшена).
    # Пример: https://fokus-bot.railway.app
    # Если не задан — используется polling (удобно для локальной разработки).
    webhook_url: Optional[str] = Field(default=None, alias="WEBHOOK_URL")
    port: int = Field(default=8080, alias="PORT")  # Railway пробрасывает PORT автоматически

    # Redis (опционально)
    # Если задан — FSM-состояния хранятся в Redis и переживают перезапуск бота.
    # Если не задан — используется MemoryStorage (состояния сбрасываются при рестарте).
    redis_url: Optional[str] = Field(default=None, alias="REDIS_URL")

    @property
    def google_credentials_dict(self) -> dict:
        return json.loads(self.google_credentials_json)

    model_config = {"env_file": ".env", "populate_by_name": True}


settings = Settings()
