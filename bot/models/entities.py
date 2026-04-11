from dataclasses import dataclass, field
from typing import Optional
from .enums import LessonType, PaymentStatus


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
class Student:
    student_id: str
    name: str


@dataclass
class TeacherStudent:
    teacher_id: str
    student_id: str


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
