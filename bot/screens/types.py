from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Btn:
    label: str
    kind: str      # "cb" | "url"
    value: str     # callback payload или URL


Rows = list  # list[list[Btn]]
Screen = tuple  # (text: str, rows: Rows)


def cb(label: str, data: str) -> Btn:
    return Btn(label, "cb", data)


def url(label: str, link: str) -> Btn:
    return Btn(label, "url", link)
