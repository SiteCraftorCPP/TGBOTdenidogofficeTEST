import json
import re

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from checkin_logic import billable_days, build_total, dog_label, parse_date_time_pair
from config import has_access, is_admin
from database import (
    complete_checkout,
    fetch_active_stays,
    fetch_stay_by_id,
    get_services_map,
)
from keyboards import (
    ERR_CHECKOUT_BEFORE_PLANNED,
    MAIN_MENU_CAPTION,
    PROMPT_DT_CHECKOUT_PAIR,
    admin_main_kb,
    employee_main_kb,
)
from states import CheckOutStates

router = Router(name="checkout")


def _main_kb_for(uid: int):
    return admin_main_kb() if is_admin(uid) else employee_main_kb()


_CHECKOUT_PAGE_SIZE = 8


def _checkout_page_count(n_stays: int) -> int:
    if n_stays <= 0:
        return 1
    return (n_stays + _CHECKOUT_PAGE_SIZE - 1) // _CHECKOUT_PAGE_SIZE


def _checkout_list_caption(page: int, n_stays: int) -> str:
    pages = _checkout_page_count(n_stays)
    p = max(0, min(page, pages - 1))
    return (
        "Выберите собаку для выезда:\n"
        f"Страница {p + 1}/{pages} · всего записей: {n_stays}"
    )


def _co_pick_kb(stays: list[dict], page: int) -> InlineKeyboardMarkup:
    n = len(stays)
    if n == 0:
        return InlineKeyboardMarkup(inline_keyboard=[])
    pages = _checkout_page_count(n)
    page = max(0, min(int(page), pages - 1))
    chunk = stays[page * _CHECKOUT_PAGE_SIZE : (page + 1) * _CHECKOUT_PAGE_SIZE]
    rows: list[list[InlineKeyboardButton]] = []
    for s in chunk:
        sid = int(s["id"])
        dgi = s.get("dog_info") or ""
        d = s.get("checkin_date") or ""
        t = s.get("checkin_time") or ""
        base = f"{dog_label(dgi)} - Заезд {d}, {t}"
        if len(base) > 64:
            base = base[:61] + "..."
        rows.append([InlineKeyboardButton(text=base, callback_data=f"co:{sid}")])
    if pages > 1:
        rows.append(
            [
                InlineKeyboardButton(text="◀️ Назад", callback_data="co_prev"),
                InlineKeyboardButton(text="Вперёд ▶️", callback_data="co_next"),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _confirm_co_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Подтвердить", callback_data="co_ok"),
                InlineKeyboardButton(text="Отменить", callback_data="co_x"),
            ]
        ]
    )


def _stay_service_data(row: dict) -> tuple[set[str], list]:
    s = json.loads(row.get("services_json") or "{}")
    selected = {k for k, v in s.items() if v}
    manual = json.loads(row.get("manual_services_json") or "[]")
    if not isinstance(manual, list):
        manual = []
    return selected, manual


async def _calc_checkout_total(
    row: dict, actual_out_date: str, actual_out_time: str
) -> tuple[int, str]:
    n = billable_days(
        row["checkin_date"],
        row["checkin_time"],
        actual_out_date,
        actual_out_time,
    )
    sel, manual = _stay_service_data(row)
    sm = await get_services_map()
    return build_total(
        nights=n,
        daily_price=int(row["daily_price"]),
        selected_keys=sel,
        manual=manual,
        service_catalog=sm,
    )


def _parse_paid(raw: str) -> int | None:
    raw = (raw or "").strip()
    if raw == "":
        return None
    digits = re.sub(r"\D", "", raw)
    if digits == "":
        return None
    return int(digits)


