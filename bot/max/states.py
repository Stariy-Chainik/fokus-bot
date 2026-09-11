from maxapi.context import State, StatesGroup


class MaxParentStates(StatesGroup):
    waiting_receipt = State()   # ждём фото/файл чека
    adding_child = State()      # фамилия второго ребёнка
    choosing_bill = State()     # чек прислан без шага «Прикрепить чек»
