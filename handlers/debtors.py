import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from checkin_logic import (
    billable_days,
    build_total,
    dog_label,
    format_dog_display,
    stay_services_from_row,
)
from config import has_access
from database import (
    apply_debt_payment,
    fetch_debtor_by_id,
    fetch_open_debtors,
    get_services_map,
    list_services_catalog,
)
from keyboards import main_menu_kb_for
from states import DebtorStates

router = Router(name="debtors")


async def _formula_for_stay(row: dict) -> str:
    f = (row.get("checkout_final_formula") or "").strip()
    if f:
        return f
    cin_d = row.get("checkin_date") or ""
    cin_t = row.get("checkin_time") or "00:00"
    od = row.get("actual_out_date") or row.get("checkout_date") or ""
    ot = row.get("actual_out_time") or row.get("checkout_time") or "00:00"
    daily = int(row.get("daily_price") or 0)
    try:
        n = billable_days(cin_d, cin_t, od, ot)
    except ValueError:
        n = 1
    sel, manual = stay_services_from_row(row)
    sm = await get_services_map()
    _t, form = build_total(
        nights=n,
        daily_price=daily,
        selected_keys=sel,
        manual=manual,
        service_catalog=sm,
    )
    return form


async def _format_debtor_info(row: dict) -> str:
    dog = (row.get("dog_info") or "").strip()
    owner = (row.get("owner_info") or "").strip()
    loc = row.get("location") or ""
    cin_d = row.get("checkin_date") or ""
    cin_t = row.get("checkin_time") or ""
    od = row.get("actual_out_date") or ""
    ot = row.get("actual_out_time") or ""
    daily = int(row.get("daily_price") or 0)
    owed = int(row.get("amount_owed") or 0)
    paid = int(row.get("payment_amount") or 0)
    formula = await _formula_for_stay(row)
    sel, manual = stay_services_from_row(row)
    svc_map = await get_services_map()
    order = [r["slug"] for r in await list_services_catalog()]
    lines: list[str] = ["Информация:", format_dog_display(dog)]
    if owner:
        lines.append(f"Хозяин: {owner}")
    lines.append(f"Место размещения: {loc}")
    lines.append(f"Заезд {cin_d}, {cin_t}")
    lines.append(f"Выезд {od}, {ot}")
    lines.append(f"Цена проживания за сутки: {daily} ₽")
    daily_lines: list[str] = []
    for slug in order:
        if slug in sel and slug in svc_map:
            title, per = svc_map[slug]
            daily_lines.append(f"{title} — {per} /день ₽")
    manual_lines = [f"{m['name']} — {m['amount']} руб." for m in manual]
    if daily_lines:
        lines.append(f"Услуги: {daily_lines[0]}")
        for rest in daily_lines[1:]:
            lines.append(f" {rest}")
        for ml in manual_lines:
            lines.append(f" {ml}")
    elif manual_lines:
        lines.append(f"Услуги: {manual_lines[0]}")
        for rest in manual_lines[1:]:
            lines.append(f" {rest}")
    else:
        lines.append("Услуги: —")
    lines.append(f"Общая сумма: {formula}")
    lines.append(f"Оплата {paid} ₽. Остаток {owed} ₽.")
    return "\n".join(lines)


def _debt_list_kb(rows: list[dict]) -> InlineKeyboardMarkup:
    out = []
    for r in rows:
        did = int(r["debtor_id"])
        dgi = r.get("dog_info") or ""
        owed = int(r.get("amount_owed") or 0)
        base = f"{dog_label(dgi)} — долг {owed} ₽"
        if len(base) > 64:
            base = base[:61] + "..."
        out.append([InlineKeyboardButton(text=base, callback_data=f"db:{did}")])
    return InlineKeyboardMarkup(inline_keyboard=out)


def _parse_pay(raw: str) -> int | None:
    raw = (raw or "").strip()
    if raw == "":
        return None
    digits = re.sub(r"\D", "", raw)
    if digits == "":
        return None
    return int(digits)


@router.message(F.text == "⚠️ Должники")
async def debtors_entry(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not has_access(uid):
        await message.answer("Нет доступа.")
        return
    await state.clear()
    rows = await fetch_open_debtors()
    if not rows:
        await message.answer("Нет записей в должниках.")
        return
    await message.answer(
        "Выберите запись, по которой внести долг:",
        reply_markup=_debt_list_kb(rows),
    )


@router.callback_query(F.data.startswith("db:"))
async def debtors_open(query: CallbackQuery, state: FSMContext) -> None:
    try:
        did = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    row = await fetch_debtor_by_id(did)
    if not row or int(row.get("amount_owed") or 0) <= 0:
        await query.answer("Запись не найдена.", show_alert=True)
        return
    await query.message.edit_reply_markup(reply_markup=None)
    await state.update_data(debtor_id=did)
    await state.set_state(DebtorStates.entering_pay)
    await query.message.answer(await _format_debtor_info(row))
    await query.message.answer(
        "4.2. Введите сумму погашения (число):\n_____________3000"
    )
    await query.answer()


@router.message(DebtorStates.entering_pay, F.text)
async def debtors_pay(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id if message.from_user else 0
    amt = _parse_pay(message.text or "")
    if amt is None:
        await message.answer(
            "4.2. Введите сумму погашения (число):\n_____________3000"
        )
        return
    data = await state.get_data()
    did = int(data.get("debtor_id") or 0)
    res = await apply_debt_payment(did, amt)
    if res is None:
        await state.clear()
        await message.answer("Запись не найдена.", reply_markup=main_menu_kb_for(uid))
        return
    applied, remaining = res
    await state.clear()
    await message.answer(
        f"☑️ Внесено {applied} ₽\n Долг {remaining} ₽",
        reply_markup=main_menu_kb_for(uid),
    )
