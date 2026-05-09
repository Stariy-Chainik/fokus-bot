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


class GroupBillingMode(str, Enum):
    NONE = "none"
    PER_VISIT = "per_visit"
    SUBSCRIPTION = "subscription"


class StudentGroupTier(str, Enum):
    FULL = "full"
    SHORT = "short"


class InviteCodeStatus(str, Enum):
    ACTIVE = "active"
    USED = "used"
    EXPIRED = "expired"
    REVOKED = "revoked"
