"""Сверка суммы в чеке с заявкой родителя (решение владельца 06.11.2026).

Случай Мазилиной: родитель прислал чек на 4250 ₽, бот подставил остаток по счёту 7650 ₽, и педагог зачёл 7650.
Теперь чек читает Claude: файл лежит в Telegram (`pending_actions.file_id` — так приходят чеки из Telegram, кабинета
и MAX), фоном скачиваем его, получаем сумму перевода и сравниваем с заявкой.

- совпала — в `pending_actions.receipt_check` пишем `ok:<сумма>` и молчим (сообщений нет, решение владельца);
- не совпала — `diff:<сумма>`, администраторам и педагогам, которые решают заявку, сообщение с кнопкой
  «Зачесть <сумма из чека>» (`pact:` с этой суммой), в «Ждут решения» строка «⚠️ в чеке …»;
- не прочли (не чек, сумма не видна, низкая уверенность) — `unread`, без сообщений.

Чек за несколько месяцев («Оплатить всё») — несколько заявок одного ученика за пару минут: сумма чека сверяется
с их суммой, пометка `…:g`. Проверка ждёт `DELAY_SEC`, чтобы соседние заявки успели записаться, и идёт под замком
ученика: повторные постановки того же чека выходят сразу, увидев пометку. Без `ANTHROPIC_API_KEY` выключено.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from bot.repositories.pending_action_repo import KIND_RECEIPT, OPEN
from config.settings import settings

logger = logging.getLogger(__name__)

DELAY_SEC = 15.0          # соседние заявки одного чека («Оплатить всё») пишутся в лист по одной
GROUP_WINDOW_SEC = 180    # заявки одного ученика ближе этого — один чек на несколько месяцев
MAX_FILE_BYTES = 20 * 1024 * 1024

_deps: dict = {}
_tasks: set = set()
_locks: dict[str, asyncio.Lock] = {}
_client = None


@dataclass
class ReceiptInfo:
    amount: Optional[int]      # сумма перевода, ₽; None — не прочли
    date: str = ""             # ДД.ММ.ГГГГ, если видна


Recognizer = Callable[[bytes, str], Awaitable[Optional[ReceiptInfo]]]


def setup(bot, pending_repo, user_repo) -> None:
    _deps.update(bot=bot, pending=pending_repo, users=user_repo)


def enabled() -> bool:
    return bool(_deps.get("bot") and _deps.get("pending") and settings.receipt_check_enabled)


def schedule(action, file_id: str = "", file_type: str = "", delay: float | None = None) -> None:
    """Поставить сверку в фон. Зовётся при создании заявки с файлом и после `set_file` (чек из MAX)."""
    if action is None or getattr(action, "kind", "") != KIND_RECEIPT or not enabled():
        return
    fid = file_id or action.file_id
    if not fid:
        return
    task = asyncio.get_running_loop().create_task(
        _run(action.action_id, action.student_id, fid, DELAY_SEC if delay is None else delay))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _lock(student_id: str) -> asyncio.Lock:
    if len(_locks) > 512:
        for k in [k for k, lk in _locks.items() if not lk.locked() and k != student_id]:
            _locks.pop(k, None)
    lock = _locks.get(student_id)
    if lock is None:
        lock = _locks[student_id] = asyncio.Lock()
    return lock


async def _run(action_id: str, student_id: str, file_id: str, delay: float) -> None:
    try:
        if delay:
            await asyncio.sleep(delay)
        async with _lock(student_id):
            pending = _deps["pending"]
            open_actions = await pending.get_open()
            action = next((a for a in open_actions if a.action_id == action_id), None)
            if action is None or action.receipt_check:        # решена или уже сверена вместе с соседней
                return
            data, mime = await _download(file_id)
            if data is None:
                return
            info = await _recognizer(data, mime)
            group = _group(open_actions, action)
            if info is None or not info.amount:
                await pending.set_receipt_check(action.action_id, "unread")
                return
            amount = int(info.amount)
            claimed = sum(a.amount for a in group)
            grouped = len(group) > 1 and amount != action.amount
            ok = amount == action.amount or (grouped and amount == claimed)
            value = f"{'ok' if ok else 'diff'}:{amount}" + (":g" if grouped else "")
            for a in (group if grouped else [action]):
                if not a.receipt_check:
                    await pending.set_receipt_check(a.action_id, value)
                    a.receipt_check = value
            logger.info("Сверка чека %s: в чеке %d ₽, в заявке %d ₽ — %s", action.action_id, amount,
                        claimed if grouped else action.amount, "совпало" if ok else "РАСХОЖДЕНИЕ")
            if not ok:
                await _alert(action, group if grouped else [action], amount, info.date)
    except Exception as exc:                        # сверка вспомогательная: заявка и кнопки живут без неё
        logger.warning("Сверка чека %s не выполнена: %s", action_id, exc)


def _group(open_actions: list, action) -> list:
    """Заявки-чеки того же ученика, поданные рядом по времени (один чек за несколько месяцев), включая саму."""
    t0 = _ts(action.created_at)
    out = [a for a in open_actions
           if a.kind == KIND_RECEIPT and a.student_id == action.student_id and a.status == OPEN
           and _near(_ts(a.created_at), t0)]
    return out or [action]


def _near(t1: Optional[datetime], t0: Optional[datetime]) -> bool:
    return t1 is None or t0 is None or abs((t1 - t0).total_seconds()) <= GROUP_WINDOW_SEC


def _ts(raw: str) -> Optional[datetime]:
    try:
        return datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


async def _download(file_id: str) -> tuple[Optional[bytes], str]:
    """Файл чека из Telegram по file_id; (None, "") — не скачали или формат не для распознавания."""
    bot = _deps["bot"]
    try:
        file = await bot.get_file(file_id)
        buf = await bot.download_file(file.file_path)
    except Exception as exc:
        logger.warning("Сверка чека: не скачали файл: %s", exc)
        return None, ""
    data = buf.read() if hasattr(buf, "read") else bytes(buf)
    if not data or len(data) > MAX_FILE_BYTES:
        return None, ""
    mime = sniff_mime(data)
    if not mime:
        logger.info("Сверка чека: формат файла не распознан (%d байт)", len(data))
        return None, ""
    return data, mime


def sniff_mime(data: bytes) -> str:
    head = data[:16]
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"GIF8"):
        return "image/gif"
    return ""


_SCHEMA = {
    "type": "object",
    "properties": {
        "is_receipt": {"type": "boolean"},
        "amount": {"type": "number"},
        "date": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "low"]},
    },
    "required": ["is_receipt", "amount", "date", "confidence"],
    "additionalProperties": False,
}
_SYSTEM = ("Ты читаешь чеки и квитанции о переводах из банковских приложений (СБП, перевод по номеру телефона, "
           "по реквизитам) — фото, скриншоты, PDF. Отвечай только JSON по заданной схеме.")
_PROMPT = ("Это чек об оплате занятий в танцевальном клубе. Найди сумму перевода в рублях — ту, что перечислена "
           "получателю; если отдельно указана комиссия, её не прибавляй. Верни: is_receipt — это чек или квитанция "
           "о переводе; amount — сумма числом в рублях (0, если не видна); date — дата операции в формате ДД.ММ.ГГГГ "
           "(пустая строка, если нет); confidence — high, если сумма читается однозначно, иначе low.")


def _parse_receipt_json(parsed: dict[str, Any]) -> ReceiptInfo:
    if not parsed.get("is_receipt") or parsed.get("confidence") != "high":
        return ReceiptInfo(None, str(parsed.get("date") or ""))
    amount = int(round(float(parsed.get("amount") or 0)))
    return ReceiptInfo(amount if amount > 0 else None, str(parsed.get("date") or ""))


_GEMINI_RECEIPT_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "is_receipt": {"type": "BOOLEAN"},
        "amount": {"type": "NUMBER"},
        "date": {"type": "STRING"},
        "confidence": {"type": "STRING", "enum": ["high", "low"]},
    },
    "required": ["is_receipt", "amount", "date", "confidence"],
}
_VERTEX_EXPRESS_URL = "https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent"


def _effective_receipt_model(api_key: str) -> str:
    model = settings.openai_receipt_model
    if api_key.startswith(("AQ.", "AIza")) and model.startswith("gpt-"):
        return "gemini-2.5-flash"
    return model


async def recognize_vertex_express(data: bytes, mime: str) -> Optional[ReceiptInfo]:
    """Сумма и дата из чека (фото/PDF) через Google Vertex AI Express Mode (ключ AQ....)."""
    import aiohttp

    api_key = settings.openai_api_key.strip()
    model = _effective_receipt_model(api_key)
    b64 = base64.standard_b64encode(data).decode("ascii")
    payload = {
        "systemInstruction": {"parts": [{"text": _SYSTEM}]},
        "contents": [{
            "role": "user",
            "parts": [
                {"inlineData": {"mimeType": mime, "data": b64}},
                {"text": _PROMPT},
            ],
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _GEMINI_RECEIPT_SCHEMA,
            "maxOutputTokens": 512,
        },
    }
    url = _VERTEX_EXPRESS_URL.format(model=model)
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status >= 400:
                body = (await resp.text())[:300]
                raise RuntimeError(f"Vertex Express receipt check HTTP {resp.status}: {body}")
            result = await resp.json()
    parts = (((result.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    text = next((p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")), "")
    return _parse_receipt_json(json.loads(text)) if text else None


async def recognize_openai(data: bytes, mime: str) -> Optional[ReceiptInfo]:
    """Сумма и дата из чека через OpenAI / Gemini AI Studio API (structured output)."""
    import aiohttp

    api_key = settings.openai_api_key.strip()
    model = _effective_receipt_model(api_key)
    base_url = settings.openai_base_url.rstrip("/")
    if api_key.startswith("AIza") and base_url == "https://api.openai.com/v1":
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    b64 = base64.standard_b64encode(data).decode("ascii")
    if mime == "application/pdf":
        attachment: dict[str, Any] = {
            "type": "file",
            "file": {"filename": "receipt.pdf", "file_data": f"data:{mime};base64,{b64}"},
        }
    else:
        attachment = {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"}}
    payload = {
        "model": model,
        "max_completion_tokens": 512,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": [attachment, {"type": "text", "text": _PROMPT}]},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "receipt_check", "strict": True, "schema": _SCHEMA},
        },
    }
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status >= 400:
                body = (await resp.text())[:300]
                raise RuntimeError(f"OpenAI receipt check HTTP {resp.status}: {body}")
            result = await resp.json()
    message = (result.get("choices") or [{}])[0].get("message") or {}
    if message.get("refusal"):
        logger.info("Сверка чека (OpenAI): модель отказалась читать файл")
        return None
    text = message.get("content") or ""
    if not text:
        return None
    return _parse_receipt_json(json.loads(text))


async def recognize_claude(data: bytes, mime: str) -> Optional[ReceiptInfo]:
    """Сумма и дата из чека через Claude API (structured output). None — не чек или сумма не читается."""
    global _client
    from anthropic import AsyncAnthropic
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=90.0, max_retries=2)
    source = {"type": "base64", "media_type": mime, "data": base64.standard_b64encode(data).decode("ascii")}
    block = {"type": "document" if mime == "application/pdf" else "image", "source": source}
    request: dict[str, Any] = {
        "model": settings.receipt_check_model,
        "max_tokens": 4096,                 # с запасом на размышления модели: обрезанный ответ — не JSON
        "system": _SYSTEM,
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": _SCHEMA}},
        "messages": [{"role": "user", "content": [block, {"type": "text", "text": _PROMPT}]}],
    }
    response = await _client.messages.create(**request)
    if response.stop_reason == "refusal":
        logger.info("Сверка чека: модель отказалась читать файл")
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    return _parse_receipt_json(json.loads(text))


async def recognize_auto(data: bytes, mime: str) -> Optional[ReceiptInfo]:
    """Приоритет — Vertex AI Express (AQ.*) / OpenAI / AI Studio (OPENAI_API_KEY), иначе Claude."""
    key = settings.openai_api_key.strip()
    if key.startswith("AQ."):
        return await recognize_vertex_express(data, mime)
    if key:
        return await recognize_openai(data, mime)
    return await recognize_claude(data, mime)


_recognizer: Recognizer = recognize_auto


async def _recipients(student_id: str) -> list[int]:
    """Администраторы и педагоги, которые сами решают заявки этого ученика (FULL_BILL_TEACHER_IDS)."""
    out = [u.tg_id for u in await _deps["users"].get_admins()]
    try:
        from bot.services import payment_events
        for tg_id in await payment_events.deciders(student_id):
            if tg_id not in out:
                out.append(tg_id)
    except Exception as exc:                        # payment_events без setup (скрипты, тесты) — только админы
        logger.debug("Сверка чека: педагоги-решающие не определены: %s", exc)
    return out


async def _alert(action, group: list, amount: int, date: str) -> None:
    from bot.screens import cb
    from bot.screens.adapters import to_aiogram_markup
    from bot.services import activity
    from bot.services.payment_methods import callback_code
    from bot.utils.dates import period_label
    from bot.utils.format import format_amount, format_money

    group = sorted(group, key=lambda a: a.period_month)
    claimed = sum(a.amount for a in group)
    single = len(group) == 1
    months = ", ".join(period_label(a.period_month).lower() for a in group)
    text = (f"⚠️ Сумма в чеке не совпадает с {'заявкой' if single else 'заявками'}\n"
            f"{action.student_name} · {months}\n"
            f"В чеке: {format_money(amount)} · в {'заявке' if single else 'заявках'}: {format_money(claimed)}"
            + ("" if single else f" ({' + '.join(format_amount(a.amount) for a in group)})")
            + (f"\nДата в чеке: {date}" if date else "")
            + ("\nЗачтите ровно сумму из чека или проверьте заявку в «Ждут решения»." if single
               else "\nПроверьте заявки в «Ждут решения» перед зачётом."))
    rows = None
    if single:
        rows = [
            [cb(
                f"✅ Зачесть {format_money(amount)} — как в чеке",
                f"pact:{action.action_id}:{amount}:{callback_code(action.method or '')}",
            )],
            [cb("❌ Не подтверждать", f"pnay:{action.action_id}")],
        ]
    kb = to_aiogram_markup(rows) if rows else None
    await activity.record(activity.QUEUE, f"Чек на другую сумму: {action.student_id} · {action.period_month} · "
                          f"в чеке {format_money(amount)}, в заявке {format_money(claimed)}", ref=action.action_id)
    for tg_id in await _recipients(action.student_id):
        try:
            await _deps["bot"].send_message(tg_id, text, reply_markup=kb)
        except Exception as exc:
            logger.warning("Сверка чека: %s не доставлено: %s", tg_id, exc)
