from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Btn:
    label: str
    kind: str      # "cb" | "url" | "app" (Mini App, только Telegram)
    value: str     # callback payload, URL или адрес Mini App


Rows = list  # list[list[Btn]]
Screen = tuple  # (text: str, rows: Rows)


def cb(label: str, data: str) -> Btn:
    return Btn(label, "cb", data)


def url(label: str, link: str) -> Btn:
    return Btn(label, "url", link)


def app(label: str, link: str) -> Btn:
    return Btn(label, "app", link)
