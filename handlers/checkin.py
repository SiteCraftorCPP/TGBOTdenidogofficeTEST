import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

from checkin_logic import (
    PLANNED_CHECKOUT_TIME,
    billable_days,
    build_total,
    format_dog_comma_line,
    format_dog_display,
    inline_button_text,
    parse_checkin_planned_block,
    parse_manual_service_line,
)
from config import ADMIN_IDS, EMPLOYEE_IDS, has_access
from database import (
    fetch_stay_by_id,
    get_location_row,
    get_services_map,
    get_stay_price_slot,
    insert_stay,
    list_locations_catalog,
    list_services_catalog,
    list_stay_price_slots,
    patch_active_stay,
    validate_booking_capacity,
)
from keyboards import (
    BTN_SKIP,
    ERR_CHECKIN_BLOCK_CHECKOUT_BEFORE,
    ERR_MANUAL_SERVICE_PARSE,
    MAIN_MENU_CAPTION,
    PROMPT_DT_CHECKIN_BLOCK,
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
from states import CheckInStates

router = Router(name="checkin")


def _staff_actor_label(user: User | None) -> str:
    if user is None:
        return "—"
    fn = (user.full_name or "").strip()
    if fn:
        return fn
    un = (user.username or "").strip()
    if un:
        return f"@{un.lstrip('@')}"
    return "—"


def _parse_checkin_pay(raw: str) -> int | None:
    t = (raw or "").strip()
    if t == "":
        return None
    digits = re.sub(r"\D", "", t)
    if digits == "":
        return None
    return int(digits)


def _checkin_pay_offer_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💰 Внести оплату",
                    callback_data="cin_pay:yes",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏭️ Позже",
                    callback_data="cin_pay:no",
                )
            ],
        ]
    )


_LOCATION_EMOJI: dict[str, str] = {
    "byt1": "🏠",
    "byt2": "🏠",
    "vol": "🦮",
    "ban": "🛁",
}

async def _price_ikb() -> InlineKeyboardMarkup:
    slots = await list_stay_price_slots()
    rows = [
        [
            InlineKeyboardButton(
                text=f"{s['name']} — {s['price']} ₽",
                callback_data=f"p:{int(s['id'])}",
            )
        ]
        for s in slots
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _location_button_text(row: dict) -> str:
    slug = str(row.get("slug") or "").strip()
    name = str(row.get("name") or "").strip() or "—"
    emo = _LOCATION_EMOJI.get(slug, "📍")
    return f"{emo} {name}"


async def _loc_ikb() -> InlineKeyboardMarkup:
    locs = await list_locations_catalog()
    rows = [
        [
            InlineKeyboardButton(
                text=_location_button_text(r),
                callback_data=f"l:{int(r['id'])}",
            )
        ]
        for r in locs
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _services_ask_ikb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Добавить", callback_data="sadd"),
                InlineKeyboardButton(text=BTN_SKIP, callback_data="sskip"),
            ]
        ]
    )


async def _services_pick_ikb(
    selected: set[str], manual: list[dict]
) -> InlineKeyboardMarkup:
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
                    callback_data=f"t:{slug}",
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
                    callback_data=f"mu:{i}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="➕ Добавить вручную", callback_data="manual")]
    )
    rows.append(
        [InlineKeyboardButton(text="🟦 Готово", callback_data="svcdone")]
    )
    rows.append(
        [InlineKeyboardButton(text="⏭️ Пропустить", callback_data="svcskip")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _confirm_ikb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Подтвердить", callback_data="cok"),
                InlineKeyboardButton(text="Отменить", callback_data="cbad"),
            ]
        ]
    )