@router.message(F.text == "➖ Выезд собаки")
async def checkout_entry(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not has_access(uid):
        await message.answer("Нет доступа.")
        return
    await state.clear()
    stays = await fetch_active_stays()
    if not stays:
        await message.answer("Нет собак в гостинице.")
        return
    await state.set_state(CheckOutStates.choosing_dog)
    await state.update_data(co_page=0)
    cap = _checkout_list_caption(0, len(stays))
    await message.answer(cap, reply_markup=_co_pick_kb(stays, 0))


@router.callback_query(CheckOutStates.choosing_dog, F.data.in_({"co_prev", "co_next"}))
async def checkout_list_page(query: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    page = int(data.get("co_page") or 0)
    stays = await fetch_active_stays()
    n = len(stays)
    if n == 0:
        await query.answer("Список пуст.", show_alert=True)
        await state.clear()
        if query.message:
            await query.message.edit_text("Нет собак в гостинице.")
        return
    pages = _checkout_page_count(n)
    page = max(0, min(page, pages - 1))
    if query.data == "co_next":
        if page < pages - 1:
            page += 1
        else:
            await query.answer("Последняя страница", show_alert=True)
            return
    else:
        if page > 0:
            page -= 1
        else:
            await query.answer("Первая страница", show_alert=True)
            return
    await state.update_data(co_page=page)
    cap = _checkout_list_caption(page, n)
    if query.message:
        await query.message.edit_text(cap, reply_markup=_co_pick_kb(stays, page))
    await query.answer()


@router.callback_query(CheckOutStates.choosing_dog, F.data.regexp(r"^co:\d+$"))
async def checkout_picked_dog(query: CallbackQuery, state: FSMContext) -> None:
    raw = query.data or ""
    try:
        sid = int(raw.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    row = await fetch_stay_by_id(sid)
    if not row or int(row.get("is_active") or 1) == 0:
        await query.answer("Не найдено.", show_alert=True)
        return
    await query.message.edit_reply_markup(reply_markup=None)
    label = dog_label(row.get("dog_info") or "")
    loc = row.get("location") or ""
    cd = row.get("checkin_date") or ""
    ct = row.get("checkin_time") or ""
    await state.update_data(co_stay_id=sid)
    await state.set_state(CheckOutStates.out_datetime)
    await query.message.answer(
        f"Выезд собаки:\n"
        f" {label}\n"
        f" Место размещения: {loc}\n"
        f" Заезд {cd}, {ct}"
    )
    await query.message.answer(PROMPT_DT_CHECKOUT_PAIR)
    await query.answer()


@router.message(CheckOutStates.out_datetime, F.text)
async def checkout_out_datetime(message: Message, state: FSMContext) -> None:
    parsed = parse_date_time_pair(message.text or "")
    if not parsed:
        await message.answer(PROMPT_DT_CHECKOUT_PAIR)
        return
    od, ot = parsed
    data = await state.get_data()
    sid = int(data.get("co_stay_id") or 0)
    row = await fetch_stay_by_id(sid)
    if not row or int(row.get("is_active") or 1) == 0:
        await state.clear()
        await message.answer(MAIN_MENU_CAPTION, reply_markup=_main_kb_for(message.from_user.id))
        return
    try:
        total, formula = await _calc_checkout_total(row, od, ot)
    except ValueError as e:
        if str(e) == "checkout_before_checkin":
            await message.answer(ERR_CHECKOUT_BEFORE_PLANNED)
            return
        raise
    label = dog_label(row.get("dog_info") or "")
    await state.update_data(
        co_out_date=od,
        co_out_time=ot,
        co_total=total,
        co_formula=formula,
    )
    await state.set_state(CheckOutStates.confirm)
    await message.answer(
        f"Выезд собаки:\n"
        f" {label}\n"
        f" Место размещения: {row.get('location') or ''}\n"
        f" Заезд {row.get('checkin_date')}, {row.get('checkin_time')}\n"
        f" Выезд {od}, {ot}\n"
        f" Общая сумма: {formula}",
        reply_markup=_confirm_co_kb(),
    )


@router.callback_query(CheckOutStates.confirm, F.data.in_({"co_ok", "co_x"}))
async def checkout_confirm_cb(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    await query.message.edit_reply_markup(reply_markup=None)
    if query.data == "co_x":
        await state.clear()
        await query.message.answer(MAIN_MENU_CAPTION, reply_markup=_main_kb_for(uid))
        await query.answer()
        return
    await state.set_state(CheckOutStates.payment)
    await query.message.answer(
        "Введите оплаченную сумму (возможно и 0 ₽ или меньше 12000 ₽), тогда Денис попадет\n"
        "в список Должников.\n"
        "_______ 8000 руб."
    )
    await query.answer()


@router.message(CheckOutStates.payment, F.text)
async def checkout_payment(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id if message.from_user else 0
    paid = _parse_paid(message.text or "")
    if paid is None:
        await message.answer(
            "Введите оплаченную сумму (возможно и 0 ₽ или меньше 12000 ₽), тогда Денис попадет\n"
            "в список Должников.\n"
            "_______ 8000 руб."
        )
        return
    data = await state.get_data()
    sid = int(data.get("co_stay_id") or 0)
    total = int(data.get("co_total") or 0)
    formula = str(data.get("co_formula") or "")
    od = str(data.get("co_out_date") or "")
    ot = str(data.get("co_out_time") or "")
    row = await fetch_stay_by_id(sid)
    if not row or int(row.get("is_active") or 1) == 0:
        await state.clear()
        await message.answer(MAIN_MENU_CAPTION, reply_markup=_main_kb_for(uid))
        return
    bal = await complete_checkout(
        stay_id=sid,
        actual_out_date=od,
        actual_out_time=ot,
        paid=paid,
        final_total=total,
        final_formula=formula,
    )
    if bal is None:
        await state.clear()
        await message.answer("Запись уже закрыта.", reply_markup=_main_kb_for(uid))
        return
    await state.clear()
    await message.answer(
        f"Выезд оформлен. Оплата {paid} ₽. Остаток {bal} ₽.",
        reply_markup=_main_kb_for(uid),
    )
