from typing import Optional
from bot.models import TeacherPeriodSubmission
from .base import BaseRepository


def _row_to_submission(row: dict) -> TeacherPeriodSubmission:
    return TeacherPeriodSubmission(
        submission_id=str(row["submission_id"]),
        teacher_id=str(row["teacher_id"]),
        period_month=str(row["period_month"]),
        submitted_at=str(row["submitted_at"]),
        lessons_count=int(row.get("lessons_count") or 0),
        total_earned=int(row.get("total_earned") or 0),
    )


class TeacherPeriodSubmissionRepository(BaseRepository):
    async def get_all(self) -> list[TeacherPeriodSubmission]:
        return [_row_to_submission(r) for r in await self._all_records()]

    async def get_by_teacher(self, teacher_id: str) -> list[TeacherPeriodSubmission]:
        return [s for s in await self.get_all() if s.teacher_id == teacher_id]

    async def get_by_teacher_and_period(
        self, teacher_id: str, period_month: str,
    ) -> Optional[TeacherPeriodSubmission]:
        for s in await self.get_all():
            if s.teacher_id == teacher_id and s.period_month == period_month:
                return s
        return None

    async def get_existing_ids(self) -> list[str]:
        return [s.submission_id for s in await self.get_all()]

    async def add(self, sub: TeacherPeriodSubmission) -> TeacherPeriodSubmission:
        await self._append_row([
            sub.submission_id,
            sub.teacher_id,
            sub.period_month,
            sub.submitted_at,
            sub.lessons_count,
            sub.total_earned,
        ])
        return sub
