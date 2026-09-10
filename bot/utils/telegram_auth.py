"""Проверка Telegram Mini App initData.

Алгоритм из документации Telegram WebApp: secret = HMAC_SHA256("WebAppData",
bot_token); подпись — HMAC_SHA256(secret, data_check_string), где
data_check_string — отсортированные пары key=value без hash, через \n.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Optional
from urllib.parse import parse_qsl

DEFAULT_MAX_AGE_SEC = 24 * 60 * 60


def verify_init_data(
    init_data: str, bot_token: str, max_age_sec: int = DEFAULT_MAX_AGE_SEC,
) -> Optional[dict]:
    """Возвращает распарсенные поля initData (user — уже dict) или None.

    None — при битой строке, неверной подписи или истёкшем auth_date.
    """
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except ValueError:
        return None
    received_hash = pairs.pop("hash", "")
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if max_age_sec and time.time() - auth_date > max_age_sec:
        return None

    if "user" in pairs:
        try:
            pairs["user"] = json.loads(pairs["user"])
        except json.JSONDecodeError:
            return None
    return pairs