def _summary_text(
    *,
    dog: str,
    notes: str,
    owner: str,
    cin_d: str,
    cin_t: str,
    cout_d: str,
    cout_t: str,
    daily: int,
    loc: str,
    selected: set[str],
    manual: list,
    formula: str,
    service_catalog: dict[str, tuple[str, int]],
    service_order: list[str],
) -> str:
    out: list[str] = ["Итог:", f" {format_dog_display(dog)}"]
    if notes.strip():
        out.append(f" {notes.strip()}")
        out.append("")
    if owner.strip():
        out.append(f" Хозяин: {owner.strip()}")
    out.append(f" Заезд: {cin_d}, {cin_t}")
    cout_t_n = (cout_t or "").strip() or PLANNED_CHECKOUT_TIME
    if cout_t_n == PLANNED_CHECKOUT_TIME:
        out.append(f" Плановая дата выезда: {cout_d}")
    else:
        out.append(f" Плановый выезд: {cout_d}, {cout_t_n}")
    out.append(f" Цена проживания за сутки: {daily} ₽")
    out.append(f" Место размещения: {loc}")

    daily_lines: list[str] = []
    for slug in service_order:
        if slug in selected and slug in service_catalog:
            title, per = service_catalog[slug]
            daily_lines.append(f"{title} - {per} ₽/день")
    manual_lines = [f"{m['name']} — {m['amount']} руб." for m in manual]

    if daily_lines:
        out.append(f" Услуги: {daily_lines[0]}")
        for rest in daily_lines[1:]:
            out.append(f" {rest}")
        for ml in manual_lines:
            out.append(f" {ml}")
    elif manual_lines:
        out.append(f" Услуги: {manual_lines[0]}")
        for rest in manual_lines[1:]:
            out.append(f" {rest}")
    else:
        out.append(" Услуги: —")

    out.append(f" Общая сумма: {formula}")
    out.append("")
    out.append("Подтвердите заезд:")
    return "\n".join(out)


def _staff_notify_body_lines(
    *,
    dog: str,
    notes: str,
    owner: str,
    cin_d: str,
    cin_t: str,
    cout_d: str,
    cout_t: str,
    daily: int,
    loc: str,
    selected: set[str],
    manual: list,
    formula: str,
    service_catalog: dict[str, tuple[str, int]],
    service_order: list[str],
) -> list[str]:
    lines: list[str] = [
        "🆕 Новый заезд оформлен",
        "",
        format_dog_display(dog),
    ]
    if notes.strip():
        lines.append(f"Примечания: {notes.strip()}")
    if owner.strip():
        lines.append(f"Хозяин: {owner.strip()}")
    lines.append(f"Заезд: {cin_d}, {cin_t}")
    cout_t_n = (cout_t or "").strip() or PLANNED_CHECKOUT_TIME
    if cout_t_n == PLANNED_CHECKOUT_TIME:
        lines.append(f"Плановая дата выезда: {cout_d}")
    else:
        lines.append(f"Плановый выезд: {cout_d}, {cout_t_n}")
    lines.append(f"Цена проживания за сутки: {daily} ₽")
    lines.append(f"Место размещения: {loc}")

    daily_lines: list[str] = []
    for slug in service_order:
        if slug in selected and slug in service_catalog:
            title, per = service_catalog[slug]
            daily_lines.append(f"{title} - {per} ₽/день")
    manual_lines = [f"{m['name']} — {m['amount']} руб." for m in manual]

    if daily_lines:
        lines.append(f"Услуги: {daily_lines[0]}")
        lines.extend(f" {x}" for x in daily_lines[1:])
        lines.extend(f" {x}" for x in manual_lines)
    elif manual_lines:
        lines.append(f"Услуги: {manual_lines[0]}")
        lines.extend(f" {x}" for x in manual_lines[1:])
    else:
        lines.append("Услуги: не указаны")

    lines.append(f"Общая сумма: {formula}")
    return lines


async def _notify_staff_new_checkin(
    bot,
    *,
    actor_id: int,
    actor_label: str,
    data: dict,
) -> None:
    selected: set[str] = set(data.get("svc_selected") or [])
    manual: list = list(data.get("svc_manual") or [])
    svc_map = await get_services_map()
    svc_order = [r["slug"] for r in await list_services_catalog()]
    body = _staff_notify_body_lines(
        dog=data.get("dog_line", ""),
        notes=(data.get("notes") or "").strip(),
        owner=(data.get("owner") or "").strip(),
        cin_d=data.get("checkin_date") or "",
        cin_t=data.get("checkin_time") or "",
        cout_d=data.get("checkout_date") or "",
        cout_t=data.get("checkout_time") or "",
        daily=int(data.get("daily_price") or 0),
        loc=str(data.get("location") or ""),
        selected=selected,
        manual=manual,
        formula=str(data.get("total_formula") or ""),
        service_catalog=svc_map,
        service_order=svc_order,
    )
    text = "\n".join([f"👤 Оформил: {actor_label}", ""] + body)
    targets = (ADMIN_IDS | EMPLOYEE_IDS) - {0}
    targets.discard(actor_id)
    photo_id = data.get("photo_file_id")
    caption_max = 1024
    for chat_id in targets:
        try:
            if photo_id:
                cap = text
                if len(cap) > caption_max:
                    cap = text[: caption_max - 30].rstrip() + "\n… (текст сокращён)"
                await bot.send_photo(chat_id, photo_id, caption=cap)
            else:
                await bot.send_message(chat_id, text, parse_mode=None)
        except Exception:
            logging.exception("Не удалось отправить уведомление о заезде пользователю %s", chat_id)


