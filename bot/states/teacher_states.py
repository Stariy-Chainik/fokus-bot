from aiogram.fsm.state import State, StatesGroup


class TeacherAddStudentStates(StatesGroup):
    """Педагог добавляет существующего ученика в свой список."""
    searching = State()
    choosing_group = State()        # при создании НОВОГО ученика: выбор тренировочной группы
    multi_selecting = State()       # мульти-выбор из ростера группы


class TeacherRenameStudentStates(StatesGroup):
    entering_name = State()
