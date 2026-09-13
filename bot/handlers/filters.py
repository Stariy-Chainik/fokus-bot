"""Фильтры доступа по роли для callback-хендлеров.

Раньше каждый хендлер начинался с одинакового блока
``if not _is_admin(user): await callback.answer("Нет доступа", show_alert=True); return``.
Теперь роль объявляется в декораторе: ``@router.callback_query(F.data == "…", AdminOnly())``.

Семантика сохранена: при отказе фильтр сам показывает alert «Нет доступа» и
возвращает False. Отклонённый callback уходит дальше по роутерам, но catch-all
хендлеров для callback в проекте нет (это проверяет tests/test_guards_static.py),
поэтому ничего другого не срабатывает — как и раньше при `return` из хендлера.

Гарды message-хендлеров (без alert, `return` молча) остаются внутри хендлеров:
для сообщений «дальше» есть catch-all регистрации родителя, менять маршрут нельзя.
"""
from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import CallbackQuery

from bot.handlers.access import can_teacher_bill, is_admin, is_teacher, is_teacher_or_admin
from bot.models import User

DENIED_TEXT = "Нет доступа"


class _RoleFilter(Filter):
    def allowed(self, user: User | None) -> bool:  # pragma: no cover — переопределяется
        raise NotImplementedError

    async def __call__(self, event: CallbackQuery, user: User | None = None, **_) -> bool:
        if self.allowed(user):
            return True
        await event.answer(DENIED_TEXT, show_alert=True)
        return False


class AdminOnly(_RoleFilter):
    def allowed(self, user: User | None) -> bool:
        return is_admin(user)


class TeacherOnly(_RoleFilter):
    """Строго педагог (админ без teacher_id не проходит) — как is_teacher()."""

    def allowed(self, user: User | None) -> bool:
        return is_teacher(user)


class TeacherOrAdmin(_RoleFilter):
    def allowed(self, user: User | None) -> bool:
        return is_teacher_or_admin(user)


class BillingTeacherOnly(_RoleFilter):
    """Педагог из BILLING_TEACHER_IDS («🧾 Счета моих групп»)."""

    def allowed(self, user: User | None) -> bool:
        return can_teacher_bill(user)
