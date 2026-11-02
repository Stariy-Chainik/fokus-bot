from aiogram.fsm.state import State, StatesGroup


class AthleteRegStates(StatesGroup):
    """Спортсмен находит себя по фамилии среди учеников спортивных групп."""
    waiting_surname = State()


class LogTrainingStates(StatesGroup):
    """Запись тренировки: дата → минуты → темы → задания → комментарий."""
    choosing_date = State()
    choosing_minutes = State()
    entering_minutes = State()
    choosing_topics = State()
    choosing_tasks = State()
    entering_comment = State()


class HomeworkVideoStates(StatesGroup):
    """Спортсмен присылает видео выполнения задания в чат бота (кнопка «📹 Отправить видео» в кабинете,
    решение владельца 02.11.2026); в данных — `hwv_task` (задание) или `hwv_file` (ролик ждёт выбора задания)."""
    waiting_video = State()
