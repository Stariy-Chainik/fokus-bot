from aiogram.fsm.state import State, StatesGroup


class TeacherAddStudentStates(StatesGroup):
    """Педагог создаёт нового ученика через заявку админу."""
    searching = State()
    choosing_group = State()        # выбор тренировочной группы нового ученика


class TeacherRenameStudentStates(StatesGroup):
    entering_name = State()
