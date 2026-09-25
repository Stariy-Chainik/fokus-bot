"""Карточки (ученик) — чистая сборка текста и параметров клавиатуры из DTO сервисов.

Хендлеры admin/students и teacher/partners только показывают результат; общие
фрагменты (подпись партнёра) живут здесь один раз.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bot.models import GroupBillingMode, Student, StudentGroupTier

if TYPE_CHECKING:
    from bot.services.student_service import StudentCard


def partner_label(student: Student, partner: Student | None) -> str:
    """«— (солист)» / имя партнёра / «(удалён: STU-…)» при битой ссылке."""
    if not student.partner_id:
        return "— (солист)"
    return partner.name if partner else f"(удалён: {student.partner_id})"


@dataclass
class AdminStudentCardView:
    text: str
    tier_toggle: tuple[str, str] | None = None
    client_rows: list = field(default_factory=list)   # [[(label, callback)], ...] для kb_student_card
    has_partner: bool = False
    has_groups: bool = False


def admin_student_card_view(card: StudentCard) -> AdminStudentCardView:
    """Текст карточки ученика админа и параметры её клавиатуры."""
    student_id = card.student.student_id
    student = card.student
    if card.teacher_names:
        teachers_text = "\n".join(f"  • {name}" for name in card.teacher_names)
    else:
        teachers_text = "  не привязан"

    partner_text = partner_label(student, card.partner)

    # Блок «Группы» — список с филиалом; тариф показываем для группы с dual-pricing.
    primary_group = card.primary_group
    if card.groups:
        group_lines: list[str] = []
        for row in card.groups:
            g = row.group
            if g:
                mode_marker = ""
                if g.billing_mode == GroupBillingMode.PER_VISIT:
                    mode_marker = f" · 💰 {g.price_full}₽/{g.duration_full}м"
                group_lines.append(f"  • <b>{g.name}</b> ({row.branch_name}){mode_marker}")
            else:
                group_lines.append(f"  • (не найдена: {row.group_id})")
        groups_block = "\n".join(group_lines)
    else:
        groups_block = "  <i>не задана</i>"

    tier_toggle = None
    tier_line = ""
    # Блок тарифа — только если есть группа с коротким тарифом (PER_VISIT и price_short > 0):
    # у «ЮБ сад ХГ» его нет, и карточка ученика про короткий тариф молчит.
    tariff_group = getattr(card, "tariff_group", None)
    if tariff_group is not None:
        primary_group = tariff_group
        if student.group_tier == StudentGroupTier.SHORT:
            tier_line = (
                f"\n🕐 Тариф: <b>короткий</b> — "
                f"{primary_group.duration_short} мин / {primary_group.price_short}₽"
            )
            tier_toggle = (
                student.group_tier.value,
                f"🕐 Переключить на полный ({primary_group.duration_full} мин / {primary_group.price_full}₽)",
            )
        else:
            tier_line = (
                f"\n🕐 Тариф: <b>полный</b> — "
                f"{primary_group.duration_full} мин / {primary_group.price_full}₽"
            )
            tier_toggle = (
                student.group_tier.value,
                f"🕐 Переключить на короткий ({primary_group.duration_short} мин / {primary_group.price_short}₽)",
            )

    # Блок клиента
    client_text = "\n\n👤 Клиент: не задан"
    client_rows: list = []
    if student.client_id:
        client = card.client
        if client:
            phone_hint = f" · тел: {client.phone}" if client.phone else ""
            if client.tg_id:
                client_text = f"\n\n👤 Клиент: {client.name} ✅"
            else:
                client_text = f"\n\n👤 Клиент: {client.name} (ожидает входа{phone_hint})"
            client_rows = [[("Отвязать клиента", f"student_client_unbind:{student_id}")]]
        else:
            client_rows = [[("👤 Создать клиента", f"student_client_create:{student_id}")]]
    else:
        client_rows = [[("👤 Создать клиента", f"student_client_create:{student_id}")]]

    athlete_text = ""
    client_rows.append([("🔗 Ссылка для спортсмена", f"athreg:link:{student_id}")])
    if student.athlete_tg_id:
        athlete_text = f"\n🏃 Спортсмен: кабинет привязан (<code>{student.athlete_tg_id}</code>)"
        client_rows.append([("🚫 Отвязать спортсмена", f"athreg:unlink:{student_id}:{student.athlete_tg_id}")])

    text = (
        f"👩‍🎓 <b>{student.name}</b>\n"
        f"ID: {student.student_id}\n"
        f"🏢 Группы:\n{groups_block}"
        f"{tier_line}\n\n"
        f"Педагоги:\n{teachers_text}\n\n"
        f"Партнёр: {partner_text}"
        f"{client_text}{athlete_text}"
    )
    return AdminStudentCardView(
        text=text, tier_toggle=tier_toggle, client_rows=client_rows,
        has_partner=bool(student.partner_id), has_groups=bool(student.group_ids),
    )
