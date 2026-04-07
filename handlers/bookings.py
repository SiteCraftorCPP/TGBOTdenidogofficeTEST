import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from checkin_logic import (
    PLANNED_CHECKOUT_TIME,
    billable_days,
    build_total,
    dog_label,
    inline_button_text,
    parse_checkin_planned_block,
    parse_manual_service_line,
    stay_prepayment_lines,
    stay_services_from_row,
)
from config import has_access
from database import (
    cancel_booking,
    fetch_active_bookings,
    fetch_booking_by_id,
    get_location_row,
    get_services_map,
    get_stay_price_slot,
    insert_booking,
    insert_stay,
    list_locations_catalog,
    list_services_catalog,
    list_stay_price_slots,
    patch_active_booking,
    patch_active_stay,
    validate_booking_capacity,
)
from keyboards import (
    BTN_SKIP,
    ERR_CHECKIN_BLOCK_CHECKOUT_BEFORE,
    ERR_MANUAL_SERVICE_PARSE,
    MAIN_MENU_CAPTION,
    PROMPT_MANUAL_SERVICE_LINE,
    SERVICES_INLINE_CAPTION,
    SKIP_CB_CHECKIN_NOTES,
    SKIP_CB_CHECKIN_OWNER,
    SKIP_CB_CHECKIN_PHOTO,
    main_menu_kb_for,
    remove_kb,
    send_notes_prompt_step,
    skip_inline_kb,
)
from states import BookingListStates, BookingStates

router = Router(name="bookings")

_LOCATION_EMOJI: dict[str, str] = {
    "byt1": "🏠",
    "byt2": "🏠",
    "vol": "🦮",
    "ban": "🛁",
}

_PROMPT_BOOKING_DATES = (
    "Введите планируемые: дату заезда, время заезда, дату выезда (через запятую)\n"
    "пример: 10.03.26, 14:30, 15.03.26"
)
_PROMPT_BOOKING_CHECKIN_FROM_LIST = (
    "Введите: дату заезда, время заезда, планируемую дату выезда (через запятую)\n"
    "пример: 10.03.26, 14:30, 15.03.26"
)


def _pay_offer_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Внести предоплату", callback_data="bpay:yes"),
                InlineKeyboardButton(text="Не сейчас", callback_data="bpay:no"),
            ]
        ]
    )


def _services_ask_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Добавить", callback_data="bs:add"),
                InlineKeyboardButton(text=BTN_SKIP, callback_data="bs:skip"),
            ]
        ]
    )


def _confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Подтвердить", callback_data="b:ok"),
                InlineKeyboardButton(text="Отменить", callback_data="b:cancel"),
            ]
        ]
    )


