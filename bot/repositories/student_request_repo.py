import json
import logging
from datetime import datetime
from typing import Optional
from bot.models import StudentRequest
from bot.models.enums import RequestStatus
from .base import BaseRepository

logger = logging.getLogger(__name__)

# Колонки листа `student_requests` (1-based):
# request_id | teacher_id | teacher_tg_id | teacher_name | student_name |
# group_id | status | created_at | resolved_at | resolved_by_tg_id | admin_msgs_json
_STATUS_COL = 7
_RESOLVED_AT_COL = 9
_RESOLVED_BY_COL = 10


def _row_to_request(row: dict) -> StudentRequest:
    rb = row.get("resolved_by_tg_id")
    return StudentRequest(
        request_id=str(row["request_id"]),
        teacher_id=str(row.get("teacher_id") or ""),
        teacher_tg_id=int(row.get("teacher_tg_id") or 0),
        teacher_name=str(row.get("teacher_name") or ""),
        student_name=str(row.get("student_name") or ""),
        group_id=str(row.get("group_id") or ""),
        status=RequestStatus(str(row.get("status") or "pending")),
        created_at=str(row.get("created_at") or ""),
        resolved_at=str(row.get("resolved_at") or "") or None,
        resolved_by_tg_id=int(rb) if rb else None,
        admin_msgs_json=str(row.get("admin_msgs_json") or ""),
    )


class StudentRequestRepository(BaseRepository):
    async def add(
        self, request_id: str, teacher_id: str, teacher_tg_id: int,
        teacher_name: str, student_name: str, group_id: str,
        admin_msgs: list[tuple[int, int]],
    ) -> StudentRequest:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        admin_msgs_json = json.dumps(admin_msgs)
        await self._append_row([
            request_id, teacher_id, teacher_tg_id, teacher_name,
            student_name, group_id, RequestStatus.PENDING.value,
            now, "", "", admin_msgs_json,
        ])
        return StudentRequest(
            request_id=request_id, teacher_id=teacher_id,
            teacher_tg_id=teacher_tg_id, teacher_name=teacher_name,
            student_name=student_name, group_id=group_id,
            status=RequestStatus.PENDING, created_at=now,
            admin_msgs_json=admin_msgs_json,
        )

    async def get_by_id(self, request_id: str) -> Optional[StudentRequest]:
        for r in await self._all_records():
            if str(r.get("request_id")) == request_id:
                return _row_to_request(r)
        return None

    async def get_pending(self) -> list[StudentRequest]:
        return [
            _row_to_request(r) for r in await self._all_records()
            if str(r.get("status") or "") == RequestStatus.PENDING.value
        ]

    async def mark_resolved(
        self, request_id: str, status: RequestStatus, resolved_by_tg_id: int,
    ) -> bool:
        """Перевод заявки из PENDING в APPROVED/REJECTED.
        Возвращает False если заявка не найдена или уже обработана.
        """
        self._invalidate_cache()
        row_idx = await self._find_row_index("request_id", request_id)
        if row_idx is None:
            return False
        req = await self.get_by_id(request_id)
        if req is None or req.status != RequestStatus.PENDING:
            return False
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self._update_cell(row_idx, _STATUS_COL, status.value)
        await self._update_cell(row_idx, _RESOLVED_AT_COL, now)
        await self._update_cell(row_idx, _RESOLVED_BY_COL, resolved_by_tg_id)
        return True

    @staticmethod
    def parse_admin_msgs(req: StudentRequest) -> list[tuple[int, int]]:
        if not req.admin_msgs_json:
            return []
        try:
            raw = json.loads(req.admin_msgs_json)
            return [(int(c), int(m)) for c, m in raw]
        except Exception as exc:
            logger.warning("Не удалось распарсить admin_msgs_json для %s: %s",
                           req.request_id, exc)
            return []
