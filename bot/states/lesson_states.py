from aiogram.fsm.state import State, StatesGroup


class RecordLessonStates(StatesGroup):
    choosing_date = State()
    choosing_kind = State()              # group / pair / soloist
    asking_attendance = State()          # group: отметить присутствующих? (legacy, теперь не используется)
    choosing_group_branch = State()      # group: выбор филиала (если у педагога группы в разных)
    choosing_group = State()             # group: выбор тренировочной группы
    selecting_attendees = State()        # group: чекбоксы присутствующих в выбранной группе
    choosing_pair = State()              # pair: чекбоксы пар (мульти-выбор)
    selecting_soloists = State()         # soloist: чекбоксы
    choosing_duration = State()


class SubmitPeriodStates(StatesGroup):
    choosing_month = State()
    confirming = State()
