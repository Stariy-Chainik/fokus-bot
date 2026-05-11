from aiogram.fsm.state import State, StatesGroup


class ReceiptStates(StatesGroup):
    waiting_for_receipt = State()
