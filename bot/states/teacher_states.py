from aiogram.fsm.state import State, StatesGroup


class TeacherRenameStudentStates(StatesGroup):
    entering_name = State()


class TeacherGroupAddStudentStates(StatesGroup):
    """Педагог добавляет ученика в свою группу: поиск → выбор/создание."""
    searching = State()


class TeacherKgGroupStates(StatesGroup):
    waiting_for_value = State()
