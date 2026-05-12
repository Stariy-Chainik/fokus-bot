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

    # Payments — Telegram Payments (legacy)
    payment_provider_token: str = Field(default="", alias="PAYMENT_PROVIDER_TOKEN")

    # ЮКасса
    yookassa_shop_id: str = Field(default="", alias="YOOKASSA_SHOP_ID")
    yookassa_secret_key: str = Field(default="", alias="YOOKASSA_SECRET_KEY")
    yookassa_return_url: str = Field(default="https://t.me/fokus_bot", alias="YOOKASSA_RETURN_URL")
    payment_webhook_port: int = Field(default=8081, alias="PAYMENT_WEBHOOK_PORT")

    # Способы оплаты
    payment_cash_enabled: bool = Field(default=True, alias="PAYMENT_CASH_ENABLED")
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
