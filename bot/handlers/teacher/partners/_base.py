from __future__ import annotations
"""
Педагог: «Мои пары», «Мои ученики (соло)», карточка ученика
и управление партнёром в рамках учеников своих групп.

Видимость ученика педагогу — через TeacherVisibilityService
(множество групп ученика пересекается с группами педагога).
"""
import logging

from aiogram import Router


logger = logging.getLogger(__name__)
router = Router(name="teacher_partners")



