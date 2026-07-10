from dataclasses import dataclass, field
from typing import Optional
from .enums import LessonType, PaymentStatus, RequestStatus, GroupBillingMode, StudentGroupTier


@dataclass
class User:
    user_id: str
    tg_id: int
    is_admin: bool
    teacher_id: Optional[str]  # None если просто администратор


@dataclass
class Teacher:
    teacher_id: str
    tg_id: Optional[int]       # может быть пустым при создании
    name: str
    rate_group: int             # рублей за 45 мин
    rate_for_teacher: int       # рублей за 45 мин (индивидуальное)
    rate_for_student: int       # рублей за 45 мин (для счёта ученика)


@dataclass
class Client:
    client_id: str
    name: str
    tg_id: Optional[int] = None
    created_at: str = ""
    phone: Optional[str] = None


@dataclass
class Student:
    student_id: str
    name: str
    partner_id: Optional[str] = None
    group_ids: list[str] = field(default_factory=list)
    group_tier: StudentGroupTier = StudentGroupTier.FULL
    client_id: Optional[str] = None
    parent_tg_ids: list[int] = field(default_factory=list)


@dataclass
class Branch:
    branch_id: str
    name: str
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Group:
    group_id: str
    branch_id: str
    name: str
    created_at: str = ""
    updated_at: str = ""
    sort_order: int = 0
    billing_mode: GroupBillingMode = GroupBillingMode.NONE
    price_short: int = 0
    duration_short: int = 35
    price_full: int = 0
    duration_full: int = 60


@dataclass
class TeacherGroup:
    teacher_id: str
    group_id: str


@dataclass
class SubscriptionOverride:
    """Переопределение цены абонемента на конкретный месяц.

    student_id пуст (None) — для всей группы; задан — для одного ученика.
    Приоритет при начислении: ученик → группа → group.price_full.
    amount = 0 — в этом месяце не начислять (освобождение).
    """
    group_id: str
    period_month: str          # YYYY-MM
    student_id: Optional[str]
    amount: int                # рублей
    created_at: str = ""


@dataclass
class StudentGroup:
    student_id: str
    group_id: str


@dataclass
class Lesson:
    lesson_id: str
    teacher_id: str
    teacher_name: str
    type: LessonType
    student_1_id: Optional[str]
    student_1_name: Optional[str]
    student_2_id: Optional[str]
    student_2_name: Optional[str]
    date: str                  # YYYY-MM-DD
    duration_min: int          # 45 / 60 / 90
    earned: int                # рублей, целое
    recorded_at: str           # YYYY-MM-DD HH:MM:SS
    updated_at: str            # YYYY-MM-DD HH:MM:SS
    # CSV student_id присутствовавших — используется только для group-занятий;
    # для individual всегда пусто (учеников видно по student_1..4_id).
    attendees: Optional[str] = None
    group_id: str = ""  # заполнено только для групповых занятий, если педагог выбрал тренировочную группу
    # INDIVIDUAL занятие может содержать 1–4 учеников; student_3/4 — опциональные
    # дополнительные слоты (для разовых микрогрупп из солистов).
    student_3_id: Optional[str] = None
    student_3_name: Optional[str] = None
    student_4_id: Optional[str] = None
    student_4_name: Optional[str] = None


@dataclass
class Billing:
    billing_id: str
    lesson_id: str
    student_id: str
    student_name: str
    teacher_id: str
    teacher_name: str
    date: str                  # YYYY-MM-DD
    duration_min: int
    amount: int                # рублей, целое
    period_month: str          # YYYY-MM
    payment_id: Optional[str]  # проставляется после оплаты
    created_at: str
    updated_at: str
    lesson_type: Optional[str] = None  # "pair" | "soloist" | "group" — для отображения


@dataclass
class TeacherPeriodSubmission:
    submission_id: str
    teacher_id: str
    period_month: str          # YYYY-MM
    submitted_at: str
    lessons_count: int
    total_earned: int


@dataclass
class StudentRequest:
    request_id: str
    teacher_id: str
    teacher_tg_id: int
    teacher_name: str
    student_name: str
    group_id: str
    status: RequestStatus
    created_at: str                # YYYY-MM-DD HH:MM:SS
    resolved_at: Optional[str] = None
    resolved_by_tg_id: Optional[int] = None
    admin_msgs_json: str = ""      # JSON: [[chat_id, message_id], ...]


@dataclass
class StudentPeriodPayment:
    payment_id: str
    student_id: str
    student_name: str
    period_month: str          # YYYY-MM
    total_amount: int
    status: PaymentStatus
    paid_at: Optional[str]
    confirmed_by_tg_id: Optional[int]
    comment: Optional[str]
    created_at: str
    updated_at: str
    teacher_id: str = ""
    teacher_name: str = ""
