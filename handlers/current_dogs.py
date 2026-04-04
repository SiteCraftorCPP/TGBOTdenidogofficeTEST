import json
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from checkin_logic import (
    PLANNED_CHECKOUT_TIME,
    billable_days,
    build_total,
    dog_label,
    normalize_date_input,
    parse_date_time_pair,
    stay_services_from_row,
)
from config import has_access
from database import (
    fetch_active_stays,
    fetch_stay_by_id,
    get_location_row,
    get_services_map,
    get_stay_price_slot,
    list_locations_catalog,
    list_services_catalog,
    list_stay_price_slots,
    patch_active_stay,
)
from keyboards import (
    BTN_SKIP,
    ERR_STAY_EDIT_PAIR_PARSE,
    ERR_STAY_EDIT_PLANNED_DATE_PARSE,
    PROMPT_DT_CHECKIN_PAIR,
    PROMPT_DT_PLANNED_CHECKOUT_DATE,
    SERVICES_INLINE_CAPTION,
    SKIP_CB_EDIT_NOTES,
    SKIP_CB_EDIT_OWNER,
    SKIP_CB_EDIT_PHOTO,
    remove_kb,
    send_notes_prompt_step,
    skip_inline_kb,
)
from states import StayEditStates

router = Router(name="current_dogs")

_LOCATION_EMOJI: dict[str, str] = {
    "byt1": "🏠",
    "byt2": "🏠",
    "vol": "🦮",
    "ban": "🛁",
}

_MANUAL_NAME_PROMPT = (
    "Наименование (например: груминг, такси, ветеринарные услуги)"
)

_PROMPT_DOG = (
    "Введите: породу, кличку собаки, возраст (через запятую)\n"
    " пример: Пудель, Макс, 3 года"
)
_PROMPT_PHOTO = "Отправьте фото собаки\n или нажмите «пропустить» (фото не меняем)"
_PROMPT_OWNER = (
    "Введите: имя хозяина, контакты (через запятую)\n"
    " пример: Денис, +79934237850\n"
    "или нажмите «пропустить»"
)
_ND_RE = re.compile(r"^nd:(\d+):(dg|nt|ph|ow|ci|co|pr|lc|sv)$")


def _sobak_word(n: int) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "собака"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "собаки"
    return "собак"


def _planned_checkout_time(row: dict) -> str:
    return (row.get("checkout_time") or "").strip() or "00:00"


async def _format_stay_detail(row: dict) -> str:
    dog_info = (row.get("dog_info") or "").strip()
    notes = (row.get("notes") or "").strip()
    owner = (row.get("owner_info") or "").strip()
    cin_d = row.get("checkin_date") or ""
    cin_t = row.get("checkin_time") or ""
    cout_d = row.get("checkout_date") or ""
    cout_t = _planned_checkout_time(row)
    daily = int(row.get("daily_price") or 0)
    loc = row.get("location") or ""
    try:
        n = billable_days(cin_d, cin_t, cout_d, cout_t)
    except ValueError:
        n = 1
    sel, manual = stay_services_from_row(row)
    svc_map = await get_services_map()
    order = [r["slug"] for r in await list_services_catalog()]
    _tot, formula = build_total(
        nights=n,
        daily_price=daily,
        selected_keys=sel,
        manual=manual,
        service_catalog=svc_map,
    )
    lines: list[str] = [f"1. {dog_info}"]
    if notes:
        lines.append(notes)
    if owner:
        lines.append(f"Хозяин: {owner}")
    lines.append(f"Заезд: {cin_d}, {cin_t}")
    if cout_t == PLANNED_CHECKOUT_TIME:
        lines.append(f"Плановая дата выезда: {cout_d}")
    else:
        lines.append(f"Плановый выезд: {cout_d}, {cout_t}")
    lines.append(f"Цена проживания за сутки: {daily} ₽")
    lines.append(f"Место размещения: {loc}")
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
    return "\n".join(lines)


