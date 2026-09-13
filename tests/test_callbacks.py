"""bot/utils/callbacks: roundtrip по корпусу кнопок из исходников + необязательные хвосты."""
import io
import re
import tokenize
from pathlib import Path

import pytest

from bot.utils import callbacks as cbm
from bot.utils.callbacks import (
    Callback, ClientPayCb, PayInvoiceCb, PayMethodCb, PaySelectToggleCb, ReceiptConfirmCb,
    ReceiptConfirmPartialCb, registry,
)

BOT = Path(__file__).resolve().parent.parent / "bot"
RE_PLACEHOLDER = re.compile(r"\{[^{}]*\}")

# Callback, которые собираются не литералом с известным префиксом (календарь kb_calendar
# получает prefix параметром) — для них образцы заданы вручную.
MANUAL_SAMPLES = {
    "aedl_t": ["aedl_t:TCH-0001"], "salary_teacher": ["salary_teacher:TCH-0001"],  # kb_teacher_list(action_prefix)
    "aedl_type": ["aedl_type:TCH-0001:g:m-2026-09"],  # в kb_lesson_list собирается по частям
    "aedlc_nav": ["aedlc_nav:2026-09"], "aedlc_pick": ["aedlc_pick:2026-09-12"],
    "cl_nav": ["cl_nav:2026-09"], "cl_pick": ["cl_pick:2026-09-12"],
    "salary_dday_nav": ["salary_dday_nav:2026-09"], "salary_dday_pick": ["salary_dday_pick:2026-09-12"],
}


def _literals(src: str):
    """Строковые литералы файла, включая f-строки (Python 3.12 токенизирует их по частям):
    выражения в {…} заменяются на «7»."""
    buf = None
    depth = 0
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.FSTRING_START:
            buf, depth = [], 0
        elif tok.type == tokenize.FSTRING_MIDDLE and buf is not None and depth == 0:
            buf.append(tok.string)
        elif tok.type == tokenize.OP and buf is not None and tok.string == "{":
            if depth == 0:
                buf.append("7")
            depth += 1
        elif tok.type == tokenize.OP and buf is not None and tok.string == "}":
            depth -= 1
        elif tok.type == tokenize.FSTRING_END and buf is not None:
            yield "".join(buf)
            buf = None
        elif tok.type == tokenize.STRING:
            raw = tok.string.lstrip("rbuRBU")
            if raw[:3] in ('"""', "'''") or len(raw) < 2:
                continue
            yield raw[1:-1]


def _corpus() -> list[tuple[str, str]]:
    """Литералы bot/, похожие на callback: без пробелов, с префиксом известного класса.

    Так ловятся и callback_data=..., и cb("…", "…") экранов, и сборка через переменные.
    """
    prefixes = tuple(c.prefix + ":" for c in registry())
    out = []
    for path in sorted(BOT.rglob("*.py")):
        for sample in _literals(path.read_text(encoding="utf-8")):
            while RE_PLACEHOLDER.search(sample):
                sample = RE_PLACEHOLDER.sub("7", sample)
            # голый «prefix:» — это фильтр хендлера (F.data.startswith), а не кнопка
            if " " in sample or not sample.startswith(prefixes) or sample.endswith(":"):
                continue
            out.append((sample, str(path.relative_to(BOT))))
    return out


# Литералы-«заготовки» префикса (дальше строка достраивается в клавиатуре) — не кнопки.
PARTIAL_BUILDERS = {"aedl_type:7"}


@pytest.mark.parametrize("cls", registry(), ids=lambda c: c.prefix)
def test_every_callback_class_roundtrips_its_buttons(cls):
    samples = [s for s, _ in _corpus() if s.startswith(cls.prefix + ":") and s not in PARTIAL_BUILDERS]
    samples += MANUAL_SAMPLES.get(cls.prefix, [])
    assert samples, f"кнопки с префиксом {cls.prefix!r} в исходниках не найдены"
    for sample in samples:
        assert cls.unpack(sample).pack() == sample, (cls.__name__, sample)


def test_registry_prefixes_unique_and_declared():
    prefixes = [c.prefix for c in registry()]
    assert len(prefixes) == len(set(prefixes))
    assert all(issubclass(c, Callback) for c in registry())
    assert cbm.GroupDelCb.prefix == "group:del" and cbm.GroupDelPickCb.prefix == "group:del_pick"


def test_unpack_errors_like_old_split():
    with pytest.raises(ValueError):
        PayMethodCb.unpack("pay_method:cash:STU-1")            # не хватает частей (было len(parts) < 4)
    with pytest.raises(ValueError):
        PaySelectToggleCb.unpack("pselt:x")                     # int("x")
    with pytest.raises(ValueError):
        ClientPayCb.unpack("client_bill:STU-1:2026-09")         # чужой префикс
    with pytest.raises(ValueError):
        cbm.GroupCardCb.unpack(None)


def test_optional_tail_and_tail_field():
    assert ClientPayCb.unpack("client_pay:STU-1:2026-09") == ClientPayCb("STU-1", "2026-09", "")
    assert ClientPayCb.unpack("client_pay:STU-1:2026-09:TCH-2:x").teacher_id == "TCH-2:x"   # split(":", 3)
    assert ClientPayCb("STU-1", "2026-09").pack() == "client_pay:STU-1:2026-09"
    assert ClientPayCb("STU-1", "2026-09", "TCH-2").pack() == "client_pay:STU-1:2026-09:TCH-2"
    assert PayInvoiceCb.unpack("pay_invoice:PAY-000001").group_id == "none"
    assert PayInvoiceCb("PAY-000001").pack() == "pay_invoice:PAY-000001"
    assert PayInvoiceCb("PAY-000001", "GRP-1").pack() == "pay_invoice:PAY-000001:GRP-1"
    assert PayMethodCb.unpack("pay_method:bank:STU-1:2026-09").period_month == "2026-09"


def test_receipt_confirm_tail_variants():
    """Сумма из чека и код способа — позиция кода зависит от наличия суммы (старая логика parts[])."""
    c = ReceiptConfirmPartialCb.unpack("rcpp:STU-1:2026-09:12.15:2600:b")
    assert (c.pids, c.claimed, c.method_code) == ("12.15", 2600, "b")
    c = ReceiptConfirmPartialCb.unpack("rcpp:STU-1:2026-09:12:b")
    assert (c.claimed, c.method_code) == (None, "b")
    c = ReceiptConfirmPartialCb.unpack("rcpp:STU-1:2026-09:12")
    assert (c.claimed, c.method_code) == (None, "")
    c = ReceiptConfirmPartialCb.unpack("rcpp:STU-1:2026-09:12:0:c")
    assert (c.claimed, c.method_code) == (0, "c")
    c = ReceiptConfirmCb.unpack("receipt_confirm:STU-1:2026-09:800:a")
    assert (c.student_id, c.claimed, c.method_code) == ("STU-1", 800, "a")
    assert ReceiptConfirmCb.unpack("receipt_confirm:STU-1:2026-09").claimed is None
    assert ReceiptConfirmCb.unpack("receipt_confirm:STU-1:2026-09:r").method_code == "r"
    assert ReceiptConfirmCb.unpack("receipt_confirm:STU-1:2026-09:800:a").pack() == "receipt_confirm:STU-1:2026-09:800:a"
