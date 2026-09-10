from maxapi import Router

router = Router(router_id="max_parent")

from . import start, bills, payment  # noqa: E402,F401

__all__ = ["router"]
