"""Проверка подписи Telegram Mini App initData."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from bot.utils.telegram_auth import verify_init_data

TOKEN = "42:TEST-TOKEN"


def make_init_data(token: str = TOKEN, age_sec: int = 0, user_id: int = 826576855) -> str:
    fields = {
        "auth_date": str(int(time.time()) - age_sec),
        "query_id": "AAF-test",
        "user": json.dumps({"id": user_id, "first_name": "Тест"}, ensure_ascii=False),
    }
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_valid_init_data_parses_user():
    parsed = verify_init_data(make_init_data(), TOKEN)
    assert parsed is not None
    assert parsed["user"]["id"] == 826576855


def test_wrong_token_rejected():
    assert verify_init_data(make_init_data(token="43:OTHER"), TOKEN) is None


def test_tampered_payload_rejected():
    data = make_init_data().replace("826576855", "111")
    assert verify_init_data(data, TOKEN) is None


def test_expired_rejected():
    data = make_init_data(age_sec=100_000)
    assert verify_init_data(data, TOKEN) is None
    assert verify_init_data(data, TOKEN, max_age_sec=0) is not None  # 0 = без проверки срока


def test_garbage_rejected():
    assert verify_init_data("", TOKEN) is None
    assert verify_init_data("hash=zzz", TOKEN) is None
