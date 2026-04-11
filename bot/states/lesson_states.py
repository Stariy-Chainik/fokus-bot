from aiogram.fsm.state import State, StatesGroup


class RecordLessonStates(StatesGroup):
    choosing_date = State()
    choosing_type = State()
    choosing_student_1 = State()
    asking_second_student = State()
    choosing_student_2 = State()
    choosing_duration = State()
    confirming = State()