async def _price_kb() -> InlineKeyboardMarkup:
    slots = await list_stay_price_slots()
    rows = [
        [
            InlineKeyboardButton(
                text=f"{s['name']} — {s['price']} ₽",
                callback_data=f"bp:{int(s['id'])}",
            )
        ]
        for s in slots
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _loc_kb() -> InlineKeyboardMarkup:
    locs = await list_locations_catalog()
    rows = [
        [
            InlineKeyboardButton(
                text=_location_button_text(r),
                callback_data=f"bl:{int(r['id'])}",
            )
        ]
        for r in locs
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _location_button_text(row: dict) -> str:
    slug = str(row.get("slug") or "").strip()
    name = str(row.get("name") or "").strip() or "—"
    emo = _LOCATION_EMOJI.get(slug, "📍")
    return f"{emo} {name}"


async def _services_pick_kb(selected: set[str], manual: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    cat = await get_services_map()
    order = [r["slug"] for r in await list_services_catalog()]
    for slug in order:
        if slug not in cat:
            continue
        title, price = cat[slug]
        on = slug in selected
        mark = "☑" if on else "☐"
        line = f"{mark} {title} - {price} /день ₽"
        rows.append(
            [
                InlineKeyboardButton(
                    text=inline_button_text(line),
                    callback_data=f"bt:{slug}",
                )
            ]
        )
    for i, m in enumerate(manual):
        nm = str(m.get("name") or "").strip()
        amt = int(m.get("amount") or 0)
        line = f"☑ {nm} — {amt} ₽"
        rows.append(
            [
                InlineKeyboardButton(
                    text=inline_button_text(line),
                    callback_data=f"bmu:{i}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ Добавить вручную", callback_data="bmanual")])
    rows.append([InlineKeyboardButton(text="🟦 Готово", callback_data="bdone")])
    rows.append([InlineKeyboardButton(text="⏭️ Пропустить", callback_data="bskip")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _booking_list_kb(rows: list[dict]) -> InlineKeyboardMarkup:
    out: list[list[InlineKeyboardButton]] = []
    for r in rows:
        bid = int(r["id"])
        d1 = (r.get("checkin_date") or "").strip()
        d2 = (r.get("checkout_date") or "").strip()
        di = r.get("dog_info") or ""
        ow = (r.get("owner_info") or "").strip()
        label = f"{d1}-{d2} {dog_label(di)}"
        if ow:
            label += f" | {ow.split(',')[0].strip()}"
        if len(label) > 64:
            label = label[:61] + "..."
        out.append([InlineKeyboardButton(text=label, callback_data=f"bo:{bid}")])
    return InlineKeyboardMarkup(inline_keyboard=out)


def _booking_card_kb(bid: int, *, confirmed: bool = False) -> InlineKeyboardMarkup:
    if not confirmed:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Подтвердить бронь", callback_data=f"bcf:{bid}"
                    ),
                    InlineKeyboardButton(text="Отменить", callback_data=f"bx:{bid}"),
                ]
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Оформить заезд", callback_data=f"bci:{bid}"),
                InlineKeyboardButton(text="Отменить", callback_data=f"bx:{bid}"),
            ]
        ]
    )


async def _format_booking(row: dict) -> str:
    dog = (row.get("dog_info") or "").strip()
    notes = (row.get("notes") or "").strip()
    owner = (row.get("owner_info") or "").strip()
    cin_d = row.get("checkin_date") or ""
    cin_t = row.get("checkin_time") or ""
    cout_d = row.get("checkout_date") or ""
    daily = int(row.get("daily_price") or 0)
    loc = row.get("location") or ""
    sel, manual = stay_services_from_row(row)
    svc_map = await get_services_map()
    order = [r["slug"] for r in await list_services_catalog()]
    try:
        n = billable_days(cin_d, cin_t, cout_d, PLANNED_CHECKOUT_TIME)
    except ValueError:
        n = 1
    total, formula = build_total(
        nights=n,
        daily_price=daily,
        selected_keys=sel,
        manual=manual,
        service_catalog=svc_map,
    )
    lines = ["📋 Бронь:"]
    if dog:
        lines.append(f"Собака: {dog}")
    if notes:
        lines.append(notes)
    if owner:
        lines.append(f"Хозяин: {owner}")
    lines.append(f"Планируемые даты заезда/выезда: {cin_d}, {cout_d}")
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
            lines.append(rest)
        for ml in manual_lines:
            lines.append(ml)
    elif manual_lines:
        lines.append(f"Услуги: {manual_lines[0]}")
        for rest in manual_lines[1:]:
            lines.append(rest)
    else:
        lines.append("Услуги: —")
    lines.append(f"Общая сумма: {formula}")
    paid = int(row.get("prepayment_amount") or 0)
    lines.extend(stay_prepayment_lines(paid, total))
    return "\n".join(lines)


