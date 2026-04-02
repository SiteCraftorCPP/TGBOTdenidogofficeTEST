from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import is_admin
from database import finance_metrics_for_last_days
from keyboards import MAIN_MENU_CAPTION, admin_main_kb
from states import FinanceStates

router = Router(name="financial_report")


def _pdf_font_path() -> Path | None:
    import os

    windir = os.environ.get("WINDIR", r"C:\Windows")
    for name in ("arial.ttf", "Arial.ttf"):
        p = Path(windir) / "Fonts" / name
        if p.is_file():
            return p
    local = Path(__file__).resolve().parent.parent / "fonts" / "DejaVuSans.ttf"
    if local.is_file():
        return local
    return None


def _pdf_lines(m: dict) -> list[str]:
    ps: date = m["period_start"]
    pe: date = m["period_end"]
    d = int(m["days"])
    return [
        "Финансовый отчёт",
        f"Период: с {ps.strftime('%d.%m.%Y')} по {pe.strftime('%d.%m.%Y')} ({d} дн.)",
        "",
        f" Проживание: {m['lodging_total']} ₽",
        f" Доп. услуги: {m['extras_total']} ₽",
    ]


async def build_finance_pdf_async(days: int) -> bytes:
    try:
        from fpdf import FPDF
        from fpdf.enums import XPos, YPos
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Установите пакет fpdf2 (даёт модуль fpdf): python -m pip install fpdf2"
        ) from exc

    font_file = _pdf_font_path()
    if font_file is None:
        raise FileNotFoundError("pdf_font")

    m = await finance_metrics_for_last_days(days)
    text_lines = _pdf_lines(m)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.set_left_margin(12)
    pdf.set_right_margin(12)
    pdf.add_page()
    pdf.add_font("DocFont", "", str(font_file))
    pdf.set_font("DocFont", size=11)
    w = pdf.epw
    for line in text_lines:
        txt = line if line.strip() else " "
        pdf.multi_cell(
            w,
            6,
            txt,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
    out = pdf.output()
    if isinstance(out, (bytes, bytearray)):
        return bytes(out)
    return str(out).encode("utf-8", errors="replace")


def _export_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Экспорт отчета в pdf", callback_data="finpdf")],
        ]
    )


def _parse_days(raw: str) -> int | None:
    m = re.match(r"^\s*(\d{1,4})\s*$", raw or "")
    if not m:
        return None
    n = int(m.group(1))
    if 1 <= n <= 3650:
        return n
    return None


@router.message(F.text == "💰 Финансовый отчет")
async def finance_entry(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not is_admin(uid):
        await message.answer("Нет доступа.")
        return
    await state.clear()
    await state.set_state(FinanceStates.wait_days)
    await message.answer(
        "За сколько последних календарных дней сделать отчёт?\n"
        "Например: 1 — как за день, 30 — примерно за месяц."
    )


@router.message(FinanceStates.wait_days, F.text)
async def finance_days(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not is_admin(uid):
        await state.clear()
        await message.answer("Нет доступа.")
        return
    n = _parse_days(message.text or "")
    if n is None:
        await message.answer("Введите целое число дней от 1 до 3650.")
        return
    await state.update_data(report_days=n)
    await state.set_state(FinanceStates.export_ready)
    await message.answer("Готово. Экспорт:", reply_markup=_export_kb())


@router.callback_query(FinanceStates.export_ready, F.data == "finpdf")
async def finance_export_pdf(query: CallbackQuery, state: FSMContext) -> None:
    uid = query.from_user.id if query.from_user else 0
    if not is_admin(uid):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await query.answer()
    data = await state.get_data()
    days = int(data.get("report_days") or 30)
    try:
        pdf_bytes = await build_finance_pdf_async(days)
    except ModuleNotFoundError:
        await state.clear()
        if query.message:
            await query.message.answer(
                "Не установлен модуль для PDF.\n"
                "Выполните в терминале (тем же Python, что запускает бота):\n"
                "python -m pip install fpdf2\n"
                "или: python -m pip install -r requirements.txt"
            )
            await query.message.answer(MAIN_MENU_CAPTION, reply_markup=admin_main_kb())
        return
    except FileNotFoundError:
        await state.clear()
        if query.message:
            await query.message.answer(
                "Для PDF не найден шрифт: нужен arial.ttf в Windows\\Fonts "
                "или файл fonts/DejaVuSans.ttf рядом с проектом."
            )
            await query.message.answer(MAIN_MENU_CAPTION, reply_markup=admin_main_kb())
        return
    await state.clear()
    fname = f"finance_{date.today().strftime('%Y%m%d')}.pdf"
    if query.message:
        await query.message.answer_document(
            BufferedInputFile(pdf_bytes, filename=fname),
            caption=f"Отчёт за последние {days} дн.",
        )
        await query.message.answer(MAIN_MENU_CAPTION, reply_markup=admin_main_kb())
