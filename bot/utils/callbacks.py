"""Именованный разбор и сборка callback_data — строки кнопок не меняются байт-в-байт.

Callback-строки бота — `prefix:часть:часть`. Здесь для них объявлены датаклассы с
`unpack()` / `pack()`, чтобы хендлеры не разбирали строку вручную и не считали
позиции. Старые кнопки в чатах продолжают работать: `unpack` принимает ровно те
строки, что собирали кнопки раньше; `pack` воспроизводит их без изменений —
это проверяет roundtrip-тест по корпусу шаблонов кнопок из исходников
(tests/test_callbacks.py).

Правила схемы:
- поля датакласса идут через `:` в порядке объявления, типы — `str` или `int`;
- поле со значением по умолчанию — необязательный хвост: при `unpack` отсутствующие
  хвосты получают default, при `pack` хвосты, равные default, опускаются;
- `tail = True` — последнее поле может содержать `:` (как `split(":", n)`);
- `unpack` бросает ValueError на строке не той формы — так же, как раньше падал
  `split`/распаковка кортежа; хендлеры, где это «Ошибка данных», ловят его сами.

aiogram `CallbackData` не используется намеренно: он всегда пишет все поля
(`prefix:a:b:`), что изменило бы существующие строки и сломало старые кнопки.
"""
from __future__ import annotations

from dataclasses import MISSING, dataclass, fields
from typing import ClassVar, get_type_hints


class Callback:
    """База: подкласс — датакласс с полями-частями callback и `prefix`."""

    prefix: ClassVar[str]
    tail: ClassVar[bool] = False

    @classmethod
    def unpack(cls, data: str | None):
        head = cls.prefix + ":"
        if not data or not data.startswith(head):
            raise ValueError(f"{cls.__name__}: ожидался callback «{head}…», получено {data!r}")
        rest = data[len(head):]
        specs = fields(cls)  # type: ignore[arg-type]
        required = sum(1 for f in specs if f.default is MISSING)
        values = rest.split(":", len(specs) - 1) if cls.tail else rest.split(":")
        if len(values) < required or len(values) > len(specs):
            raise ValueError(f"{cls.__name__}: неверное число частей в {data!r}")
        hints = get_type_hints(cls)
        kwargs = {}
        for spec, raw in zip(specs, values, strict=False):
            kwargs[spec.name] = int(raw) if hints.get(spec.name) is int else raw
        return cls(**kwargs)

    def pack(self) -> str:
        specs = fields(self)  # type: ignore[arg-type]
        values = [getattr(self, f.name) for f in specs]
        # необязательные хвосты, равные default, не печатаем — как собирали кнопки раньше
        while values and specs[len(values) - 1].default is not MISSING and values[-1] == specs[len(values) - 1].default:
            values.pop()
        return ":".join([self.prefix, *map(str, values)])


# ─── Админ: занятия педагога (edit_lesson) ────────────────────────────────────

@dataclass(frozen=True)
class AdminLessonsTeacherCb(Callback):
    prefix = "aedl_t"
    teacher_id: str


@dataclass(frozen=True)
class TeacherCardLessonsCb(Callback):
    prefix = "tc_lessons"
    teacher_id: str


@dataclass(frozen=True)
class AdminLessonsDatesCb(Callback):
    prefix = "aedl_dates"
    teacher_id: str


@dataclass(frozen=True)
class AdminLessonsPickDayCb(Callback):
    prefix = "aedl_pick"
    teacher_id: str
    day: str


@dataclass(frozen=True)
class AdminLessonsAllCb(Callback):
    prefix = "aedl_all"
    teacher_id: str


@dataclass(frozen=True)
class AdminLessonsMonthPickCb(Callback):
    prefix = "aedl_month_pick"
    teacher_id: str


@dataclass(frozen=True)
class AdminLessonsMonthCb(Callback):
    prefix = "aedl_month"
    teacher_id: str
    ym: str


@dataclass(frozen=True)
class AdminLessonsCalendarCb(Callback):
    prefix = "aedl_manual"
    teacher_id: str


@dataclass(frozen=True)
class AdminLessonsCalNavCb(Callback):
    prefix = "aedlc_nav"
    ym: str


@dataclass(frozen=True)
class AdminLessonsCalPickCb(Callback):
    prefix = "aedlc_pick"
    day: str