async def _notify_staff_checkin_payment(
    bot,
    *,
    actor_id: int,
    actor_label: str,
    dog_info: str,
    paid: int,
    total: int,
) -> None:
    rest = max(0, int(total) - int(paid))
    dog = format_dog_comma_line(dog_info)
    text = (
        f"Оплата: {dog}\n"
        f"Внес: {actor_label}\n"
        f"{paid} ₽\n"
        f"К оплате: {total}-{paid}={rest} ₽"
    )
    targets = (ADMIN_IDS | EMPLOYEE_IDS) - {0}
    targets.discard(actor_id)
    for chat_id in targets:
        try:
            await bot.send_message(chat_id, text, parse_mode=None)
        except Exception:
            logging.exception(
                "Не удалось отправить уведомление об оплате в боте пользователю %s",
                chat_id,
            )


async def _send_summary(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    dog = data.get("dog_line", "")
    notes = (data.get("notes") or "").strip()
    owner = (data.get("owner") or "").strip()
    photo_id = data.get("photo_file_id")
    cin_d = data["checkin_date"]
    cin_t = data["checkin_time"]
    cout_d = data["checkout_date"]
    cout_t = data["checkout_time"]
    daily = int(data["daily_price"])
    loc = data["location"]
    selected: set[str] = set(data.get("svc_selected") or [])
    manual: list = list(data.get("svc_manual") or [])
    svc_map = await get_services_map()
    svc_order = [r["slug"] for r in await list_services_catalog()]

    n = billable_days(cin_d, cin_t, cout_d, cout_t)
    total, formula = build_total(
        nights=n,
        daily_price=daily,
        selected_keys=selected,
        manual=manual,
        service_catalog=svc_map,
    )
    await state.update_data(nights=n, total_amount=total, total_formula=formula)

    full = _summary_text(
        dog=dog,
        notes=notes,
        owner=owner,
        cin_d=cin_d,
        cin_t=cin_t,
        cout_d=cout_d,
        cout_t=cout_t,
        daily=daily,
        loc=loc,
        selected=selected,
        manual=manual,
        formula=formula,
        service_catalog=svc_map,
        service_order=svc_order,
    )

    if photo_id and len(full) < 900:
        await message.answer_photo(
            photo_id,
            caption=full,
            reply_markup=_confirm_ikb(),
        )
    else:
        if photo_id:
            await message.answer_photo(photo_id)
        await message.answer(full, reply_markup=_confirm_ikb())

    await state.set_state(CheckInStates.confirm)


@router.message(F.text == "➕ Заезд собаки")
async def checkin_entry(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not has_access(uid):
        await message.answer("Нет доступа.")
        return
    await state.clear()
    await state.set_state(CheckInStates.dog_line)
    await message.answer(
        "Введите: породу, кличку собаки, возраст (через запятую)\n"
        " пример: Пудель, Макс, 3 года",
        reply_markup=remove_kb(),
    )


@router.message(CheckInStates.dog_line, F.text)
async def checkin_dog_line(message: Message, state: FSMContext) -> None:
    line = (message.text or "").strip()
    if not line:
        await message.answer(
            "Введите: породу, кличку собаки, возраст (через запятую)\n"
            " пример: Пудель, Макс, 3 года"
        )
        return
    await state.update_data(dog_line=line)
    await state.set_state(CheckInStates.notes)
    await send_notes_prompt_step(message, SKIP_CB_CHECKIN_NOTES)


def _prompt_photo_with_skip() -> tuple[str, InlineKeyboardMarkup]:
    return (
        "Отправьте фото собаки\n или нажмите «пропустить»",
        skip_inline_kb(SKIP_CB_CHECKIN_PHOTO),
    )


def _prompt_owner_with_skip() -> tuple[str, InlineKeyboardMarkup]:
    return (
        "Введите: имя хозяина, контакты (через запятую)\n"
        " пример: Мария, +79001234567\n"
        "или нажмите «пропустить»",
        skip_inline_kb(SKIP_CB_CHECKIN_OWNER),
    )


async def _answer_photo_step(message: Message, state: FSMContext) -> None:
    await state.set_state(CheckInStates.photo)
    txt, ikb = _prompt_photo_with_skip()
    await message.answer(txt, reply_markup=ikb)


@router.callback_query(CheckInStates.notes, F.data == SKIP_CB_CHECKIN_NOTES)
async def checkin_skip_notes_cb(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await state.update_data(notes="")
    await _answer_photo_step(query.message, state)


@router.message(CheckInStates.notes, F.text)
async def checkin_notes(message: Message, state: FSMContext) -> None:
    t = (message.text or "").strip()
    if t == BTN_SKIP:
        await state.update_data(notes="")
    else:
        await state.update_data(notes=t)
    await _answer_photo_step(message, state)


@router.callback_query(CheckInStates.photo, F.data == SKIP_CB_CHECKIN_PHOTO)
async def checkin_skip_photo_cb(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await state.update_data(photo_file_id=None)
    await state.set_state(CheckInStates.owner)
    txt, ikb = _prompt_owner_with_skip()
    await query.message.answer(txt, reply_markup=ikb)


@router.message(CheckInStates.photo, F.text)
async def checkin_photo_wrong_text(message: Message, state: FSMContext) -> None:
    if (message.text or "").strip() == BTN_SKIP:
        await state.update_data(photo_file_id=None)
        await state.set_state(CheckInStates.owner)
        txt, ikb = _prompt_owner_with_skip()
        await message.answer(txt, reply_markup=ikb)
        return
    txt, ikb = _prompt_photo_with_skip()
    await message.answer(txt, reply_markup=ikb)


@router.message(CheckInStates.photo, F.photo)
async def checkin_photo_file(message: Message, state: FSMContext) -> None:
    if not message.photo:
        return
    fid = message.photo[-1].file_id
    await state.update_data(photo_file_id=fid)
    await state.set_state(CheckInStates.owner)
    txt, ikb = _prompt_owner_with_skip()
    await message.answer(txt, reply_markup=ikb)


@router.message(CheckInStates.photo)
async def checkin_photo_other(message: Message) -> None:
    txt, ikb = _prompt_photo_with_skip()
    await message.answer(txt, reply_markup=ikb)


@router.callback_query(CheckInStates.owner, F.data == SKIP_CB_CHECKIN_OWNER)
async def checkin_skip_owner_cb(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await state.update_data(owner="")
    await state.set_state(CheckInStates.dates)
    await query.message.answer(PROMPT_DT_CHECKIN_BLOCK, reply_markup=remove_kb())


@router.message(CheckInStates.owner, F.text)
async def checkin_owner(message: Message, state: FSMContext) -> None:
    t = (message.text or "").strip()
    if t == BTN_SKIP:
        await state.update_data(owner="")
    else:
        await state.update_data(owner=t)
    await state.set_state(CheckInStates.dates)
    await message.answer(PROMPT_DT_CHECKIN_BLOCK, reply_markup=remove_kb())


@router.message(CheckInStates.dates, F.text)
async def checkin_dates(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    parsed = parse_checkin_planned_block(raw)
    if not parsed:
        await message.answer(PROMPT_DT_CHECKIN_BLOCK)
        return
    d1, tm, d2 = parsed
    try:
        billable_days(d1, tm, d2, PLANNED_CHECKOUT_TIME)
    except ValueError as e:
        if str(e) == "checkout_before_checkin":
            await message.answer(ERR_CHECKIN_BLOCK_CHECKOUT_BEFORE)
            return
        await message.answer(PROMPT_DT_CHECKIN_BLOCK)
        return
    cap_err = await validate_booking_capacity(
        d1,
        tm,
        d2,
        PLANNED_CHECKOUT_TIME,
    )
    if cap_err:
        await message.answer(f"{cap_err}\n\n{PROMPT_DT_CHECKIN_BLOCK}")
        return
    await state.update_data(
        checkin_date=d1,
        checkin_time=tm,
        checkout_date=d2,
        checkout_time=PLANNED_CHECKOUT_TIME,
    )
    await state.set_state(CheckInStates.price)
    ikb = await _price_ikb()
    if not ikb.inline_keyboard:
        await message.answer("Нет тарифов проживания. Добавьте в настройках.")
        return
    await message.answer("Выберите цену проживания за сутки:", reply_markup=ikb)


@router.callback_query(CheckInStates.price, F.data.startswith("p:"))
async def checkin_price_cb(query: CallbackQuery, state: FSMContext) -> None:
    sid = int(query.data.split(":", 1)[1])
    row = await get_stay_price_slot(sid)
    if not row:
        await query.answer()
        return
    await state.update_data(daily_price=int(row["price"]))
    await state.set_state(CheckInStates.location)
    await query.message.edit_reply_markup(reply_markup=None)
    price_line = f"{row['name']} — {int(row['price'])} ₽"
    await query.message.answer(price_line)
    loc_kb = await _loc_ikb()
    if not loc_kb.inline_keyboard:
        await query.message.answer("Нет мест размещения. Добавьте в настройках.")
        await query.answer()
        return
    await query.message.answer(
        "🏠 Выберите место размещения:", reply_markup=loc_kb
    )
    await query.answer()


@router.callback_query(CheckInStates.location, F.data.startswith("l:"))
async def checkin_loc_cb(query: CallbackQuery, state: FSMContext) -> None:
    lid = int(query.data.split(":", 1)[1])
    loc_row = await get_location_row(lid)
    if not loc_row:
        await query.answer()
        return
    loc = loc_row["name"]
    await state.update_data(location=loc)
    await state.set_state(CheckInStates.services_ask)
    await query.message.edit_reply_markup(reply_markup=None)
    await query.message.answer(_location_button_text(loc_row))
    await query.message.answer(
        "Добавить услуги сейчас?",
        reply_markup=_services_ask_ikb(),
    )
    await query.answer()


@router.callback_query(CheckInStates.services_ask, F.data.in_({"sadd", "sskip"}))
async def checkin_svc_ask_cb(query: CallbackQuery, state: FSMContext) -> None:
    await query.message.edit_reply_markup(reply_markup=None)
    if query.data == "sskip":
        await state.update_data(svc_selected=set(), svc_manual=[])
        await _send_summary(query.message, state)
        await query.answer()
        return
    await state.update_data(svc_selected=set(), svc_manual=[])
    await state.set_state(CheckInStates.services_pick)
    await query.message.answer(
        SERVICES_INLINE_CAPTION,
        reply_markup=await _services_pick_ikb(set(), []),
    )
    await query.answer()


@router.callback_query(CheckInStates.services_pick, F.data.startswith("t:"))
async def checkin_toggle_svc(query: CallbackQuery, state: FSMContext) -> None:
    key = query.data.split(":", 1)[1]
    sm = await get_services_map()
    if key not in sm:
        await query.answer()
        return
    data = await state.get_data()
    sel = set(data.get("svc_selected") or [])
    manual = list(data.get("svc_manual") or [])
    if key in sel:
        sel.remove(key)
    else:
        sel.add(key)
    await state.update_data(svc_selected=sel)
    await query.message.edit_reply_markup(
        reply_markup=await _services_pick_ikb(sel, manual)
    )
    await query.answer()


@router.callback_query(CheckInStates.services_pick, F.data == "manual")
async def checkin_manual_enter(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CheckInStates.manual_name)
    await query.message.answer(
        PROMPT_MANUAL_SERVICE_LINE,
        reply_markup=remove_kb(),
    )
    await query.answer()


@router.callback_query(CheckInStates.services_pick, F.data.startswith("mu:"))
async def checkin_manual_row_remove(query: CallbackQuery, state: FSMContext) -> None:
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
    await query.message.edit_reply_markup(
        reply_markup=await _services_pick_ikb(sel, manual)
    )
    await query.answer()


@router.callback_query(CheckInStates.services_pick, F.data == "svcdone")
async def checkin_svc_done_to_summary(query: CallbackQuery, state: FSMContext) -> None:
    await query.message.edit_reply_markup(reply_markup=None)
    await _send_summary(query.message, state)
    await query.answer()


@router.callback_query(CheckInStates.services_pick, F.data == "svcskip")
async def checkin_svc_skip_to_summary(query: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(svc_selected=set(), svc_manual=[])
    await query.message.edit_reply_markup(reply_markup=None)
    await _send_summary(query.message, state)
    await query.answer()


@router.message(CheckInStates.manual_name, F.text)
async def checkin_manual_line(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    parsed = parse_manual_service_line(raw)
    if not parsed:
        await message.answer(
            f"{ERR_MANUAL_SERVICE_PARSE}\n\n{PROMPT_MANUAL_SERVICE_LINE}"
        )
        return
    name, amt = parsed
    data = await state.get_data()
    manual = list(data.get("svc_manual") or [])
    manual.append({"name": name, "amount": amt})
    await state.update_data(svc_manual=manual)
    sel = set(data.get("svc_selected") or [])
    await state.set_state(CheckInStates.services_pick)
    await message.answer(
        SERVICES_INLINE_CAPTION,
        reply_markup=await _services_pick_ikb(sel, manual),
    )


@router.callback_query(CheckInStates.confirm, F.data.in_({"cok", "cbad"}))
async def checkin_confirm_cb(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    await query.message.edit_reply_markup(reply_markup=None)
    if query.data == "cbad":
        await state.clear()
        await query.message.answer(MAIN_MENU_CAPTION, reply_markup=main_menu_kb_for(uid))
        await query.answer()
        return

    data = await state.get_data()
    cap_err = await validate_booking_capacity(
        data["checkin_date"],
        data["checkin_time"],
        data["checkout_date"],
        data["checkout_time"],
    )
    if cap_err:
        await query.message.answer(cap_err)
        await query.answer()
        return

    stay_id = await insert_stay(
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
        location=data["location"],
        services={k: True for k in (data.get("svc_selected") or set())},
        manual_services=list(data.get("svc_manual") or []),
        total_amount=int(data.get("total_amount", 0)),
        total_formula=data.get("total_formula", ""),
    )
    await _notify_staff_new_checkin(
        query.bot,
        actor_id=uid,
        actor_label=_staff_actor_label(query.from_user),
        data=data,
    )
    await query.message.answer("Заезд оформлен", reply_markup=main_menu_kb_for(uid))
    await state.set_data(
        {
            "pay_stay_id": stay_id,
            "pay_total": int(data.get("total_amount") or 0),
        }
    )
    await state.set_state(CheckInStates.pay_offer)
    await query.message.answer(
        "Внести оплату сейчас?",
        reply_markup=_checkin_pay_offer_kb(),
    )
    await query.answer()


@router.callback_query(CheckInStates.pay_offer, F.data == "cin_pay:no")
async def checkin_pay_later_cb(query: CallbackQuery, state: FSMContext) -> None:
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await state.clear()
    await query.answer()


@router.callback_query(CheckInStates.pay_offer, F.data == "cin_pay:yes")
async def checkin_pay_now_cb(query: CallbackQuery, state: FSMContext) -> None:
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await state.set_state(CheckInStates.pay_checkin_amount)
    if query.message:
        await query.message.answer("Введите сумму оплаты:")
    await query.answer()


@router.message(CheckInStates.pay_checkin_amount, F.text)
async def checkin_pay_amount_msg(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id if message.from_user else 0
    paid = _parse_checkin_pay(message.text or "")
    if paid is None:
        await message.answer("Введите сумму оплаты (число).")
        return
    data = await state.get_data()
    sid = int(data.get("pay_stay_id") or 0)
    total = int(data.get("pay_total") or 0)
    row = await fetch_stay_by_id(sid)
    if (
        not row
        or int(row.get("telegram_user_id") or 0) != uid
        or int(row.get("is_active") or 1) == 0
    ):
        await state.clear()
        await message.answer(
            MAIN_MENU_CAPTION,
            reply_markup=main_menu_kb_for(uid),
        )
        return
    await patch_active_stay(sid, payment_amount=paid)
    rest = max(0, total - paid)
    await _notify_staff_checkin_payment(
        message.bot,
        actor_id=uid,
        actor_label=_staff_actor_label(message.from_user),
        dog_info=str(row.get("dog_info") or ""),
        paid=paid,
        total=total,
    )
    await state.clear()
    await message.answer(
        f"Оплата принята: {paid} ₽\n"
        f"К оплате: {total}-{paid}={rest} ₽",
        reply_markup=main_menu_kb_for(uid),
    )
