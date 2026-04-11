from enum import Enum


class LessonType(str, Enum):
    GROUP = "group"
    INDIVIDUAL = "individual"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
