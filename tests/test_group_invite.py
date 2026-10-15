"""«🔗 Пригласить родителей» (решение владельца 15.10.2026): текст со ссылками группы в кабинетах педагога и админа."""
import pytest

from bot.services import group_invite
from bot.utils.group_links import build_start_payload, parse_start_payload
from config.settings import settings
from tests.test_admin_api import ADMIN_TG, _call as admin_call
from tests.test_teacher_api import _call, api  # noqa: F401 — фикстура педагога


@pytest.fixture(autouse=True)
def _usernames(monkeypatch):
    monkeypatch.setattr(group_invite, "_tg_username", "FokusTestBot")
    monkeypatch.setattr(settings, "max_bot_token", "")


def test_text_has_both_links_and_clean_name(monkeypatch):
    full, share = group_invite.invite_text("ВБ ХГ Младшая 🤸‍♀️", "https://t.me/b?start=x", "https://max.ru/m?start=x")
    assert full.startswith("Родители группы «ВБ ХГ Младшая»!")
    assert "Telegram: https://t.me/b?start=x" in full and "MAX: https://max.ru/m?start=x" in full
    assert "t.me" not in share and "MAX: https://max.ru/m?start=x" in share   # в «Поделиться» ссылка Telegram идёт отдельно
    only_tg, _ = group_invite.invite_text("БП Джаз", "https://t.me/b?start=x", "")
    assert "MAX" not in only_tg


def test_teacher_gets_own_group_link_only(api):  # noqa: F811
    app, dp = api
    st, d = _call(app, "GET", "/api/teacher/groups/GRP-0001/invite")
    secret = settings.group_link_secret or settings.bot_token
    assert st == 200 and d["tg"] == f"https://t.me/FokusTestBot?start={build_start_payload('GRP-0001', secret)}"
    assert parse_start_payload(d["tg"].split("start=")[1], secret) == "GRP-0001"      # бот примет эту ссылку
    assert d["max"] == "" and "БП Джаз" in d["text"] and d["tg"] in d["text"]
    assert _call(app, "GET", "/api/teacher/groups/GRP-0404/invite")[0] == 404           # чужая группа


def test_admin_gets_any_group_link(api):  # noqa: F811
    app, dp = api
    st, d = admin_call(app, "GET", "/api/admin/groups/GRP-0001/invite", tg_id=ADMIN_TG)
    assert st == 200 and d["groupName"] == "БП Джаз" and d["tg"].startswith("https://t.me/FokusTestBot?start=g_GRP-0001_")
    assert admin_call(app, "GET", "/api/admin/groups/GRP-0404/invite", tg_id=ADMIN_TG)[0] == 404
