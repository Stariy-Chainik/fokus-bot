"""Ссылки-приглашения в группу: t.me/<bot>?start=g_<group_id>_<token>.

Токен — усечённый HMAC-SHA256 от group_id с секретом из настроек
(GROUP_LINK_SECRET, по умолчанию BOT_TOKEN). Хранить токены не нужно:
проверка выполняется пересчётом. Ротация всех ссылок = смена секрета.

Формат payload совместим с Mini App: тот же код валиден и для
?start= (бот), и для ?startapp= (веб-кабинет в будущем).
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Optional

_PREFIX = "g"
_TOKEN_LEN = 6  # hex-символов; защита «от посторонних», не криптография


def group_link_token(group_id: str, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), group_id.encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    return digest[:_TOKEN_LEN]


def build_start_payload(group_id: str, secret: str) -> str:
    """Например: g_GRP-0017_a3f9c1 (только [A-Za-z0-9_-] — требование Telegram)."""
    return f"{_PREFIX}_{group_id}_{group_link_token(group_id, secret)}"


def parse_start_payload(payload: str, secret: str) -> Optional[str]:
    """Возвращает group_id при валидном токене, иначе None."""
    parts = (payload or "").split("_")
    if len(parts) != 3 or parts[0] != _PREFIX:
        return None
    group_id, token = parts[1], parts[2]
    if not group_id or not token:
        return None
    expected = group_link_token(group_id, secret)
    if not hmac.compare_digest(token.lower(), expected):
        return None
    return group_id


# ─── Персональная ссылка спортсмена: t.me/<bot>?start=a_<student_id>_<token> ───
_ATHLETE_PREFIX = "a"


def athlete_link_token(student_id: str, secret: str) -> str:
    """Токен отличается от группового: HMAC берётся от «a:<student_id>»."""
    return group_link_token(f"{_ATHLETE_PREFIX}:{student_id}", secret)


def build_athlete_payload(student_id: str, secret: str) -> str:
    """Например: a_STU-0193_9f1c2b."""
    return f"{_ATHLETE_PREFIX}_{student_id}_{athlete_link_token(student_id, secret)}"


def parse_athlete_payload(payload: str, secret: str) -> Optional[str]:
    """Возвращает student_id при валидном токене, иначе None."""
    parts = (payload or "").split("_")
    if len(parts) != 3 or parts[0] != _ATHLETE_PREFIX:
        return None
    student_id, token = parts[1], parts[2]
    if not student_id or not token:
        return None
    if not hmac.compare_digest(token.lower(), athlete_link_token(student_id, secret)):
        return None
    return student_id