def _stay_card_actions_kb(sid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"nhe:{sid}")],
        ]
    )


def _fields_menu_kb(sid: int) -> InlineKeyboardMarkup:
    p = str(sid)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🐕 Собака", callback_data=f"nd:{p}:dg"),
                InlineKeyboardButton(text="📝 Примечания", callback_data=f"nd:{p}:nt"),
            ],
            [
                InlineKeyboardButton(text="📷 Фото", callback_data=f"nd:{p}:ph"),
                InlineKeyboardButton(text="👤 Хозяин", callback_data=f"nd:{p}:ow"),
            ],
            [
                InlineKeyboardButton(text="📅 Заезд", callback_data=f"nd:{p}:ci"),
                InlineKeyboardButton(text="📅 Выезд", callback_data=f"nd:{p}:co"),
            ],
            [
                InlineKeyboardButton(text="💰 Тариф за сутки", callback_data=f"nd:{p}:pr"),
                InlineKeyboardButton(text="🏠 Место", callback_data=f"nd:{p}:lc"),
            ],
            [
                InlineKeyboardButton(text="🔧 Услуги", callback_data=f"nd:{p}:sv"),
            ],
        ]
    )


def _location_button_text(row: dict) -> str:
    slug = str(row.get("slug") or "").strip()
    name = str(row.get("name") or "").strip() or "—"
    emo = _LOCATION_EMOJI.get(slug, "📍")
    return f"{emo} {name}"