@router.message(F.text == "📅 Бронирование")
async def booking_start(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not has_access(uid):
        await message.answer("Нет доступа.")
        return
    await state.clear()
    await state.set_state(BookingStates.dog_line)
    await message.answer(
        "Введите: породу, кличку собаки, возраст (через запятую)\n"
        "пример: Пудель, Макс, 3 года",
        reply_markup=remove_kb(),
    )


@router.message(BookingStates.dog_line, F.text)
async def booking_dog(message: Message, state: FSMContext) -> None:
    await state.update_data(dog_line=(message.text or "").strip())
    await state.set_state(BookingStates.notes)
    await send_notes_prompt_step(message, SKIP_CB_CHECKIN_NOTES)


@router.callback_query(BookingStates.notes, F.data == SKIP_CB_CHECKIN_NOTES)
async def booking_skip_notes(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await state.update_data(notes="")
        await state.set_state(BookingStates.photo)
        await query.message.answer(
            "Отправьте фото собаки\n или нажмите «пропустить»",
            reply_markup=skip_inline_kb(SKIP_CB_CHECKIN_PHOTO),
        )


@router.message(BookingStates.notes, F.text)
async def booking_notes(message: Message, state: FSMContext) -> None:
    t = (message.text or "").strip()
    await state.update_data(notes="" if t == BTN_SKIP else t)
    await state.set_state(BookingStates.photo)
    await message.answer(
        "Отправьте фото собаки\n или нажмите «пропустить»",
        reply_markup=skip_inline_kb(SKIP_CB_CHECKIN_PHOTO),
    )


@router.callback_query(BookingStates.photo, F.data == SKIP_CB_CHECKIN_PHOTO)
async def booking_skip_photo(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await state.update_data(photo_file_id=None)
        await state.set_state(BookingStates.owner)
        await query.message.answer(
            "Введите: имя хозяина, контакты (через запятую)\n"
            "пример: Денис, +79934237850 или нажмите «пропустить»",
            reply_markup=skip_inline_kb(SKIP_CB_CHECKIN_OWNER),
        )


@router.message(BookingStates.photo, F.photo)
async def booking_photo(message: Message, state: FSMContext) -> None:
    if message.photo:
        await state.update_data(photo_file_id=message.photo[-1].file_id)
    await state.set_state(BookingStates.owner)
    await message.answer(
        "Введите: имя хозяина, контакты (через запятую)\n"
        "пример: Денис, +79934237850 или нажмите «пропустить»",
        reply_markup=skip_inline_kb(SKIP_CB_CHECKIN_OWNER),
    )


@router.callback_query(BookingStates.owner, F.data == SKIP_CB_CHECKIN_OWNER)
async def booking_skip_owner(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await state.update_data(owner="")
        await state.set_state(BookingStates.dates)
        await query.message.answer(_PROMPT_BOOKING_DATES, reply_markup=remove_kb())


@router.message(BookingStates.owner, F.text)
async def booking_owner(message: Message, state: FSMContext) -> None:
    t = (message.text or "").strip()
    await state.update_data(owner="" if t == BTN_SKIP else t)
    await state.set_state(BookingStates.dates)
    await message.answer(_PROMPT_BOOKING_DATES, reply_markup=remove_kb())


@router.message(BookingStates.dates, F.text)
async def booking_dates(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    parsed = parse_checkin_planned_block(raw)
    if not parsed:
        await message.answer(_PROMPT_BOOKING_DATES)
        return
    d1, tm, d2 = parsed
    try:
        billable_days(d1, tm, d2, PLANNED_CHECKOUT_TIME)
    except ValueError as e:
        if str(e) == "checkout_before_checkin":
            await message.answer(ERR_CHECKIN_BLOCK_CHECKOUT_BEFORE)
            return
        await message.answer(_PROMPT_BOOKING_DATES)
        return
    await state.update_data(
        checkin_date=d1,
        checkin_time=tm,
        checkout_date=d2,
        checkout_time=PLANNED_CHECKOUT_TIME,
    )
    await state.set_state(BookingStates.price)
    ikb = await _price_kb()
    if not ikb.inline_keyboard:
        await message.answer("Нет тарифов проживания. Добавьте в настройках.")
        return
    await message.answer("💰 Выберите цену проживания за сутки:", reply_markup=ikb)


@router.callback_query(BookingStates.price, F.data.startswith("bp:"))
async def booking_price_cb(query: CallbackQuery, state: FSMContext) -> None:
    sid = int(query.data.split(":", 1)[1])
    row = await get_stay_price_slot(sid)
    if not row:
        await query.answer()
        return
    await state.update_data(daily_price=int(row["price"]))
    await state.set_state(BookingStates.location)
    await query.message.edit_reply_markup(reply_markup=None)
    await query.message.answer(f"{row['name']} — {int(row['price'])} ₽")
    kb = await _loc_kb()
    await query.message.answer("🏠 Выберите место размещения:", reply_markup=kb)
    await query.answer()


@router.callback_query(BookingStates.location, F.data.startswith("bl:"))
async def booking_loc_cb(query: CallbackQuery, state: FSMContext) -> None:
    lid = int(query.data.split(":", 1)[1])
    loc_row = await get_location_row(lid)
    if not loc_row:
        await query.answer()
        return
    await state.update_data(location=str(loc_row["name"]))
    await state.set_state(BookingStates.services_ask)
    await query.message.edit_reply_markup(reply_markup=None)
    await query.message.answer(_location_button_text(loc_row))
    await query.message.answer("🔧 Добавить услуги сейчас?", reply_markup=_services_ask_kb())
    await query.answer()


@router.callback_query(BookingStates.services_ask, F.data.in_({"bs:add", "bs:skip"}))
async def booking_services_ask(query: CallbackQuery, state: FSMContext) -> None:
    await query.message.edit_reply_markup(reply_markup=None)
    if query.data == "bs:skip":
        await state.update_data(svc_selected=set(), svc_manual=[])
        await _send_booking_summary(query.message, state)
        await query.answer()
        return
    await state.update_data(svc_selected=set(), svc_manual=[])
    await state.set_state(BookingStates.services_pick)
    await query.message.answer(
        SERVICES_INLINE_CAPTION,
        reply_markup=await _services_pick_kb(set(), []),
    )
    await query.answer()


@router.callback_query(BookingStates.services_pick, F.data.startswith("bt:"))
async def booking_toggle_service(query: CallbackQuery, state: FSMContext) -> None:
    slug = (query.data or "").split(":", 1)[1]
    sm = await get_services_map()
    if slug not in sm:
        await query.answer()
        return
    data = await state.get_data()
    sel = set(data.get("svc_selected") or [])
    manual = list(data.get("svc_manual") or [])
    if slug in sel:
        sel.remove(slug)
    else:
        sel.add(slug)
    await state.update_data(svc_selected=sel)
    await query.message.edit_reply_markup(reply_markup=await _services_pick_kb(sel, manual))
    await query.answer()


@router.callback_query(BookingStates.services_pick, F.data == "bmanual")
async def booking_manual_start(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BookingStates.manual_name)
    await query.message.answer(PROMPT_MANUAL_SERVICE_LINE, reply_markup=remove_kb())
    await query.answer()


@router.message(BookingStates.manual_name, F.text)
async def booking_manual_line(message: Message, state: FSMContext) -> None:
    parsed = parse_manual_service_line(message.text or "")
    if not parsed:
        await message.answer(f"{ERR_MANUAL_SERVICE_PARSE}\n\n{PROMPT_MANUAL_SERVICE_LINE}")
        return
    name, amt = parsed
    data = await state.get_data()
    manual = list(data.get("svc_manual") or [])
    manual.append({"name": name, "amount": amt})
    await state.update_data(svc_manual=manual)
    sel = set(data.get("svc_selected") or [])
    await state.set_state(BookingStates.services_pick)
    await message.answer(
        SERVICES_INLINE_CAPTION,
        reply_markup=await _services_pick_kb(sel, manual),
    )


@router.callback_query(BookingStates.services_pick, F.data.startswith("bmu:"))
async def booking_manual_remove(query: CallbackQuery, state: FSMContext) -> None:
    try:
        idx = int((query.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    data = await state.get_data()
    manual = list(data.get("svc_manual") or [])
    if 0 <= idx < len(manual):
        manual.pop(idx)
    await state.update_data(svc_manual=manual)
    sel = set(data.get("svc_selected") or [])
    await query.message.edit_reply_markup(reply_markup=await _services_pick_kb(sel, manual))
    await query.answer()


@router.callback_query(BookingStates.services_pick, F.data == "bdone")
async def booking_services_done(query: CallbackQuery, state: FSMContext) -> None:
    await query.message.edit_reply_markup(reply_markup=None)
    await _send_booking_summary(query.message, state)
    await query.answer()


@router.callback_query(BookingStates.services_pick, F.data == "bskip")
async def booking_services_skip(query: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(svc_selected=set(), svc_manual=[])
    await query.message.edit_reply_markup(reply_markup=None)
    await _send_booking_summary(query.message, state)
    await query.answer()


async def _send_booking_summary(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    svc_map = await get_services_map()
    svc_order = [r["slug"] for r in await list_services_catalog()]
    d1 = data["checkin_date"]
    tm = data["checkin_time"]
    d2 = data["checkout_date"]
    daily = int(data["daily_price"])
    loc = data["location"]
    sel = set(data.get("svc_selected") or [])
    manual = list(data.get("svc_manual") or [])
    n = billable_days(d1, tm, d2, PLANNED_CHECKOUT_TIME)
    total, formula = build_total(
        nights=n,
        daily_price=daily,
        selected_keys=sel,
        manual=manual,
        service_catalog=svc_map,
    )
    await state.update_data(total_amount=total, total_formula=formula)
    lines = [
        "📅 Бронирование:",
        f"Собака: {data.get('dog_line','')}",
    ]
    notes = (data.get("notes") or "").strip()
    if notes:
        lines.append(notes)
    owner = (data.get("owner") or "").strip()
    if owner:
        lines.append(f"Хозяин: {owner}")
    lines.append(f"Планируемые даты заезда/выезда: {d1}, {d2}")
    lines.append(f"Цена проживания за сутки: {daily} ₽")
    lines.append(f"Место размещения: {loc}")
    daily_lines: list[str] = []
    for slug in svc_order:
        if slug in sel and slug in svc_map:
            title, per = svc_map[slug]
            daily_lines.append(f"{title} — {per} /день ₽")
    manual_lines = [f"{m['name']} — {m['amount']} руб." for m in manual]
    if daily_lines:
        lines.append(f"Услуги: {daily_lines[0]}")
        for rest in daily_lines[1:]:
            lines.append(rest)
        for ml in manual_lines:
            lines.append(ml)
    elif manual_lines:
        lines.append(f"Услуги: {manual_lines[0]}")
        for rest in manual_lines[1:]:
            lines.append(rest)
    else:
        lines.append("Услуги: —")
    lines.append(f"Общая сумма: {formula}")
    lines.append("Подтвердите бронирование:")
    await state.set_state(BookingStates.confirm)
    text = "\n".join(lines)
    photo_id = data.get("photo_file_id")
    if photo_id and len(text) < 900:
        await message.answer_photo(photo_id, caption=text, reply_markup=_confirm_kb())
    else:
        if photo_id:
            await message.answer_photo(photo_id)
        await message.answer(text, reply_markup=_confirm_kb())


@router.callback_query(BookingStates.confirm, F.data.in_({"b:ok", "b:cancel"}))
async def booking_confirm(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    await query.message.edit_reply_markup(reply_markup=None)
    if query.data == "b:cancel":
        await state.clear()
        await query.message.answer(MAIN_MENU_CAPTION, reply_markup=main_menu_kb_for(uid))
        await query.answer()
        return
    data = await state.get_data()
    bid = await insert_booking(
        telegram_user_id=uid,
        dog_info=data.get("dog_line", ""),
        notes=data.get("notes") or "",
        photo_file_id=data.get("photo_file_id"),
        owner_info=data.get("owner") or "",
        checkin_date=data["checkin_date"],
        checkin_time=data["checkin_time"],
        checkout_date=data["checkout_date"],
        checkout_time=data["checkout_time"],
        daily_price=int(data["daily_price"]),
        location=str(data.get("location") or ""),
        services={k: True for k in (data.get("svc_selected") or set())},
        manual_services=list(data.get("svc_manual") or []),
        total_amount=int(data.get("total_amount") or 0),
        total_formula=str(data.get("total_formula") or ""),
        prepayment_amount=0,
    )
    await state.clear()
    await query.message.answer("✅ Бронь создана")
    await state.set_data({"bpay_id": bid, "bpay_total": int(data.get("total_amount") or 0)})
    await state.set_state(BookingStates.pay_offer)
    await query.message.answer("Внести предоплату сейчас?", reply_markup=_pay_offer_kb())
    await query.answer()


@router.callback_query(BookingStates.pay_offer, F.data == "bpay:no")
async def booking_pay_later(query: CallbackQuery, state: FSMContext) -> None:
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await state.clear()
    await query.answer()


@router.callback_query(BookingStates.pay_offer, F.data == "bpay:yes")
async def booking_pay_now(query: CallbackQuery, state: FSMContext) -> None:
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.message.answer("Введите сумму оплаты:")
    await state.set_state(BookingStates.pay_amount)
    await query.answer()


def _parse_money(raw: str) -> int | None:
    t = (raw or "").strip()
    if t == "":
        return None
    digits = re.sub(r"\\D", "", t)
    if digits == "":
        return None
    return int(digits)


@router.message(BookingStates.pay_amount, F.text)
async def booking_pay_amount(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id if message.from_user else 0
    paid = _parse_money(message.text or "")
    if paid is None:
        await message.answer("Введите сумму оплаты (число).")
        return
    data = await state.get_data()
    bid = int(data.get("bpay_id") or 0)
    total = int(data.get("bpay_total") or 0)
    row = await fetch_booking_by_id(bid)
    if not row or int(row.get("is_active") or 1) == 0:
        await state.clear()
        await message.answer(MAIN_MENU_CAPTION, reply_markup=main_menu_kb_for(uid))
        return
    await patch_active_booking(bid, prepayment_amount=int(paid))
    rest = max(0, total - int(paid))
    await state.clear()
    await message.answer(
        f"Оплата принята: {paid} ₽\n"
        f"К оплате: {total}-{paid}={rest} ₽",
        reply_markup=main_menu_kb_for(uid),
    )


@router.message(F.text == "📋 Список броней")
async def bookings_list(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not has_access(uid):
        await message.answer("Нет доступа.")
        return
    await state.clear()
    rows = await fetch_active_bookings()
    if not rows:
        await message.answer("Список броней пуст.")
        return
    await message.answer("📋 Список броней:", reply_markup=_booking_list_kb(rows))


@router.callback_query(F.data.startswith("bo:"))
async def booking_open(query: CallbackQuery) -> None:
    try:
        bid = int((query.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    row = await fetch_booking_by_id(bid)
    if not row or int(row.get("is_active") or 1) == 0:
        await query.answer("Не найдено.", show_alert=True)
        return
    await query.message.edit_reply_markup(reply_markup=None)
    confirmed = int(row.get("is_confirmed") or 0) == 1
    await query.message.answer(
        await _format_booking(row),
        reply_markup=_booking_card_kb(bid, confirmed=confirmed),
    )
    await query.answer()


@router.callback_query(F.data.startswith("bx:"))
async def booking_cancel(query: CallbackQuery) -> None:
    try:
        bid = int((query.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    await cancel_booking(bid)
    if query.message:
        await query.message.edit_reply_markup(reply_markup=None)
        await query.message.answer("❌ Бронь отменена.")
    await query.answer()


@router.callback_query(F.data.startswith("bcf:"))
async def booking_confirm(query: CallbackQuery) -> None:
    try:
        bid = int((query.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    row = await fetch_booking_by_id(bid)
    if not row or int(row.get("is_active") or 1) == 0:
        await query.answer("Не найдено.", show_alert=True)
        return
    await patch_active_booking(bid, is_confirmed=1)
    if query.message:
        await query.message.edit_reply_markup(
            reply_markup=_booking_card_kb(bid, confirmed=True)
        )
        await query.message.answer("✅ Бронь подтверждена.")
    await query.answer()


@router.callback_query(F.data.startswith("bci:"))
async def booking_checkin_start(query: CallbackQuery, state: FSMContext) -> None:
    try:
        bid = int((query.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    row = await fetch_booking_by_id(bid)
    if not row or int(row.get("is_active") or 1) == 0:
        await query.answer("Не найдено.", show_alert=True)
        return
    if int(row.get("is_confirmed") or 0) != 1:
        await query.answer("Сначала подтвердите бронь.", show_alert=True)
        return
    await state.clear()
    await state.set_state(BookingListStates.checkin_dates)
    await state.update_data(bcheck_id=bid)
    if query.message:
        await query.message.edit_reply_markup(reply_markup=None)
        await query.message.answer(_PROMPT_BOOKING_CHECKIN_FROM_LIST, reply_markup=remove_kb())
    await query.answer()


@router.message(BookingListStates.checkin_dates, F.text)
async def booking_checkin_dates_msg(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id if message.from_user else 0
    raw = (message.text or "").strip()
    parsed = parse_checkin_planned_block(raw)
    if not parsed:
        await message.answer(_PROMPT_BOOKING_CHECKIN_FROM_LIST)
        return
    d1, tm, d2 = parsed
    try:
        billable_days(d1, tm, d2, PLANNED_CHECKOUT_TIME)
    except ValueError as e:
        if str(e) == "checkout_before_checkin":
            await message.answer(ERR_CHECKIN_BLOCK_CHECKOUT_BEFORE)
            return
        await message.answer(_PROMPT_BOOKING_CHECKIN_FROM_LIST)
        return
    data = await state.get_data()
    bid = int(data.get("bcheck_id") or 0)
    book = await fetch_booking_by_id(bid)
    if not book or int(book.get("is_active") or 1) == 0:
        await state.clear()
        await message.answer(MAIN_MENU_CAPTION, reply_markup=main_menu_kb_for(uid))
        return
    cap_err = await validate_booking_capacity(
        d1,
        tm,
        d2,
        PLANNED_CHECKOUT_TIME,
        exclude_booking_id=bid,
    )
    if cap_err:
        await message.answer(f"{cap_err}\n\n{_PROMPT_BOOKING_CHECKIN_FROM_LIST}")
        return
    sel, manual = stay_services_from_row(book)
    svc_map = await get_services_map()
    n = billable_days(d1, tm, d2, PLANNED_CHECKOUT_TIME)
    total, formula = build_total(
        nights=n,
        daily_price=int(book.get("daily_price") or 0),
        selected_keys=sel,
        manual=manual,
        service_catalog=svc_map,
    )
    stay_id = await insert_stay(
        telegram_user_id=int(book.get("telegram_user_id") or uid),
        dog_info=str(book.get("dog_info") or ""),
        notes=str(book.get("notes") or ""),
        photo_file_id=book.get("photo_file_id"),
        owner_info=str(book.get("owner_info") or ""),
        checkin_date=d1,
        checkin_time=tm,
        checkout_date=d2,
        checkout_time=PLANNED_CHECKOUT_TIME,
        daily_price=int(book.get("daily_price") or 0),
        location=str(book.get("location") or ""),
        services={k: True for k in sel},
        manual_services=list(manual),
        total_amount=int(total),
        total_formula=str(formula),
    )
    pre = int(book.get("prepayment_amount") or 0)
    if pre > 0:
        await patch_active_stay(stay_id, payment_amount=pre)
    await cancel_booking(bid)
    await state.clear()
    await message.answer("✅ Заезд оформлен.", reply_markup=main_menu_kb_for(uid))

