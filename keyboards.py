from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from config import is_admin

BTN_SKIP = "Пропустить"

MAIN_MENU_CAPTION = (
    "<b>Добро пожаловать в бот зоогостиницы!</b> 🐕\n\n"
    "<i>Выберите раздел на клавиатуре ниже.</i>"
)

SERVICES_INLINE_CAPTION = "Выберите услуги кнопками ниже:"

DT_PAIR_EXAMPLE = "15.03.26, 8:15"
DT_BLOCK_EXAMPLE = "10.03.26, 14:30, 15.03.26, 8:15"

PROMPT_DT_CHECKIN_PAIR = (
    "Введите дату и время заезда (через запятую):\n"
    f" пример: {DT_PAIR_EXAMPLE}"
)
PROMPT_DT_CHECKOUT_PAIR = (
    "Введите дату и время выезда (через запятую):\n"
    f" пример: {DT_PAIR_EXAMPLE}"
)
PROMPT_DT_CHECKIN_BLOCK = (
    "Введите через запятую: дату и время заезда, дату и время выезда.\n"
    "Формат каждой пары: ДД.ММ.ГГ, ЧЧ:ММ\n"
    f" пример: {DT_BLOCK_EXAMPLE}"
)

ERR_STAY_EDIT_PAIR_PARSE = (
    "Не получилось разобрать строку. Нужны дата и время через запятую "
    f"(как «{DT_PAIR_EXAMPLE}»)."
)
ERR_CHECKIN_BLOCK_CHECKOUT_BEFORE = (
    "Дата и время выезда не могут быть раньше даты и времени заезда.\n\n"
    f"{PROMPT_DT_CHECKIN_BLOCK}"
)
ERR_CHECKOUT_BEFORE_PLANNED = (
    "Дата и время выезда не могут быть раньше даты и времени заезда.\n\n"
    f"{PROMPT_DT_CHECKOUT_PAIR}"
)


def admin_main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="➕ Заезд собаки"),
                KeyboardButton(text="➖ Выезд собаки"),
            ],
            [
                KeyboardButton(text="🐾 Сейчас в гостинице"),
                KeyboardButton(text="⚠️ Должники"),
            ],
            [
                KeyboardButton(text="💰 Финансовый отчет"),
                KeyboardButton(text="⚙️ Настройки"),
            ],
        ],
        resize_keyboard=True,
    )


def employee_main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="➕ Заезд собаки"),
                KeyboardButton(text="➖ Выезд собаки"),
            ],
            [
                KeyboardButton(text="🐾 Сейчас в гостинице"),
                KeyboardButton(text="⚠️ Должники"),
            ],
        ],
        resize_keyboard=True,
    )


def skip_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_SKIP)]],
        resize_keyboard=True,
    )


def remove_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def main_menu_kb_for(uid: int) -> ReplyKeyboardMarkup:
    return admin_main_kb() if is_admin(uid) else employee_main_kb()
