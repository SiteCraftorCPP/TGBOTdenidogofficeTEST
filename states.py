from aiogram.fsm.state import State, StatesGroup


class CheckInStates(StatesGroup):
    dog_line = State()
    notes = State()
    photo = State()
    owner = State()
    dates = State()
    price = State()
    location = State()
    services_ask = State()
    services_pick = State()
    manual_name = State()
    confirm = State()
    pay_offer = State()
    pay_checkin_amount = State()

class BookingStates(StatesGroup):
    dog_line = State()
    notes = State()
    photo = State()
    owner = State()
    dates = State()
    price = State()
    location = State()
    services_ask = State()
    services_pick = State()
    manual_name = State()
    confirm = State()
    pay_offer = State()
    pay_amount = State()


class BookingListStates(StatesGroup):
    checkin_dates = State()


class CheckOutStates(StatesGroup):
    choosing_dog = State()
    out_datetime = State()
    confirm = State()
    payment = State()


class DebtorStates(StatesGroup):
    entering_pay = State()


class FinanceStates(StatesGroup):
    wait_days = State()
    export_ready = State()


class SettingsStates(StatesGroup):
    inputting = State()


class StayEditStates(StatesGroup):
    dog_line = State()
    notes = State()
    photo = State()
    owner = State()
    cin_pair = State()
    cout_pair = State()
    choose_price = State()
    choose_location = State()
    services_pick = State()
    manual_name = State()
