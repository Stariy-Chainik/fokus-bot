from aiogram.fsm.state import State, StatesGroup


class AddTeacherStates(StatesGroup):
    entering_tg_id = State()
    entering_name = State()
    entering_rate_group = State()
    entering_rate_for_teacher = State()
    entering_rate_for_student = State()


class EditTeacherRatesStates(StatesGroup):
    choosing_rate = State()
    entering_rate = State()
    confirming = State()


class AddStudentStates(StatesGroup):
    entering_name = State()
    choosing_branch = State()
    choosing_group = State()
    confirming = State()


class StudentListStates(StatesGroup):
    searching = State()


class PartnerAssignStates(StatesGroup):
    choosing_partner = State()
    confirming = State()


class AddBranchStates(StatesGroup):
    entering_name = State()


class EditBranchNameStates(StatesGroup):
    entering_name = State()


class AddGroupStates(StatesGroup):
    entering_name = State()


class EditGroupNameStates(StatesGroup):
    entering_name = State()


class GroupBillingStates(StatesGroup):
    entering_duration_short = State()
    entering_price_short = State()
    entering_duration_full = State()
    entering_price_full = State()
    entering_sub_price = State()  # цена абонемента, ₽/месяц (режим SUBSCRIPTION)
    entering_override_amount = State()  # цена на конкретный месяц (0 = не начислять)


class GroupAddStudentStates(StatesGroup):
    """Админ добавляет ученика в группу: ввод ФИО → поиск → выбор/создание."""
    searching = State()


class ClientCreateStates(StatesGroup):
    entering_phone = State()


class ClientRegStates(StatesGroup):
    adding_child = State()