async def _edit_price_ikb(sid: int) -> InlineKeyboardMarkup:
    slots = await list_stay_price_slots()
    p = str(sid)
    rows = [
        [
            InlineKeyboardButton(
                text=f"{s['name']} — {s['price']} ₽",
                callback_data=f"np:{p}:{int(s['id'])}",
            )
        ]
        for s in slots
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _edit_loc_ikb(sid: int) -> InlineKeyboardMarkup:
    locs = await list_locations_catalog()
    p = str(sid)
    rows = [
        [
            InlineKeyboardButton(
                text=_location_button_text(r),
                callback_data=f"nl:{p}:{int(r['id'])}",
            )
        ]
        for r in locs
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _edit_services_ikb(selected: set[str], sid: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    cat = await get_services_map()
    order = [r["slug"] for r in await list_services_catalog()]
    p = str(sid)
    for slug in order:
        if slug not in cat:
            continue
        title, price = cat[slug]
        on = slug in selected
        mark = "✅" if on else "⚪"
        line = f"{mark} {title} - {price} ₽/день"
        rows.append(
            [InlineKeyboardButton(text=line, callback_data=f"et:{p}:{slug}")]
        )
    rows.append(
        [InlineKeyboardButton(text="✏️ Добавить вручную", callback_data=f"em:{p}")]
    )
    rows.append(
        [InlineKeyboardButton(text="✅ Готово", callback_data=f"ed:{p}")]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⏭️ Сбросить выбор", callback_data=f"xs:{p}"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _list_kb(stays: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for s in stays:
        sid = int(s["id"])
        dgi = s.get("dog_info") or ""
        d = s.get("checkin_date") or ""
        t = s.get("checkin_time") or ""
        base = f"{dog_label(dgi)} - Заезд {d}, {t}"
        if len(base) > 64:
            base = base[:61] + "..."
        rows.append([InlineKeyboardButton(text=base, callback_data=f"nh:{sid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _recalc_stay_totals(sid: int) -> tuple[bool, str | None]:
    row = await fetch_stay_by_id(sid)
    if not row or int(row.get("is_active") or 1) == 0:
        return False, "Запись не найдена."
    cin_d = row.get("checkin_date") or ""
    cin_t = row.get("checkin_time") or ""
    cout_d = row.get("checkout_date") or ""
    cout_t = _planned_checkout_time(row)
    try:
        n = billable_days(cin_d, cin_t, cout_d, cout_t)
    except ValueError as e:
        if str(e) == "checkout_before_checkin":
            return (
                False,
                "Плановая дата выезда раньше заезда — сумму не обновила. Исправьте даты.",
            )
        return False, "Проверьте даты и время — сумму не обновила."
    daily = int(row.get("daily_price") or 0)
    sel, manual = stay_services_from_row(row)
    svc_map = await get_services_map()
    total, formula = build_total(
        nights=n,
        daily_price=daily,
        selected_keys=sel,
        manual=manual,
        service_catalog=svc_map,
    )
    await patch_active_stay(sid, total_amount=total, total_formula=formula)
    return True, None


async def _after_edit_save(
    message: Message, sid: int, *, warn: str | None = None
) -> None:
    row = await fetch_stay_by_id(sid)
    if not row or int(row.get("is_active") or 1) == 0:
        await message.answer("Запись не найдена.")
        return
    text = await _format_stay_detail(row)
    lines = ["Сохранено."]
    if warn:
        lines.append(warn)
    await message.answer("\n".join(lines))
    await message.answer(text, reply_markup=_stay_card_actions_kb(sid))


async def _load_edit_sid(state: FSMContext) -> int | None:
    data = await state.get_data()
    sid = data.get("edit_sid")
    if sid is None:
        return None
    try:
        return int(sid)
    except (TypeError, ValueError):
        return None


async def _ensure_active_stay(sid: int) -> dict | None:
    row = await fetch_stay_by_id(sid)
    if not row or int(row.get("is_active") or 1) == 0:
        return None
    return row


@router.message(F.text == "🐾 Сейчас в гостинице")
async def current_dogs_entry(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not has_access(uid):
        await message.answer("Нет доступа.")
        return
    await state.clear()
    stays = await fetch_active_stays()
    n = len(stays)
    head = f"Сейчас: {n} {_sobak_word(n)}"
    if n == 0:
        await message.answer(head)
        return
    await message.answer(head, reply_markup=_list_kb(stays))


@router.callback_query(F.data.startswith("nh:"))
async def current_dogs_open(query: CallbackQuery) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not has_access(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    try:
        sid = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    row = await fetch_stay_by_id(sid)
    if not row or int(row.get("is_active") or 1) == 0:
        await query.answer("Не найдено.", show_alert=True)
        return
    text = await _format_stay_detail(row)
    await query.message.answer(text, reply_markup=_stay_card_actions_kb(sid))
    await query.answer()


@router.callback_query(F.data.startswith("nhe:"))
async def current_dogs_edit_menu(query: CallbackQuery) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not has_access(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    try:
        sid = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    if not await _ensure_active_stay(sid):
        await query.answer("Не найдено.", show_alert=True)
        return
    await query.message.answer(
        "Что изменить?",
        reply_markup=_fields_menu_kb(sid),
    )
    await query.answer()


@router.callback_query(F.data.startswith("nd:"))
async def current_dogs_edit_field_start(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not has_access(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    m = _ND_RE.match((query.data or "").strip())
    if not m:
        await query.answer()
        return
    sid = int(m.group(1))
    code = m.group(2)
    if not await _ensure_active_stay(sid):
        await query.answer("Не найдено.", show_alert=True)
        return

    await state.update_data(edit_sid=sid)

    if code == "dg":
        await state.set_state(StayEditStates.dog_line)
        await query.message.answer(_PROMPT_DOG, reply_markup=remove_kb())
        await query.answer()
        return
    if code == "nt":
        await state.set_state(StayEditStates.notes)
        await send_notes_prompt_step(query.message, SKIP_CB_EDIT_NOTES)
        await query.answer()
        return
    if code == "ph":
        await state.set_state(StayEditStates.photo)
        await query.message.answer(
            _PROMPT_PHOTO, reply_markup=skip_inline_kb(SKIP_CB_EDIT_PHOTO)
        )
        await query.answer()
        return
    if code == "ow":
        await state.set_state(StayEditStates.owner)
        await query.message.answer(
            _PROMPT_OWNER, reply_markup=skip_inline_kb(SKIP_CB_EDIT_OWNER)
        )
        await query.answer()
        return
    if code == "ci":
        await state.set_state(StayEditStates.cin_pair)
        await query.message.answer(PROMPT_DT_CHECKIN_PAIR, reply_markup=remove_kb())
        await query.answer()
        return
    if code == "co":
        await state.set_state(StayEditStates.cout_pair)
        await query.message.answer(
            PROMPT_DT_PLANNED_CHECKOUT_DATE, reply_markup=remove_kb()
        )
        await query.answer()
        return
    if code == "pr":
        await state.set_state(StayEditStates.choose_price)
        ikb = await _edit_price_ikb(sid)
        if not ikb.inline_keyboard:
            await query.message.answer("Нет тарифов. Добавьте в настройках.")
            await state.clear()
            await query.answer()
            return
        await query.message.answer("Выберите тариф за сутки:", reply_markup=ikb)
        await query.answer()
        return
    if code == "lc":
        await state.set_state(StayEditStates.choose_location)
        ikb = await _edit_loc_ikb(sid)
        if not ikb.inline_keyboard:
            await query.message.answer("Нет мест размещения. Добавьте в настройках.")
            await state.clear()
            await query.answer()
            return
        await query.message.answer("🏠 Место размещения:", reply_markup=ikb)
        await query.answer()
        return
    if code == "sv":
        row = await fetch_stay_by_id(sid)
        if not row:
            await query.answer()
            return
        sel, manual = stay_services_from_row(row)
        await state.set_state(StayEditStates.services_pick)
        await state.update_data(
            edit_sid=sid,
            edit_svc_sel=list(sel),
            edit_svc_manual=list(manual),
        )
        await query.message.answer(
            SERVICES_INLINE_CAPTION,
            reply_markup=await _edit_services_ikb(sel, sid),
        )
        await query.answer()


@router.message(StayEditStates.dog_line, F.text)
async def edit_dog_line(message: Message, state: FSMContext) -> None:
    sid = await _load_edit_sid(state)
    if sid is None or not await _ensure_active_stay(sid):
        await state.clear()
        await message.answer("Сессия редактирования сброшена.")
        return
    line = (message.text or "").strip()
    if not line:
        await message.answer(_PROMPT_DOG)
        return
    await patch_active_stay(sid, dog_info=line)
    await state.clear()
    await _after_edit_save(message, sid)


@router.callback_query(StayEditStates.notes, F.data == SKIP_CB_EDIT_NOTES)
async def edit_skip_notes_cb(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    sid = await _load_edit_sid(state)
    if sid is None or not await _ensure_active_stay(sid):
        await state.clear()
        await query.message.answer("Сессия редактирования сброшена.")
        return
    await patch_active_stay(sid, notes="")
    await state.clear()
    await _after_edit_save(query.message, sid)


@router.message(StayEditStates.notes, F.text)
async def edit_notes(message: Message, state: FSMContext) -> None:
    sid = await _load_edit_sid(state)
    if sid is None or not await _ensure_active_stay(sid):
        await state.clear()
        await message.answer("Сессия редактирования сброшена.")
        return
    t = (message.text or "").strip()
    notes = "" if t == BTN_SKIP else t
    await patch_active_stay(sid, notes=notes)
    await state.clear()
    await _after_edit_save(message, sid)


@router.callback_query(StayEditStates.photo, F.data == SKIP_CB_EDIT_PHOTO)
async def edit_skip_photo_cb(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    sid = await _load_edit_sid(state)
    if sid is None or not await _ensure_active_stay(sid):
        await state.clear()
        await query.message.answer("Сессия редактирования сброшена.")
        return
    await state.clear()
    await query.message.answer("Фото не меняли.")
    text = await _format_stay_detail(await fetch_stay_by_id(sid))
    await query.message.answer(text, reply_markup=_stay_card_actions_kb(sid))


@router.message(StayEditStates.photo, F.text)
async def edit_photo_wrong_text(message: Message, state: FSMContext) -> None:
    sid = await _load_edit_sid(state)
    if sid is None or not await _ensure_active_stay(sid):
        await state.clear()
        await message.answer("Сессия редактирования сброшена.")
        return
    if (message.text or "").strip() == BTN_SKIP:
        await state.clear()
        await message.answer("Фото не меняли.")
        text = await _format_stay_detail(await fetch_stay_by_id(sid))
        await message.answer(text, reply_markup=_stay_card_actions_kb(sid))
        return
    await message.answer(
        _PROMPT_PHOTO, reply_markup=skip_inline_kb(SKIP_CB_EDIT_PHOTO)
    )


@router.message(StayEditStates.photo, F.photo)
async def edit_photo_file(message: Message, state: FSMContext) -> None:
    sid = await _load_edit_sid(state)
    if sid is None or not await _ensure_active_stay(sid):
        await state.clear()
        await message.answer("Сессия редактирования сброшена.")
        return
    if not message.photo:
        return
    fid = message.photo[-1].file_id
    await patch_active_stay(sid, photo_file_id=fid)
    await state.clear()
    await _after_edit_save(message, sid)


@router.message(StayEditStates.photo)
async def edit_photo_other(message: Message) -> None:
    await message.answer(
        _PROMPT_PHOTO, reply_markup=skip_inline_kb(SKIP_CB_EDIT_PHOTO)
    )


@router.callback_query(StayEditStates.owner, F.data == SKIP_CB_EDIT_OWNER)
async def edit_skip_owner_cb(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    sid = await _load_edit_sid(state)
    if sid is None or not await _ensure_active_stay(sid):
        await state.clear()
        await query.message.answer("Сессия редактирования сброшена.")
        return
    await patch_active_stay(sid, owner_info="")
    await state.clear()
    await _after_edit_save(query.message, sid)


@router.message(StayEditStates.owner, F.text)
async def edit_owner(message: Message, state: FSMContext) -> None:
    sid = await _load_edit_sid(state)
    if sid is None or not await _ensure_active_stay(sid):
        await state.clear()
        await message.answer("Сессия редактирования сброшена.")
        return
    t = (message.text or "").strip()
    owner = "" if t == BTN_SKIP else t
    await patch_active_stay(sid, owner_info=owner)
    await state.clear()
    await _after_edit_save(message, sid)


@router.message(StayEditStates.cin_pair, F.text)
async def edit_cin_pair(message: Message, state: FSMContext) -> None:
    sid = await _load_edit_sid(state)
    if sid is None or not await _ensure_active_stay(sid):
        await state.clear()
        await message.answer("Сессия редактирования сброшена.")
        return
    raw = (message.text or "").strip()
    parsed = parse_date_time_pair(raw)
    if not parsed:
        await message.answer(f"{ERR_STAY_EDIT_PAIR_PARSE}\n\n{PROMPT_DT_CHECKIN_PAIR}")
        return
    d, t = parsed
    row = await fetch_stay_by_id(sid)
    if not row:
        await state.clear()
        await message.answer("Запись не найдена.")
        return
    cout_d = row.get("checkout_date") or ""
    cout_t = _planned_checkout_time(row)
    try:
        billable_days(d, t, cout_d, cout_t)
    except ValueError as e:
        if str(e) == "checkout_before_checkin":
            await message.answer(
                "С таким заездом текущая плановая дата выезда оказалась бы раньше. "
                "Сначала измените плановую дату выезда или укажите другую пару заезда.\n\n"
                f"{PROMPT_DT_CHECKIN_PAIR}"
            )
        else:
            await message.answer(f"{ERR_STAY_EDIT_PAIR_PARSE}\n\n{PROMPT_DT_CHECKIN_PAIR}")
        return
    await patch_active_stay(sid, checkin_date=d, checkin_time=t)
    ok, err = await _recalc_stay_totals(sid)
    await state.clear()
    await _after_edit_save(message, sid, warn=None if ok else err)


@router.message(StayEditStates.cout_pair, F.text)
async def edit_cout_pair(message: Message, state: FSMContext) -> None:
    sid = await _load_edit_sid(state)
    if sid is None or not await _ensure_active_stay(sid):
        await state.clear()
        await message.answer("Сессия редактирования сброшена.")
        return
    raw = (message.text or "").strip()
    try:
        d = normalize_date_input(raw, pick_last=False)
    except ValueError:
        await message.answer(
            f"{ERR_STAY_EDIT_PLANNED_DATE_PARSE}\n\n{PROMPT_DT_PLANNED_CHECKOUT_DATE}"
        )
        return
    row = await fetch_stay_by_id(sid)
    if not row:
        await state.clear()
        await message.answer("Запись не найдена.")
        return
    cin_d = row.get("checkin_date") or ""
    cin_t = (row.get("checkin_time") or "").strip() or "00:00"
    try:
        billable_days(cin_d, cin_t, d, PLANNED_CHECKOUT_TIME)
    except ValueError as e:
        if str(e) == "checkout_before_checkin":
            await message.answer(
                "Плановая дата выезда не может быть раньше заезда.\n\n"
                f"{PROMPT_DT_PLANNED_CHECKOUT_DATE}"
            )
        else:
            await message.answer(
                f"{ERR_STAY_EDIT_PLANNED_DATE_PARSE}\n\n{PROMPT_DT_PLANNED_CHECKOUT_DATE}"
            )
        return
    await patch_active_stay(
        sid, checkout_date=d, checkout_time=PLANNED_CHECKOUT_TIME
    )
    ok, err = await _recalc_stay_totals(sid)
    await state.clear()
    await _after_edit_save(message, sid, warn=None if ok else err)


@router.callback_query(StayEditStates.choose_price, F.data.startswith("np:"))
async def edit_price_cb(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not has_access(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await query.answer()
        return
    try:
        sid = int(parts[1])
        slot_id = int(parts[2])
    except ValueError:
        await query.answer()
        return
    data_sid = await _load_edit_sid(state)
    if data_sid != sid:
        await query.answer("Устарело. Откройте карточку снова.", show_alert=True)
        return
    if not await _ensure_active_stay(sid):
        await state.clear()
        await query.answer("Не найдено.", show_alert=True)
        return
    row = await get_stay_price_slot(slot_id)
    if not row:
        await query.answer()
        return
    await patch_active_stay(sid, daily_price=int(row["price"]))
    ok, err = await _recalc_stay_totals(sid)
    await state.clear()
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await query.answer()
    await _after_edit_save(query.message, sid, warn=None if ok else err)


@router.callback_query(StayEditStates.choose_location, F.data.startswith("nl:"))
async def edit_location_cb(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not has_access(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await query.answer()
        return
    try:
        sid = int(parts[1])
        loc_id = int(parts[2])
    except ValueError:
        await query.answer()
        return
    data_sid = await _load_edit_sid(state)
    if data_sid != sid:
        await query.answer("Устарело. Откройте карточку снова.", show_alert=True)
        return
    if not await _ensure_active_stay(sid):
        await state.clear()
        await query.answer("Не найдено.", show_alert=True)
        return
    loc_row = await get_location_row(loc_id)
    if not loc_row:
        await query.answer()
        return
    loc = str(loc_row["name"])
    await patch_active_stay(sid, location=loc)
    await state.clear()
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await query.answer()
    await _after_edit_save(query.message, sid)


@router.callback_query(StayEditStates.services_pick, F.data.startswith("et:"))
async def edit_svc_toggle(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not has_access(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    parts = (query.data or "").split(":")
    if len(parts) < 3:
        await query.answer()
        return
    try:
        sid = int(parts[1])
    except ValueError:
        await query.answer()
        return
    slug = ":".join(parts[2:])
    data = await state.get_data()
    if int(data.get("edit_sid") or 0) != sid:
        await query.answer("Устарело.", show_alert=True)
        return
    sm = await get_services_map()
    if slug not in sm:
        await query.answer()
        return
    sel_list = list(data.get("edit_svc_sel") or [])
    sel = set(sel_list)
    if slug in sel:
        sel.remove(slug)
    else:
        sel.add(slug)
    await state.update_data(edit_svc_sel=list(sel))
    try:
        await query.message.edit_reply_markup(
            reply_markup=await _edit_services_ikb(sel, sid)
        )
    except Exception:
        pass
    await query.answer()


@router.callback_query(StayEditStates.services_pick, F.data.startswith("xs:"))
async def edit_svc_reset_selection(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not has_access(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    try:
        sid = int((query.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    data = await state.get_data()
    if int(data.get("edit_sid") or 0) != sid:
        await query.answer("Устарело.", show_alert=True)
        return
    await state.update_data(edit_svc_sel=[], edit_svc_manual=[])
    try:
        await query.message.edit_reply_markup(
            reply_markup=await _edit_services_ikb(set(), sid)
        )
    except Exception:
        pass
    await query.answer()


@router.callback_query(StayEditStates.services_pick, F.data.startswith("em:"))
async def edit_svc_manual_start(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not has_access(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    try:
        sid = int((query.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    data = await state.get_data()
    if int(data.get("edit_sid") or 0) != sid:
        await query.answer("Устарело.", show_alert=True)
        return
    await state.set_state(StayEditStates.manual_name)
    await query.message.answer(_MANUAL_NAME_PROMPT, reply_markup=remove_kb())
    await query.answer()


@router.callback_query(StayEditStates.services_pick, F.data.startswith("ed:"))
async def edit_svc_done(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not has_access(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    try:
        sid = int((query.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    data = await state.get_data()
    if int(data.get("edit_sid") or 0) != sid:
        await query.answer("Устарело.", show_alert=True)
        return
    if not await _ensure_active_stay(sid):
        await state.clear()
        await query.answer("Не найдено.", show_alert=True)
        return
    sel = set(data.get("edit_svc_sel") or [])
    manual = list(data.get("edit_svc_manual") or [])
    services = {k: True for k in sel}
    await patch_active_stay(
        sid,
        services_json=services,
        manual_services_json=manual,
    )
    ok, err = await _recalc_stay_totals(sid)
    await state.clear()
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await query.answer()
    await _after_edit_save(query.message, sid, warn=None if ok else err)


@router.message(StayEditStates.manual_name, F.text)
async def edit_manual_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer(_MANUAL_NAME_PROMPT)
        return
    await state.update_data(manual_name_tmp=name)
    await state.set_state(StayEditStates.manual_amount)
    await message.answer("Сумма (только число):__________ 2500 руб.")


@router.message(StayEditStates.manual_amount, F.text)
async def edit_manual_amount(message: Message, state: FSMContext) -> None:
    sid = await _load_edit_sid(state)
    if sid is None or not await _ensure_active_stay(sid):
        await state.clear()
        await message.answer("Сессия редактирования сброшена.")
        return
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit():
        await message.answer("Сумма (только число):__________ 2500 руб.")
        return
    amt = int(raw)
    data = await state.get_data()
    name = data.get("manual_name_tmp") or ""
    manual = list(data.get("edit_svc_manual") or [])
    manual.append({"name": name, "amount": amt})
    sel_list = list(data.get("edit_svc_sel") or [])
    sel = set(sel_list)
    await state.update_data(edit_svc_manual=manual)
    await state.set_state(StayEditStates.services_pick)
    await message.answer(
        SERVICES_INLINE_CAPTION,
        reply_markup=await _edit_services_ikb(sel, sid),
    )
