from aiogram.fsm.state import State, StatesGroup


class TeacherAddStudentStates(StatesGroup):
    """Педагог добавляет существующего ученика в свой список."""
    searching = State()
    choosing_group = State()        # при создании НОВОГО ученика: выбор тренировочной группы
