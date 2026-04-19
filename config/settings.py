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
    sheet_teacher_students: str = Field(default="teacher_students", alias="SHEET_TEACHER_STUDENTS")
    sheet_lessons: str = Field(default="lessons", alias="SHEET_LESSONS")
    sheet_billing: str = Field(default="billing", alias="SHEET_BILLING")
    sheet_payments: str = Field(default="student_period_payments", alias="SHEET_PAYMENTS")
    sheet_teacher_period_submissions: str = Field(
        default="teacher_period_submissions", alias="SHEET_TEACHER_PERIOD_SUBMISSIONS",
    )
    sheet_branches: str = Field(default="branches", alias="SHEET_BRANCHES")
    sheet_groups: str = Field(default="groups", alias="SHEET_GROUPS")
    sheet_teacher_groups: str = Field(default="teacher_groups", alias="SHEET_TEACHER_GROUPS")
    sheet_student_requests: str = Field(default="student_requests", alias="SHEET_STUDENT_REQUESTS")
    sheet_student_invites: str = Field(default="student_invite_codes", alias="SHEET_STUDENT_INVITES")

    # Оплата (Telegram Payments + ЮKassa)
    # Токен провайдера выдаётся @BotFather при подключении ЮKassa к боту.
    # Без него клиентские хендлеры оплаты показывают «оплата временно недоступна».
    yookassa_provider_token: Optional[str] = Field(default=None, alias="YOOKASSA_PROVIDER_TOKEN")

    # TTL кода привязки ученика. 0 = без ограничения.
    invite_code_ttl_hours: int = Field(default=24, alias="INVITE_CODE_TTL_HOURS")

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
