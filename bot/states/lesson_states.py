from aiogram.fsm.state import State, StatesGroup


class RecordLessonStates(StatesGroup):
    choosing_date = State()
    choosing_kind = State()              # group / pair / soloist
    asking_attendance = State()          # group: отметить присутствующих?
    selecting_attendees = State()        # group + Да: чекбоксы
    choosing_pair = State()              # pair: чекбоксы пар (мульти-выбор)
    selecting_soloists = State()         # soloist: чекбоксы
    choosing_duration = State()


class MyLessonsStates(StatesGroup):
    entering_custom_date = State()


class SubmitPeriodStates(StatesGroup):
    choosing_month = State()
    confirming = State()
