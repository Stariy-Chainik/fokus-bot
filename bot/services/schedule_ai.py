"""Гибридный разбор расписаний из Telegram через OpenAI GPT (запасной к локальному парсеру).

Используется только если задан `OPENAI_API_KEY` и локальный парсер не распознал сообщение,
но в тексте от авторизованного педагога есть хотя бы одно время (`HH:MM` / `HH.MM`).
Умеет:
- резать недельные расписания (Ангелина Клецова и др.) на отдельные дни с точными датами;
- определять педагога-адресата (например, Назар выложил расписание для Леры → TCH-0013);
- отбрасывать обычную переписку и обсуждения (`is_schedule: false`).

Все ответы модели проходят жёсткую проверку в Python (`validate_ai_schedule`) перед сохранением.
При отсутствии ключа, сбое сети или исчерпании баланса — тихо деградирует, бот работает штатно.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from bot.services.schedule_pilot import DRAFT_DURATIONS, Slot, _plain, _time_parts
from config.settings import settings

logger = logging.getLogger(__name__)

_HAS_TIME = re.compile(r"(?<!\d)(?:[01]?\d|2[0-3])[:.][0-5]\d(?!\d)")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_WEEKDAY_RU = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")

_SCHEDULE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_schedule": {"type": "boolean"},
        "schedules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "teacher_id": {"type": "string"},
                    "days": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "date": {"type": "string"},
                                "slots": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "time": {"type": "string"},
                                            "end": {"type": ["string", "null"]},
                                            "duration": {"type": ["integer", "null"]},
                                            "names": {"type": "string"},
                                        },
                                        "required": ["time", "end", "duration", "names"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["date", "slots"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["teacher_id", "days"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["is_schedule", "schedules"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "Ты разбираешь сообщения из рабочего чата танцевального клуба «Фокус» в структурированное расписание занятий.\n"
    "Правила:\n"
    "1. Если сообщение НЕ является расписанием занятий (обычная переписка, вопрос, объявление, обсуждение одного переноса), "
    "верни is_schedule=false и schedules=[].\n"
    "2. Определи, для какого педагога из списка составлено расписание:\n"
    "   - если явно указан другой педагог (например, «Расписание Леры», «Лера», «Влад», «С.А.»), укажи его teacher_id;\n"
    "   - если педагог не назван, это личное расписание отправителя (sender_teacher_id);\n"
    "   - если в одном сообщении отдельные блоки для двух педагогов, верни отдельный элемент в schedules для каждого.\n"
    "   - используй ТОЛЬКО teacher_id из переданного списка педагогов; если педагога нет в списке, пропусти его блок.\n"
    "3. Даты:\n"
    "   - всегда вычисляй точную дату YYYY-MM-DD относительно даты публикации и дня недели;\n"
    "   - для недельного расписания разбей его на отдельные объекты days по каждому дню недели;\n"
    "   - если указан день недели без даты: для поста одного дня — ближайший этот день недели начиная с даты публикации "
    "(если сегодня этот же день недели — сегодняшняя дата); для недельного поста «на эту неделю» — дни текущей недели "
    "(пн–вс недели публикации), «на следующую неделю» — следующей недели;\n"
    "   - выходные/пустые дни без занятий пропускай.\n"
    "4. Слоты дня:\n"
    "   - time и end (если явно указан конец, иначе null) строго в формате HH:MM;\n"
    "   - duration — целое число минут ТОЛЬКО если длительность явно написана в строке или задан интервал start-end, иначе null;\n"
    "   - names — текст участника/пары или название группы (если окно «свободно» или пустое время — верни пустую строку \"\");\n"
    "   - слоты в рамках одного дня должны идти строго по возрастанию времени.\n"
    "Отвечай строго JSON по схеме."
)


def looks_like_schedule(text: str) -> bool:
    """Быстрый бесплатный предфильтр: есть ли в сообщении хотя бы одно время HH:MM / HH.MM."""
    stripped = (text or "").strip()
    return len(stripped) >= 12 and bool(_HAS_TIME.search(stripped))


def validate_ai_schedule(
    parsed: dict[str, Any],
    published: date,
    allowed_teacher_ids: set[str],
) -> dict[str, dict[date, list[Slot]]]:
    """Жёсткая проверка JSON от модели перед сохранением в SchedulePilot."""
    if not isinstance(parsed, dict) or parsed.get("is_schedule") is not True:
        return {}
    raw_schedules = parsed.get("schedules")
    if not isinstance(raw_schedules, list):
        return {}

    min_day = published - timedelta(days=14)
    max_day = published + timedelta(days=21)
    by_teacher: dict[str, dict[date, list[Slot]]] = {}

    for item in raw_schedules:
        if not isinstance(item, dict):
            continue
        tid = item.get("teacher_id")
        if not isinstance(tid, str) or tid not in allowed_teacher_ids:
            continue
        raw_days = item.get("days")
        if not isinstance(raw_days, list):
            continue
        teacher_days = by_teacher.setdefault(tid, {})
        for raw_day in raw_days:
            if not isinstance(raw_day, dict):
                continue
            day_str = raw_day.get("date")
            if not isinstance(day_str, str) or not _ISO_DATE.fullmatch(day_str):
                continue
            try:
                day = date.fromisoformat(day_str)
            except ValueError:
                continue
            if not (min_day <= day <= max_day):
                continue
            raw_slots = raw_day.get("slots")
            if not isinstance(raw_slots, list) or not raw_slots or len(raw_slots) > 50:
                continue

            slots: list[Slot] = []
            valid_day = True
            for raw_slot in raw_slots:
                if not isinstance(raw_slot, dict):
                    valid_day = False
                    break
                time_val = raw_slot.get("time")
                if not isinstance(time_val, str) or not _HHMM.fullmatch(time_val):
                    valid_day = False
                    break
                time_norm, at = _time_parts(time_val)
                if slots and at <= slots[-1].at:
                    valid_day = False
                    break
                end_val = raw_slot.get("end")
                end_norm: str | None = None
                duration_val = raw_slot.get("duration")
                duration: int | None = None
                if isinstance(duration_val, int) and not isinstance(duration_val, bool) and 1 <= duration_val <= 240:
                    duration = duration_val
                if isinstance(end_val, str) and _HHMM.fullmatch(end_val):
                    end_norm, ends_at = _time_parts(end_val)
                    interval = ends_at - at
                    if interval > 0:
                        duration = interval
                    else:
                        end_norm = None
                names_raw = raw_slot.get("names")
                names = names_raw.strip() if isinstance(names_raw, str) else ""
                if not names.strip(" -–—") or _plain(names).rstrip(".!") == "свободно":
                    names = ""
                slots.append(Slot(time_norm, at, names[:200], duration, end=end_norm))

            if not valid_day or not slots or not any(s.names for s in slots):
                continue
            for idx, slot in enumerate(slots[:-1]):
                gap = slots[idx + 1].at - slot.at
                if slot.names and slot.duration is None and gap in DRAFT_DURATIONS:
                    slots[idx] = replace(slot, duration=gap, suggested=True)
            teacher_days[day] = slots

    return {tid: days for tid, days in by_teacher.items() if days}


_GEMINI_SCHEDULE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "is_schedule": {"type": "BOOLEAN"},
        "schedules": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "teacher_id": {"type": "STRING"},
                    "days": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "date": {"type": "STRING"},
                                "slots": {
                                    "type": "ARRAY",
                                    "items": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "time": {"type": "STRING"},
                                            "end": {"type": "STRING", "nullable": True},
                                            "duration": {"type": "INTEGER", "nullable": True},
                                            "names": {"type": "STRING"},
                                        },
                                        "required": ["time", "names"],
                                    },
                                },
                            },
                            "required": ["date", "slots"],
                        },
                    },
                },
                "required": ["teacher_id", "days"],
            },
        },
    },
    "required": ["is_schedule", "schedules"],
}

_VERTEX_EXPRESS_URL = "https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent"


def _effective_schedule_model(api_key: str) -> str:
    model = settings.openai_schedule_model
    if api_key.startswith(("AQ.", "AIza")) and model.startswith("gpt-"):
        return "gemini-2.5-flash"
    return model


async def _call_vertex_express(api_key: str, model: str, user_prompt: str) -> dict[str, Any]:
    import aiohttp

    url = _VERTEX_EXPRESS_URL.format(model=model)
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _GEMINI_SCHEDULE_SCHEMA,
            "maxOutputTokens": 2048,
        },
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status >= 400:
                body = (await resp.text())[:300]
                raise RuntimeError(f"Vertex Express schedule parse HTTP {resp.status}: {body}")
            result = await resp.json()
    parts = (((result.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    content = next((p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")), "")
    return json.loads(content) if content else {}


async def _call_openai_compat(api_key: str, model: str, user_prompt: str) -> dict[str, Any]:
    import aiohttp

    base_url = settings.openai_base_url.rstrip("/")
    if api_key.startswith("AIza") and base_url == "https://api.openai.com/v1":
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    payload = {
        "model": model,
        "max_completion_tokens": 1500,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "schedule_parse", "strict": True, "schema": _SCHEDULE_SCHEMA},
        },
    }
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status >= 400:
                body = (await resp.text())[:300]
                raise RuntimeError(f"OpenAI schedule parse HTTP {resp.status}: {body}")
            result = await resp.json()
    message = (result.get("choices") or [{}])[0].get("message") or {}
    if message.get("refusal"):
        return {}
    content = message.get("content") or ""
    return json.loads(content) if content else {}


async def parse_schedule_openai(
    text: str,
    published: date,
    sender_teacher_id: str,
    teachers: list[dict[str, str]],
) -> dict[str, dict[date, list[Slot]]]:
    """Разобрать сообщение через OpenAI / Gemini AI Studio / Vertex AI Express API (structured JSON)."""
    api_key = settings.openai_api_key.strip()
    if not api_key or not looks_like_schedule(text):
        return {}

    weekday_ru = _WEEKDAY_RU[published.weekday()]
    teacher_lines = "\n".join(
        f"- {t['id']}: {t['name']}" + (f" ({t['aliases']})" if t.get("aliases") else "")
        for t in teachers
    )
    user_prompt = (
        f"Дата публикации: {published.isoformat()} ({weekday_ru})\n"
        f"Отправитель сообщения: {sender_teacher_id}\n"
        f"Педагоги клуба:\n{teacher_lines}\n\n"
        f"Текст сообщения:\n{text}"
    )
    model = _effective_schedule_model(api_key)
    if api_key.startswith("AQ."):
        parsed = await _call_vertex_express(api_key, model, user_prompt)
    else:
        parsed = await _call_openai_compat(api_key, model, user_prompt)
    allowed = {t["id"] for t in teachers}
    return validate_ai_schedule(parsed, published, allowed)
