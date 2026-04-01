from __future__ import annotations

import re
import secrets

import aiosqlite
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import is_admin
from database import (
    delete_location_catalog,
    delete_service_catalog,
    delete_stay_price_slot,
    get_location_row,
    get_service_row,
    get_stay_price_slot,
    insert_location_catalog,
    insert_service_catalog,
    insert_stay_price_slot,
    list_locations_catalog,
    list_services_catalog,
    list_stay_price_slots,
    update_location_catalog,
    update_service_catalog,
    update_stay_price_slot,
)
from keyboards import MAIN_MENU_CAPTION, admin_main_kb
from states import SettingsStates

router = Router(name="settings")

KB_BACK = [InlineKeyboardButton(text="◀️ К списку настроек", callback_data="sroot")]


def _kb_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💵 Цена проживания/сутки", callback_data="sm1")],
            [InlineKeyboardButton(text="🔔 Услуги/день", callback_data="sm2")],
            [InlineKeyboardButton(text="🏠 Места размещения", callback_data="sm3")],
        ]
    )


def _truncate(s: str, n: int = 42) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"


async def _kb_stay_prices() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for r in await list_stay_price_slots():
        sid = int(r["id"])
        label = _truncate(f'{r["name"]} — {r["price"]} ₽')
        rows.append(
            [
                InlineKeyboardButton(text="✏️", callback_data=f"spe:{sid}"),
                InlineKeyboardButton(text=label, callback_data=f"spe:{sid}"),
                InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"spd:{sid}"),
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="➕ Добавить стоимость проживания в сутки", callback_data="spa")]
    )
    rows.append(KB_BACK)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _kb_services() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for r in await list_services_catalog():
        sid = int(r["id"])
        label = _truncate(f'{r["name"]} — {r["price_per_day"]} /день ₽')
        rows.append(
            [
                InlineKeyboardButton(text="✏️", callback_data=f"sve:{sid}"),
                InlineKeyboardButton(text=label, callback_data=f"sve:{sid}"),
                InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"svd:{sid}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ Добавить услугу/день", callback_data="sva")])
    rows.append(KB_BACK)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _kb_locations() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for r in await list_locations_catalog():
        lid = int(r["id"])
        name = _truncate(str(r["name"]))
        rows.append(
            [
                InlineKeyboardButton(text="✏️", callback_data=f"lce:{lid}"),
                InlineKeyboardButton(text=name, callback_data=f"lce:{lid}"),
                InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"lcd:{lid}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ Добавить место", callback_data="lca")])
    rows.append(KB_BACK)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _parse_money(raw: str) -> int | None:
    t = (raw or "").strip().replace(" ", "")
    if not re.match(r"^\d{1,9}$", t):
        return None
    return int(t)


