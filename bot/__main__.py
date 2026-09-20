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
    ClientRepository, SubscriptionOverrideRepository, FinanceEntryRepository,
    TrainingEntryRepository, AthleteTaskRepository,
)
from bot.services import (
    LessonService, PaymentService, DiagnosticsService, TeacherVisibilityService,
    CloudKassirService, StudentService, StudentRequestService, ProfitService,
    DiaryService,
)
from bot.middlewares import AuthMiddleware, DedupUpdateMiddleware
from bot.handlers import common_router, admin_router, teacher_router, athlete_router, client_router

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


def _build_dispatcher(storage, tg_bot=None) -> Dispatcher:
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
    subscription_override_repo = SubscriptionOverrideRepository(
        sheets_client, settings.sheet_subscription_overrides,
    )
    finance_entry_repo = FinanceEntryRepository(sheets_client, settings.sheet_finance_entries)
    from bot.repositories.teacher_rate_history_repo import TeacherRateHistoryRepository
    rate_history_repo = TeacherRateHistoryRepository(sheets_client, settings.sheet_teacher_rate_history)
    from bot.repositories.teacher_payout_repo import TeacherPayoutRepository
    payout_repo = TeacherPayoutRepository(sheets_client, settings.sheet_teacher_payouts)
    from bot.repositories.salary_override_repo import SalaryOverrideRepository
    salary_override_repo = SalaryOverrideRepository(sheets_client, settings.sheet_salary_overrides)
    training_entry_repo = TrainingEntryRepository(sheets_client, settings.sheet_training_entries)
    athlete_task_repo = AthleteTaskRepository(sheets_client, settings.sheet_athlete_tasks)

    # ── Сервисы ──────────────────────────────────────────────────────────────
    from bot.services.salary_service import SalaryService
    salary_service = SalaryService(lesson_repo, salary_override_repo)
    lesson_service = LessonService(lesson_repo, submission_repo, teacher_repo, salary_service=salary_service)
    payment_service = PaymentService(
        payment_repo, lesson_repo, teacher_repo,
        group_repo=group_repo, student_group_repo=student_group_repo,
        subscription_override_repo=subscription_override_repo,
    )
    profit_service = ProfitService(
        teacher_repo, lesson_repo, payment_service, finance_entry_repo,
        salary_service=salary_service,
    )
    diagnostics_service = DiagnosticsService(lesson_repo, teacher_repo, student_repo)
    visibility = TeacherVisibilityService(student_repo, teacher_group_repo, student_group_repo)
    student_service = StudentService(
        student_repo, teacher_repo, group_repo, branch_repo,
        student_group_repo, client_repo, visibility,
    )
    student_request_service = StudentRequestService(
        student_request_repo, student_repo, student_group_repo,
    )
    diary_service = DiaryService(
        training_entry_repo, athlete_task_repo, student_repo,
        student_group_repo, group_repo, visibility,
    )
    from bot.services.parent_notifier import ParentNotifier
    notifier = ParentNotifier(tg_bot=tg_bot).set_as_default()
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
    dp["subscription_override_repo"] = subscription_override_repo
    dp["finance_entry_repo"] = finance_entry_repo
    dp["rate_history_repo"] = rate_history_repo
    dp["payout_repo"] = payout_repo
    dp["salary_override_repo"] = salary_override_repo
    dp["salary_service"] = salary_service
    dp["lesson_service"] = lesson_service
    dp["payment_service"] = payment_service
    dp["profit_service"] = profit_service
    dp["diagnostics_service"] = diagnostics_service
    dp["visibility"] = visibility
    dp["student_service"] = student_service
    dp["student_request_service"] = student_request_service
    dp["cloudkassir_service"] = cloudkassir_service
    dp["notifier"] = notifier
    dp["training_entry_repo"] = training_entry_repo
    dp["athlete_task_repo"] = athlete_task_repo
    dp["diary_service"] = diary_service

    # ── Middleware ────────────────────────────────────────────────────────────
    dp.update.outer_middleware(DedupUpdateMiddleware())
    dp.update.middleware(AuthMiddleware(user_repo))

    # ── Роутеры ──────────────────────────────────────────────────────────────
    dp.include_routers(common_router, admin_router, teacher_router, athlete_router, client_router)

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


def _register_miniapp_api(app, dp: Dispatcher, bot=None) -> None:
    """Регистрирует HTTP API личного кабинета (Telegram Mini App) и его фронт (/app/)."""
    from bot.api import (
        register_admin_api, register_miniapp_api, register_miniapp_static,
        register_parent_api, register_teacher_api,
    )
    register_miniapp_api(app, dp, bot)
    register_admin_api(app, dp, bot)
    register_teacher_api(app, dp, bot)
    register_parent_api(app, dp, bot)
    register_miniapp_static(app)


async def _rate_history_refresher(dp: Dispatcher, interval_sec: int = 300) -> None:
    """Держит в памяти историю ставок педагогов (лист teacher_rate_history)."""
    from bot.services import rate_history
    repo = dp["rate_history_repo"]
    while True:
        try:
            rows = await repo.get_all()
            rate_history.load(rows)
            logger.debug("История ставок загружена: %d строк", len(rows))
        except Exception as exc:  # лист может отсутствовать — работаем по карточкам
            logger.warning("История ставок недоступна (%s) — используются текущие ставки", exc)
        await asyncio.sleep(interval_sec)


async def _run_webhook(bot: Bot, dp: Dispatcher) -> None:
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    assert settings.webhook_url, "WEBHOOK_URL пуст — режим webhook невозможен"
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
    _register_miniapp_api(app, dp, bot)

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

    # Отдельный aiohttp-сервер: webhook ЮКассы + API Mini App
    app = web.Application()
    _register_payment_webhook(app, dp, bot)
    _register_miniapp_api(app, dp, bot)
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
    dp = _build_dispatcher(storage, tg_bot=bot)

    await bot.set_my_commands([
        BotCommand(command="start", description="Запуск / главное меню"),
        BotCommand(command="menu", description="Главное меню"),
    ])

    # История ставок педагогов: первая загрузка до старта, дальше — фоновое обновление
    refresher = asyncio.create_task(_rate_history_refresher(dp))
    await asyncio.sleep(0)  # дать задаче выполнить первую загрузку

    # Бот в MAX (кабинет родителя) — в том же процессе, на тех же репозиториях
    max_task = None
    if settings.max_bot_token:
        import bot.max as max_front
        if max_front.available:
            max_bot, max_dp = max_front.build_max(settings.max_bot_token, dict(dp.workflow_data), bot)
            dp["notifier"].max_bot = max_bot
            from bot.max.app import run_max
            max_task = asyncio.create_task(run_max(max_bot, max_dp))
        else:
            logger.warning("MAX_BOT_TOKEN задан, но библиотека maxapi не установлена — MAX отключён")
    else:
        logger.info("MAX отключён (MAX_BOT_TOKEN пуст)")

    try:
        if settings.webhook_url:
            await _run_webhook(bot, dp)
        else:
            await _run_polling(bot, dp)
    finally:
        refresher.cancel()
        if max_task is not None:
            max_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
