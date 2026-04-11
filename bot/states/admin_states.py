from aiogram.fsm.state import State, StatesGroup


class AddTeacherStates(StatesGroup):
    choosing_user = State()   # выбор из зарегистрированных пользователей
    entering_tg_id = State()  # ручной ввод, если нужного нет в списке
    entering_name = State()
    entering_rate_group = State()
    entering_rate_for_teacher = State()
    entering_rate_for_student = State()
    confirming = State()


class EditTeacherRatesStates(StatesGroup):
    choosing_teacher = State()
    choosing_rate = State()
    entering_rate = State()
    confirming = State()


class AddStudentStates(StatesGroup):
    entering_name = State()
    confirming = State()


class LinkTeacherStudentStates(StatesGroup):
    choosing_teacher = State()
    choosing_student = State()
    confirming = State()


class ConfirmPaymentStates(StatesGroup):
    choosing_student = State()
    choosing_period = State()
    confirming = State()