@dataclass(frozen=True)
class AdminLessonsTypeCb(Callback):
    prefix = "aedl_type"
    teacher_id: str
    type_code: str
    tag: str


# ─── Админ: группы филиала (branches/groups) ──────────────────────────────────

@dataclass(frozen=True)
class GroupAddCb(Callback):
    prefix = "group:add"
    branch_id: str


@dataclass(frozen=True)
class GroupCardCb(Callback):
    prefix = "group_card"
    group_id: str


@dataclass(frozen=True)
class GroupArchiveCb(Callback):
    prefix = "group_arch"
    action: str  # on | off
    group_id: str


@dataclass(frozen=True)
class GroupRenamePickCb(Callback):
    prefix = "group:rename_pick"
    branch_id: str


@dataclass(frozen=True)
class GroupEditNameCb(Callback):
    prefix = "group:edit_name"
    branch_id: str
    group_id: str


@dataclass(frozen=True)
class GroupDelPickCb(Callback):
    prefix = "group:del_pick"
    branch_id: str


@dataclass(frozen=True)
class GroupDelCb(Callback):
    prefix = "group:del"
    group_id: str


@dataclass(frozen=True)
class ConfirmDelGroupCb(Callback):
    prefix = "confirm_del_group"
    group_id: str


@dataclass(frozen=True)
class GroupTeachersCb(Callback):
    prefix = "group_teachers"
    group_id: str


@dataclass(frozen=True)
class GroupTeacherToggleCb(Callback):
    prefix = "gt_toggle"
    group_id: str
    teacher_id: str


# ─── Родитель: оплата (client/my_bills/payment) ───────────────────────────────

@dataclass(frozen=True)
class ClientPayCb(Callback):
    """client_pay:{sid}:{period}[:{teacher_id}] — из «Занятий» педагог предвыбран."""
    prefix = "client_pay"
    tail = True
    student_id: str
    period_month: str
    teacher_id: str = ""


@dataclass(frozen=True)
class PaySelectToggleCb(Callback):
    prefix = "pselt"
    idx: int


@dataclass(frozen=True)
class PayMethodCb(Callback):
    prefix = "pay_method"
    tail = True
    method: str
    student_id: str
    period_month: str


@dataclass(frozen=True)
class CashNotifyCb(Callback):
    prefix = "cash_notify"
    tail = True
    student_id: str
    period_month: str


@dataclass(frozen=True)
class ReceiptUploadCb(Callback):
    prefix = "receipt_upload"
    tail = True
    method: str
    student_id: str
    period_month: str


@dataclass(frozen=True)
class ReceiptRejectCb(Callback):
    prefix = "rcpt_no"
    tail = True
    student_id: str
    period_month: str
    parent_raw: str


@dataclass(frozen=True)
class ReceiptPickCb(Callback):
    prefix = "rcpick"
    tail = True
    student_id: str
    period_month: str


class _ReceiptConfirmMixin:
    """Хвост `[:{сумма}]:{код способа}`: сумма — если сегмент из цифр, код — следующий."""

    tail_raw: str

    @property
    def _segments(self) -> list[str]:
        return self.tail_raw.split(":") if self.tail_raw else []

    @property
    def claimed(self) -> int | None:
        """Сумма из чека; None у старых кнопок без суммы."""
        seg = self._segments
        return int(seg[0]) if seg and seg[0].isdigit() else None

    @property
    def method_code(self) -> str:
        seg = self._segments
        idx = 1 if self.claimed is not None else 0
        return seg[idx] if len(seg) > idx else ""


@dataclass(frozen=True)
class ReceiptConfirmPartialCb(_ReceiptConfirmMixin, Callback):
    """rcpp:{sid}:{period}:{pids}[:{сумма}]:{код} — подтверждение выбранных счетов."""
    prefix = "rcpp"
    tail = True
    student_id: str
    period_month: str
    pids: str
    tail_raw: str = ""


@dataclass(frozen=True)
class ReceiptConfirmCb(_ReceiptConfirmMixin, Callback):
    """receipt_confirm:{sid}:{period}[:{сумма}]:{код}."""
    prefix = "receipt_confirm"
    tail = True
    student_id: str
    period_month: str
    tail_raw: str = ""


