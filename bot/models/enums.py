from enum import Enum


class LessonType(str, Enum):
    GROUP = "group"
    INDIVIDUAL = "individual"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"


class RequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
