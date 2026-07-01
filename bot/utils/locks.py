"""Единый локер «операция в процессе» — защита от двойного клика.

Раньше в 6 файлах-хендлерах жили разрозненные `module-level set[str]`
(`_submitting`, `_sending`, `_confirming_lesson_ids` и т.п.). Здесь один класс.

API намеренно совместим с `set` в объёме, который используют хендлеры
(`key in guard`, `guard.add(key)`, `guard.discard(key)`), поэтому call-site'ы
не меняются, а поведение остаётся прежним. Дополнительно есть контекст-менеджер
`hold()` для нового кода — он гарантирует снятие ключа в finally.

TODO(I2): бэкенд можно заменить на Redis с TTL, чтобы локи переживали
перезапуск и работали при нескольких процессах — тогда меняется только этот файл.
"""
from __future__ import annotations

from contextlib import contextmanager


class InProgressGuard:
    def __init__(self) -> None:
        self._keys: set[str] = set()

    def __contains__(self, key: str) -> bool:
        return key in self._keys

    def add(self, key: str) -> None:
        self._keys.add(key)

    def discard(self, key: str) -> None:
        self._keys.discard(key)

    @contextmanager
    def hold(self, key: str):
        """Контекст-менеджер: помечает ключ занятым и гарантированно снимает.

        Использование в новом коде:
            if key in guard:
                ...  # уже выполняется
                return
            with guard.hold(key):
                ...
        """
        self.add(key)
        try:
            yield
        finally:
            self.discard(key)