# ─── Админ: подтверждение оплат (admin/bills/confirm) ─────────────────────────

@dataclass(frozen=True)
class PayConfirmPeriodCb(Callback):
    prefix = "pcp"
    period: str


@dataclass(frozen=True)
class PayConfirmBranchCb(Callback):
    prefix = "pcpb"
    tail = True
    period: str
    branch_id: str


@dataclass(frozen=True)
class PayConfirmGroupCb(Callback):
    prefix = "pcpg"
    tail = True
    period: str
    group_id: str


@dataclass(frozen=True)
class PayConfirmStudentCb(Callback):
    prefix = "pcps"
    tail = True
    period: str
    group_id: str
    student_id: str


@dataclass(frozen=True)
class PayInvoiceCb(Callback):
    """pay_invoice:{payment_id}[:{group_id}] — без группы возврат ведёт в «none»."""
    prefix = "pay_invoice"
    payment_id: str
    group_id: str = "none"


@dataclass(frozen=True)
class DoConfirmPaymentCb(Callback):
    prefix = "do_confirm_payment"
    payment_id: str
    group_id: str = "none"


@dataclass(frozen=True)
class PaySelectLessonsCb(Callback):
    prefix = "paysel"
    tail = True
    payment_id: str
    group_id: str


@dataclass(frozen=True)
class PaySelectLessonToggleCb(Callback):
    prefix = "pslt"
    idx: int


@dataclass(frozen=True)
class PaySelectApplyCb(Callback):
    prefix = "pslok"
    amount: int


# ─── Родитель: занятия (client/my_lessons) ────────────────────────────────────

@dataclass(frozen=True)
class ClientStudentCb(Callback):
    prefix = "cl_stu"
    student_id: str


@dataclass(frozen=True)
class ClientDateCb(Callback):
    prefix = "cl_date"
    tail = True
    student_id: str
    date: str


@dataclass(frozen=True)
class ClientCalendarCb(Callback):
    prefix = "cl_calendar_s"
    student_id: str


@dataclass(frozen=True)
class ClientCalNavCb(Callback):
    prefix = "cl_nav"
    ym: str


@dataclass(frozen=True)
class ClientCalPickCb(Callback):
    prefix = "cl_pick"
    date: str


@dataclass(frozen=True)
class ClientMonthListCb(Callback):
    prefix = "cl_month_list_s"
    student_id: str


@dataclass(frozen=True)
class ClientMonthTeacherCb(Callback):
    prefix = "cl_month_t"
    tail = True
    student_id: str
    period: str
    teacher_id: str


@dataclass(frozen=True)
class ClientMonthCb(Callback):
    prefix = "cl_month"
    tail = True
    student_id: str
    period: str


# ─── Админ: зарплаты (admin/salaries) ─────────────────────────────────────────

@dataclass(frozen=True)
class SalaryTeacherCb(Callback):
    prefix = "salary_teacher"
    teacher_id: str


@dataclass(frozen=True)
class TeacherCardSalaryCb(Callback):
    prefix = "tc_salary"
    teacher_id: str


@dataclass(frozen=True)
class SalaryPeriodCb(Callback):
    prefix = "salary_period"
    tail = True
    teacher_id: str
    period_month: str


@dataclass(frozen=True)
class SalaryDayCb(Callback):
    prefix = "salary_day"
    teacher_id: str


@dataclass(frozen=True)
class SalaryDayCalendarCb(Callback):
    prefix = "salary_dday_cal"
    teacher_id: str


@dataclass(frozen=True)
class SalaryDayCalNavCb(Callback):
    prefix = "salary_dday_nav"
    ym: str


@dataclass(frozen=True)
class SalaryDayCalPickCb(Callback):
    prefix = "salary_dday_pick"
    date: str


@dataclass(frozen=True)
class SalaryDayShowCb(Callback):
    prefix = "salary_day_show"
    tail = True
    teacher_id: str
    date: str


def registry() -> list[type[Callback]]:
    """Все объявленные callback-классы (для тестов и диагностики)."""
    out: list[type[Callback]] = []
    stack = list(Callback.__subclasses__())
    while stack:
        cls = stack.pop()
        if hasattr(cls, "prefix"):
            out.append(cls)
        stack.extend(cls.__subclasses__())
    return sorted(out, key=lambda c: c.prefix)
