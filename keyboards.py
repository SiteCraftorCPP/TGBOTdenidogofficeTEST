from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from config import is_admin

BTN_SKIP = "⏭️ Пропустить"

SKIP_CB_CHECKIN_NOTES = "csk:nt"
SKIP_CB_CHECKIN_PHOTO = "csk:ph"
SKIP_CB_CHECKIN_OWNER = "csk:ow"
SKIP_CB_EDIT_NOTES = "esk:nt"
SKIP_CB_EDIT_PHOTO = "esk:ph"
SKIP_CB_EDIT_OWNER = "esk:ow"

MAIN_MENU_CAPTION = (
    "<b>Зоогостиница🐕</b>\n\n"
    "Выберите действие:"
)

SERVICES_INLINE_CAPTION = "Выберите нужные или добавьте вручную:"

PROMPT_MANUAL_SERVICE_LINE = (
    "Введите услугу и стоимость, через запятую:\n"
    "пример: Груминг, 1200"
)

ERR_MANUAL_SERVICE_PARSE = (
    "Не разобрал. Услуга и сумма через запятую, например: Груминг, 1200"
)

DT_PAIR_EXAMPLE = "15.03.26, 8:15"
DT_BLOCK_EXAMPLE = "17.03.26, 14:30, 22.03.26"
DT_PLANNED_CHECKOUT_EXAMPLE = "22.03.26"

PROMPT_DT_CHECKIN_PAIR = (
    "Введите: дату и время заезда (через запятую)\n"
    f"пример: {DT_PAIR_EXAMPLE}"
)
PROMPT_DT_CHECKOUT_PAIR = (
    "Введите: дату и время выезда (через запятую)\n"
    f"пример: {DT_PAIR_EXAMPLE}"
)
PROMPT_DT_CHECKIN_BLOCK = (
    "Введите: дату заезда, время заезда, планируемую дату выезда (через запятую)\n"
    f"пример: {DT_BLOCK_EXAMPLE}"
)
PROMPT_DT_PLANNED_CHECKOUT_DATE = (
    "Введите: планируемую дату выезда\n"
    f"пример: {DT_PLANNED_CHECKOUT_EXAMPLE}"
)

ERR_STAY_EDIT_PAIR_PARSE = (
    "Не получилось разобрать строку. Нужны дата и время через запятую "
    f"(как «{DT_PAIR_EXAMPLE}»)."
)
ERR_STAY_EDIT_PLANNED_DATE_PARSE = (
    "Не получилось разобрать дату. Укажите дату в формате ДД.ММ.ГГ "
    f"(как «{DT_PLANNED_CHECKOUT_EXAMPLE}»)."
)
ERR_CHECKIN_BLOCK_CHECKOUT_BEFORE = (
    "Планируемая дата выезда не может быть раньше даты и времени заезда.\n\n"
    f"{PROMPT_DT_CHECKIN_BLOCK}"
)
ERR_CHECKOUT_BEFORE_PLANNED = (
    "Дата и время выезда не могут быть раньше даты и времени заезда.\n\n"
    f"{PROMPT_DT_CHECKOUT_PAIR}"
)

PROMPT_NOTES_QUESTION = "Примечания: (корм, вещи, аллергии и пр.)"


def admin_main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📅 Бронирование"),
                KeyboardButton(text="📋 Список броней"),
            ],
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
                KeyboardButton(text="📅 Бронирование"),
                KeyboardButton(text="📋 Список броней"),
            ],
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


def skip_inline_kb(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=BTN_SKIP, callback_data=callback_data)]]
    )


async def send_notes_prompt_step(message: Message, skip_callback_data: str) -> None:
    await message.answer(
        PROMPT_NOTES_QUESTION,
        reply_markup=skip_inline_kb(skip_callback_data),
    )


def remove_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def main_menu_kb_for(uid: int) -> ReplyKeyboardMarkup:
    return admin_main_kb() if is_admin(uid) else employee_main_kb()
