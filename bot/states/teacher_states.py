from aiogram.fsm.state import State, StatesGroup


class TeacherRenameStudentStates(StatesGroup):
    entering_name = State()


class TeacherGroupAddStudentStates(StatesGroup):
    """Педагог добавляет ученика в свою группу: поиск → выбор/создание."""
    searching = State()


class GradeEntryStates(StatesGroup):
    """Педагог оценивает тренировку спортсмена: 1–5 → комментарий."""
    choosing_grade = State()
    entering_comment = State()


class AssignTaskStates(StatesGroup):
    """Педагог даёт задание: упражнение → минуты → комментарий."""
    choosing_exercise = State()
    entering_exercise = State()
    choosing_minutes = State()
    entering_minutes = State()
    entering_comment = State()