def _new_slug(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


async def _finish_to_main(message: Message, state: FSMContext, text: str) -> None:
    await state.clear()
    await message.answer(f"{text}\n\nГлавное меню:")
    await message.answer(MAIN_MENU_CAPTION, reply_markup=admin_main_kb())


@router.message(F.text == "⚙️ Настройки")
async def settings_open(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not is_admin(uid):
        await message.answer("Нет доступа.")
        return
    await state.clear()
    await message.answer("6. Настройки", reply_markup=_kb_root())


@router.callback_query(F.data == "sroot")
async def cb_settings_root(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not is_admin(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    await query.answer()
    if query.message:
        await query.message.edit_text("6. Настройки", reply_markup=_kb_root())


@router.callback_query(F.data == "sm1")
async def cb_sm1(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not is_admin(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await query.answer()
    if query.message:
        await query.message.edit_text(
            "6.1 Цена проживания/сутки:",
            reply_markup=await _kb_stay_prices(),
        )


@router.callback_query(F.data == "sm2")
async def cb_sm2(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not is_admin(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await query.answer()
    if query.message:
        await query.message.edit_text(
            "6.2 Услуги\nВыберите для редактирования или добавьте новую:",
            reply_markup=await _kb_services(),
        )


@router.callback_query(F.data == "sm3")
async def cb_sm3(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not is_admin(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await query.answer()
    if query.message:
        await query.message.edit_text(
            "6.3 Места размещения",
            reply_markup=await _kb_locations(),
        )


@router.callback_query(F.data.startswith("spd:"))
async def cb_spd(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not is_admin(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    sid = int(query.data.split(":")[1])
    await delete_stay_price_slot(sid)
    await query.answer("Удалено")
    if query.message:
        await query.message.edit_reply_markup(reply_markup=await _kb_stay_prices())


@router.callback_query(F.data.startswith("svd:"))
async def cb_svd(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not is_admin(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    sid = int(query.data.split(":")[1])
    await delete_service_catalog(sid)
    await query.answer("Удалено")
    if query.message:
        await query.message.edit_reply_markup(reply_markup=await _kb_services())


@router.callback_query(F.data.startswith("lcd:"))
async def cb_lcd(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not is_admin(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    lid = int(query.data.split(":")[1])
    await delete_location_catalog(lid)
    await query.answer("Удалено")
    if query.message:
        await query.message.edit_reply_markup(reply_markup=await _kb_locations())


@router.callback_query(F.data.startswith("spe:"))
async def cb_spe(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not is_admin(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    sid = int(query.data.split(":")[1])
    row = await get_stay_price_slot(sid)
    if not row:
        await query.answer("Запись не найдена", show_alert=True)
        return
    await query.answer()
    await state.set_state(SettingsStates.inputting)
    await state.update_data(
        flow="sp_price_name",
        slot_id=sid,
        edit_label=str(row["name"]),
    )
    if query.message:
        await query.message.answer(
            f"Проживание/сутки: {row['name']}\nВведите новое наименование услуги:"
        )


@router.callback_query(F.data == "spa")
async def cb_spa(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not is_admin(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await query.answer()
    await state.set_state(SettingsStates.inputting)
    await state.update_data(flow="sp_add_name")
    if query.message:
        await query.message.answer("Введите название новой стоимости")


@router.callback_query(F.data.startswith("sve:"))
async def cb_sve(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not is_admin(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    sid = int(query.data.split(":")[1])
    row = await get_service_row(sid)
    if not row:
        await query.answer("Запись не найдена", show_alert=True)
        return
    await query.answer()
    await state.set_state(SettingsStates.inputting)
    await state.update_data(
        flow="sv_price_name",
        svc_id=sid,
        old_svc_name=str(row["name"]),
    )
    if query.message:
        await query.message.answer(
            f"Услуга: {row['name']}.\nВведите новое наименование услуги:"
        )


@router.callback_query(F.data == "sva")
async def cb_sva(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not is_admin(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await query.answer()
    await state.set_state(SettingsStates.inputting)
    await state.update_data(flow="sv_add_name")
    if query.message:
        await query.message.answer("Введите название новой услуги")


@router.callback_query(F.data.startswith("lce:"))
async def cb_lce(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not is_admin(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    lid = int(query.data.split(":")[1])
    row = await get_location_row(lid)
    if not row:
        await query.answer("Запись не найдена", show_alert=True)
        return
    await query.answer()
    await state.set_state(SettingsStates.inputting)
    await state.update_data(flow="lc_edit_name", loc_id=lid, old_loc_name=str(row["name"]))
    if query.message:
        await query.message.answer(
            f"Место размещения: {row['name']}\nВведите новое наименование:"
        )


@router.callback_query(F.data == "lca")
async def cb_lca(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not is_admin(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await query.answer()
    await state.set_state(SettingsStates.inputting)
    await state.update_data(flow="lc_add_name")
    if query.message:
        await query.message.answer("Введите название нового места размещения")


@router.message(SettingsStates.inputting, F.text)
async def settings_input(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not is_admin(uid):
        await state.clear()
        await message.answer("Нет доступа.")
        return

    data = await state.get_data()
    flow = str(data.get("flow") or "")
    raw = (message.text or "").strip()

    if flow == "sp_price_name":
        if not raw:
            await message.answer("Введите непустое наименование.")
            return
        await state.update_data(flow="sp_price_value", pending_name=raw)
        await message.answer("Введите новую стоимость (число)/день:")
        return

    if flow == "sp_price_value":
        price = _parse_money(raw)
        if price is None:
            await message.answer("Введите целое число (стоимость за сутки).")
            return
        sid = int(data["slot_id"])
        label = str(data["edit_label"])
        pending = str(data["pending_name"])
        await update_stay_price_slot(sid, pending, price)
        await _finish_to_main(
            message,
            state,
            f"Проживание/сутки: {label} обновлена.",
        )
        return

    if flow == "sp_add_name":
        if not raw:
            await message.answer("Введите непустое наименование.")
            return
        await state.update_data(flow="sp_add_price", pending_name=raw)
        await message.answer("Введите стоимость услуги (число)/день:")
        return

    if flow == "sp_add_price":
        price = _parse_money(raw)
        if price is None:
            await message.answer("Введите целое число (стоимость за сутки).")
            return
        name = str(data["pending_name"])
        await insert_stay_price_slot(name, price)
        await _finish_to_main(
            message,
            state,
            f"стоимость проживания в сутки «{name}» добавлена.",
        )
        return

    if flow == "sv_price_name":
        if not raw:
            await message.answer("Введите непустое наименование.")
            return
        await state.update_data(flow="sv_price_value", pending_name=raw)
        await message.answer("Введите новую стоимость (число)/день:")
        return

    if flow == "sv_price_value":
        price = _parse_money(raw)
        if price is None:
            await message.answer("Введите целое число.")
            return
        sid = int(data["svc_id"])
        old_nm = str(data["old_svc_name"])
        pending = str(data["pending_name"])
        await update_service_catalog(sid, pending, price)
        await _finish_to_main(
            message,
            state,
            f"Услуга «{old_nm}» обновлена.",
        )
        return

    if flow == "sv_add_name":
        if not raw:
            await message.answer("Введите непустое наименование.")
            return
        await state.update_data(flow="sv_add_price", pending_name=raw)
        await message.answer("Введите стоимость услуги (число)/день:")
        return

    if flow == "sv_add_price":
        price = _parse_money(raw)
        if price is None:
            await message.answer("Введите целое число.")
            return
        name = str(data["pending_name"])
        slug = _new_slug("svc")
        for _ in range(24):
            try:
                await insert_service_catalog(slug, name, price)
                break
            except aiosqlite.IntegrityError:
                slug = _new_slug("svc")
        else:
            await message.answer("Не удалось сохранить услугу, попробуйте снова.")
            return
        await _finish_to_main(message, state, f"Услуга «{name}» добавлена.")
        return

    if flow == "lc_edit_name":
        if not raw:
            await message.answer("Введите непустое наименование.")
            return
        lid = int(data["loc_id"])
        await update_location_catalog(lid, raw)
        await _finish_to_main(
            message,
            state,
            f"Место размещения «{raw}» обновлено.",
        )
        return

    if flow == "lc_add_name":
        if not raw:
            await message.answer("Введите непустое наименование.")
            return
        slug = _new_slug("loc")
        for _ in range(24):
            try:
                await insert_location_catalog(slug, raw)
                break
            except aiosqlite.IntegrityError:
                slug = _new_slug("loc")
        else:
            await message.answer("Не удалось сохранить место, попробуйте снова.")
            return
        await _finish_to_main(
            message,
            state,
            f"Место размещения «{raw}» добавлено.",
        )
        return

    await state.clear()
    await message.answer("Состояние сброшено. Откройте настройки снова.")
