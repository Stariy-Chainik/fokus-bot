"""Архив групп: скрыт из списков, но остаётся доступен по id и в истории."""
from __future__ import annotations

import asyncio

from bot.repositories.group_repo import GroupRepository


def _rows():
    base = {
        "branch_id": "BRN-0001", "created_at": "", "updated_at": "", "sort_order": 0,
        "billing_mode": "per_visit", "price_short": 0, "duration_short": 35,
        "price_full": 800, "duration_full": 60,
    }
    return [
        {**base, "group_id": "GRP-0001", "name": "Активная", "archived": ""},
        {**base, "group_id": "GRP-0002", "name": "Ушедший педагог", "archived": "1"},
    ]


class _Repo(GroupRepository):
    def __init__(self):  # без gspread: подменяем чтение листа
        pass

    async def _all_records(self):
        return _rows()


def _run(coro):
    return asyncio.run(coro)


def test_archived_group_hidden_from_lists():
    repo = _Repo()
    assert [g.group_id for g in _run(repo.get_all())] == ["GRP-0001"]
    assert [g.group_id for g in _run(repo.get_by_branch("BRN-0001"))] == ["GRP-0001"]


def test_archived_group_available_for_history():
    repo = _Repo()
    assert len(_run(repo.get_all(include_archived=True))) == 2
    assert len(_run(repo.get_by_branch("BRN-0001", include_archived=True))) == 2
    group = _run(repo.get_by_id("GRP-0002"))
    assert group is not None and group.archived and group.price_full == 800
