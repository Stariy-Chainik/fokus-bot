"""
Точка входа бота.

Режимы запуска (определяются env-переменными):
  - Webhook: если задан WEBHOOK_URL → регистрирует webhook, запускает aiohttp-сервер
  - Polling: иначе → long polling (удобно для локальной разработки)

FSM storage:
  - Redis: если задан REDIS_URL → персистентные состояния, переживают перезапуск
  - Memory: иначе → состояния сбрасываются при перезапуске (только для dev)
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from config.settings import settings
from bot.repositories import (
    SheetsClient, UserRepository, TeacherRepository, StudentRepository,
    LessonRepository, PaymentRepository,
    TeacherPeriodSubmissionRepository,
    BranchRepository, GroupRepository, TeacherGroupRepository,
    StudentGroupRepository,
    StudentRequestRepository,
    ClientRepository,
)
from bot.services import (
    LessonService, PaymentService, DiagnosticsService, TeacherVisibilityService,
    CloudKassirService,
)
from bot.middlewares import AuthMiddleware, DedupUpdateMiddleware
from bot.handlers import common_router, admin_router, teacher_router, client_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def _build_storage():
    """Redis если задан REDIS_URL, иначе Memory (для локальной разработки)."""
    if settings.redis_url:
        try:
            from redis.asyncio import Redis
            from aiogram.fsm.storage.redis import RedisStorage
            redis = Redis.from_url(settings.redis_url, decode_responses=False)
            logger.info("FSM storage: Redis (%s)", settings.redis_url)
            return RedisStorage(redis=redis)
        except ImportError:
            logger.warning("Пакет redis не установлен, используется MemoryStorage")
    logger.info("FSM storage: Memory (состояния не переживают перезапуск)")
    return MemoryStorage()


def _build_dispatcher(storage) -> Dispatcher:
    dp = Dispatcher(storage=storage)

    # ── Sheets client и репозитории ──────────────────────────────────────────
    sheets_client = SheetsClient(settings)

    user_repo = UserRepository(sheets_client, settings.sheet_users)
    teacher_repo = TeacherRepository(sheets_client, settings.sheet_teachers)
    student_repo = StudentRepository(sheets_client, settings.sheet_students)
    lesson_repo = LessonRepository(sheets_client, settings.sheet_lessons)
    payment_repo = PaymentRepository(sheets_client, settings.sheet_payments)
    submission_repo = TeacherPeriodSubmissionRepository(
        sheets_client, settings.sheet_teacher_period_submissions,
    )
    branch_repo = BranchRepository(sheets_client, settings.sheet_branches)
    group_repo = GroupRepository(sheets_client, settings.sheet_groups)
    teacher_group_repo = TeacherGroupRepository(sheets_client, settings.sheet_teacher_groups)
    student_group_repo = StudentGroupRepository(sheets_client, settings.sheet_student_groups)
    student_request_repo = StudentRequestRepository(sheets_client, settings.sheet_student_requests)
    client_repo = ClientRepository(sheets_client, settings.sheet_clients)

    # ── Сервисы ──────────────────────────────────────────────────────────────
    lesson_service = LessonService(lesson_repo, submission_repo, teacher_repo)
    payment_service = PaymentService(payment_repo, lesson_repo, teacher_repo)
    diagnostics_service = DiagnosticsService(lesson_repo, teacher_repo, student_repo)
    visibility = TeacherVisibilityService(student_repo, teacher_group_repo, student_group_repo)
    cloudkassir_service = CloudKassirService(
        settings.cloudkassir_public_id,
        settings.cloudkassir_api_secret,
    )

    # ── DI: зависимости во все хендлеры через workflow_data ──────────────────
    dp["user_repo"] = user_repo
    dp["teacher_repo"] = teacher_repo
    dp["student_repo"] = student_repo
    dp["lesson_repo"] = lesson_repo
    dp["payment_repo"] = payment_repo
    dp["submission_repo"] = submission_repo
    dp["branch_repo"] = branch_repo
    dp["group_repo"] = group_repo
    dp["teacher_group_repo"] = teacher_group_repo
    dp["student_group_repo"] = student_group_repo
    dp["student_request_repo"] = student_request_repo
    dp["client_repo"] = client_repo
    dp["lesson_service"] = lesson_service
    dp["payment_service"] = payment_service
    dp["diagnostics_service"] = diagnostics_service
    dp["visibility"] = visibility
    dp["cloudkassir_service"] = cloudkassir_service

    # ── Middleware ────────────────────────────────────────────────────────────
    dp.update.outer_middleware(DedupUpdateMiddleware())
    dp.update.middleware(AuthMiddleware(user_repo))

    # ── Роутеры ──────────────────────────────────────────────────────────────
    dp.include_routers(common_router, admin_router, teacher_router, client_router)

    return dp


def _register_payment_webhook(app, dp: Dispatcher, bot: Bot) -> None:
    """Регистрирует маршрут /yookassa-webhook в aiohttp app."""
    from bot.handlers.client.payments import make_yookassa_webhook_handler
    payment_service = dp["payment_service"]
    user_repo = dp["user_repo"]
    app.router.add_post(
        "/yookassa-webhook",
        make_yookassa_webhook_handler(payment_service, bot, user_repo),
    )
    logger.info("Маршрут /yookassa-webhook зарегистрирован")


async def _run_webhook(bot: Bot, dp: Dispatcher) -> None:
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    webhook_path = f"/webhook/{settings.bot_token}"
    webhook_url = f"{settings.webhook_url.rstrip('/')}{webhook_path}"

    await bot.set_webhook(
        url=webhook_url,
        drop_pending_updates=True,
        allowed_updates=dp.resolve_used_update_types(),
    )
    logger.info("Webhook зарегистрирован: %s", webhook_url)

    app = web.Application()

    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app.router.add_get("/health", health)
    _register_payment_webhook(app, dp, bot)

    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=webhook_path)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.port)
    await site.start()
    logger.info("aiohttp сервер запущен на порту %d", settings.port)

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await bot.delete_webhook()


async def _run_polling(bot: Bot, dp: Dispatcher) -> None:
    from aiohttp import web
    logger.info("Запуск в режиме polling")
    await bot.delete_webhook(drop_pending_updates=True)

    # Отдельный aiohttp-сервер для приёма webhook-уведомлений ЮКасса
    app = web.Application()
    _register_payment_webhook(app, dp, bot)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", settings.payment_webhook_port).start()
    logger.info("Payment webhook сервер запущен на порту %d", settings.payment_webhook_port)

    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()


async def main() -> None:
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage = _build_storage()
    dp = _build_dispatcher(storage)

    await bot.set_my_commands([
        BotCommand(command="start", description="Запуск / главное меню"),
        BotCommand(command="menu", description="Главное меню"),
    ])

    if settings.webhook_url:
        await _run_webhook(bot, dp)
    else:
        await _run_polling(bot, dp)


if __name__ == "__main__":
    asyncio.run(main())
