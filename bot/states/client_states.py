from aiogram.fsm.state import State, StatesGroup


class ReceiptStates(StatesGroup):
    waiting_for_receipt = State()
    choosing_bill = State()  # чек прислан без шага «Прикрепить чек» — выбираем счёт


class GroupLinkStates(StatesGroup):
    # После привязки по ссылке группы ждём телефон («Поделиться номером» / «Пропустить»)
    waiting_contact = State()
    # Затем — необязательный email для фискальных чеков
    waiting_email = State()


class ClientEmailStates(StatesGroup):
    # Родитель вводит/меняет email для чеков из меню («✉️ Email для чеков»)
    waiting_email = State()
