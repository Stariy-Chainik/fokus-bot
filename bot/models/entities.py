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
    email: Optional[str] = None  # для фискальных чеков (колонка 6 листа clients)
    max_id: Optional[int] = None  # аккаунт родителя в MAX (колонка 7)


@dataclass
class Student:
    student_id: str
    name: str
    partner_id: Optional[str] = None
    group_ids: list[str] = field(default_factory=list)
    group_tier: StudentGroupTier = StudentGroupTier.FULL
    client_id: Optional[str] = None
    parent_tg_ids: list[int] = field(default_factory=list)
    athlete_tg_id: Optional[int] = None  # свой Telegram спортсмена (колонка 9)
    parent_max_ids: list[int] = field(default_factory=list)  # родители в MAX (колонка 10)

    @property
    def parent_addrs(self) -> list:
        """Адреса родителей: [("tg", id), ..., ("max", id), ...]."""
        return [("tg", i) for i in self.parent_tg_ids] + [("max", i) for i in self.parent_max_ids]


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
    archived: bool = False   # группа не работает: скрыта из списков, история сохранена


@dataclass
class TeacherGroup:
    teacher_id: str
    group_id: str


@dataclass
class FinanceEntry:
    """Ручная запись дохода/расхода за месяц (экран «Прибыль»).

    Доходы — турниры и прочее (суммы произвольные, периодичность любая);
    расходы — аренда и др. kind: "income" | "expense".
    """
    entry_id: str              # FIN-XXXXXX
    period_month: str          # YYYY-MM
    kind: str                  # income | expense
    title: str                 # «Турнир …», «Аренда» …
    amount: int                # рублей
    created_at: str = ""


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
    joined_period: str = ""  # YYYY-MM, с какого месяца ученик в группе; пусто = «был всегда»
    left_period: str = ""    # YYYY-MM, с какого месяца ушёл (этот месяц уже не оплачивает)

    @property
    def is_active(self) -> bool:
        return not self.left_period

    def covers(self, period_month: str) -> bool:
        """Начислять ли абонемент за этот месяц: ученик числился в группе."""
        if self.joined_period and period_month < self.joined_period:
            return False
        return not (self.left_period and period_month >= self.left_period)


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
    payment_method: str = ""  # cash | receipt_bank | receipt_unknown | yookassa_* | provider_online | admin_manual


@dataclass
class TeacherPayout:
    """Выплата зарплаты педагогу за месяц (может быть несколько: аванс + остаток)."""
    payout_id: str        # PO-XXXXXX
    teacher_id: str
    period_month: str     # YYYY-MM
    amount: int           # рублей
    paid_at: str          # YYYY-MM-DD HH:MM:SS
    paid_by_tg_id: int
    comment: str = ""


@dataclass
class TrainingEntry:
    """Запись дневника спортсмена: самостоятельная тренировка в зале."""
    entry_id: str              # TE-XXXXXX
    student_id: str
    date: str                  # YYYY-MM-DD
    minutes: int
    topics: list[str] = field(default_factory=list)    # танцы/темы
    task_ids: list[str] = field(default_factory=list)  # отработанные задания
    comment: str = ""
    created_at: str = ""
    grade: Optional[int] = None        # 1–5, ставит педагог
    grade_comment: str = ""
    graded_by: str = ""                # teacher_id или ADM:<tg_id>
    graded_at: str = ""


@dataclass
class AthleteTask:
    """Задание педагога спортсмену: упражнение + минуты. Открыто, пока педагог не закроет."""
    task_id: str               # TK-XXXXXX
    student_id: str
    teacher_id: str
    exercise: str
    minutes: int
    comment: str = ""
    source: str = "teacher"    # teacher | lecture
    created_at: str = ""
    status: str = "open"       # open | closed
    closed_at: str = ""
