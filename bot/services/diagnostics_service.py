import logging
from dataclasses import dataclass

from bot.models.enums import LessonType
from bot.repositories import LessonRepository, BillingRepository, TeacherRepository
from .billing_service import BillingService

logger = logging.getLogger(__name__)


@dataclass
class DiagnosticsReport:
    individual_without_billing: list[str]   # lesson_id
    billing_without_lesson: list[str]        # billing_id
    rebuilt_billing_count: int
    errors: list[str]


class DiagnosticsService:
    def __init__(
        self,
        lesson_repo: LessonRepository,
        billing_repo: BillingRepository,
        teacher_repo: TeacherRepository,
    ) -> None:
        self._lesson_repo = lesson_repo
        self._billing_repo = billing_repo
        self._teacher_repo = teacher_repo

    async def find_individual_without_billing(self) -> list[str]:
        all_lessons = await self._lesson_repo.get_all()
        billed_ids = {b.lesson_id for b in await self._billing_repo.get_all()}
        return [
            ls.lesson_id for ls in all_lessons
            if ls.type == LessonType.INDIVIDUAL and ls.lesson_id not in billed_ids
        ]

    async def find_billing_without_lesson(self) -> list[str]:
        lesson_ids = {ls.lesson_id for ls in await self._lesson_repo.get_all()}
        return [
            b.billing_id for b in await self._billing_repo.get_all()
            if b.lesson_id not in lesson_ids
        ]

    async def rebuild_billing(self) -> DiagnosticsReport:
        """
        Полная пересборка billing из lessons:
        1. Удаляет все текущие billing-строки
        2. Для каждого individual-занятия пересчитывает и создаёт billing
        """
        logger.info("Начало пересборки billing из lessons")
        errors: list[str] = []

        await self._billing_repo.delete_all()
        logger.info("Все billing-строки удалены")

        all_lessons = await self._lesson_repo.get_all()
        rebuilt = 0

        for ls in all_lessons:
            if ls.type == LessonType.GROUP:
                continue
            teacher = await self._teacher_repo.get_by_id(ls.teacher_id)
            if teacher is None:
                msg = f"Педагог {ls.teacher_id} не найден для занятия {ls.lesson_id}"
                logger.warning(msg)
                errors.append(msg)
                continue
            try:
                existing_ids = await self._billing_repo.get_existing_ids()
                rows = BillingService.build_billing_rows(ls, teacher, existing_ids)
                for row in rows:
                    await self._billing_repo.add(row)
                    rebuilt += 1
            except Exception as exc:
                msg = f"Ошибка пересборки billing для {ls.lesson_id}: {exc}"
                logger.error(msg)
                errors.append(msg)

        logger.info("Пересборка billing завершена, создано строк: %d", rebuilt)
        return DiagnosticsReport(
            individual_without_billing=[],
            billing_without_lesson=[],
            rebuilt_billing_count=rebuilt,
            errors=errors,
        )

    async def run_consistency_check(self) -> DiagnosticsReport:
        without_billing = await self.find_individual_without_billing()
        without_lesson = await self.find_billing_without_lesson()
        logger.info(
            "Диагностика: individual без billing=%d, billing без lesson=%d",
            len(without_billing), len(without_lesson),
        )
        return DiagnosticsReport(
            individual_without_billing=without_billing,
            billing_without_lesson=without_lesson,
            rebuilt_billing_count=0,
            errors=[],
        )
