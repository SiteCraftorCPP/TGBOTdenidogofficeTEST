from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from checkin_logic import parse_dmY
from config import is_admin
from database import fetch_completed_stays_for_report, fetch_open_debtors
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


def _stay_out_date(row: dict) -> date | None:
    raw = (row.get("actual_out_date") or "").strip()
    if not raw:
        return None
    try:
        return parse_dmY(raw).date()
    except ValueError:
        return None


def _filter_by_last_days(rows: list[dict], days: int) -> list[dict]:
    n = max(1, min(int(days), 3650))
    end = date.today()
    start = end - timedelta(days=n - 1)
    out: list[dict] = []
    for r in rows:
        d = _stay_out_date(r)
        if d is not None and start <= d <= end:
            out.append(r)
    out.sort(key=lambda x: _stay_out_date(x) or date.min, reverse=True)
    return out


def _build_pdf_lines(completed: list[dict], debtors: list[dict], days: int) -> list[str]:
    end = date.today()
    start = end - timedelta(days=max(1, min(days, 3650)) - 1)
    lines = [
        f"Отчёт за период с {start.strftime('%d.%m.%Y')} по {end.strftime('%d.%m.%Y')} ({days} дн.)",
        "",
        "Выезды за период:",
    ]
    if not completed:
        lines.append("— нет записей —")
    else:
        for r in completed:
            dog = (r.get("dog_info") or "—").strip()
            owner = (r.get("owner_info") or "").strip()
            od = r.get("actual_out_date") or ""
            ot = r.get("actual_out_time") or ""
            total = int(r.get("checkout_final_total") or 0)
            paid = int(r.get("payment_amount") or 0)
            oo = f", {owner}" if owner else ""
            lines.append(f"• {dog}{oo}, выезд {od} {ot}, итого {total} ₽, оплачено {paid} ₽")
    lines.append("")
    lines.append("Текущие должники:")
    if not debtors:
        lines.append("— нет —")
    else:
        for d in debtors:
            dog = (d.get("dog_info") or "—").strip()
            owner = (d.get("owner_info") or "").strip()
            owed = int(d.get("amount_owed") or 0)
            oo = f", {owner}" if owner else ""
            lines.append(f"• {dog}{oo}, долг {owed} ₽")
    return lines


async def build_finance_pdf_async(days: int) -> bytes:
    try:
        from fpdf import FPDF
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Установите пакет fpdf2 (даёт модуль fpdf): python -m pip install fpdf2"
        ) from exc

    font_file = _pdf_font_path()
    if font_file is None:
        raise FileNotFoundError("pdf_font")

    completed = _filter_by_last_days(await fetch_completed_stays_for_report(), days)
    debtors = await fetch_open_debtors()
    text_lines = _build_pdf_lines(completed, debtors, days)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.add_page()
    pdf.add_font("DocFont", "", str(font_file))
    pdf.set_font("DocFont", size=10)
    w = pdf.epw
    for line in text_lines:
        s = line if line.strip() else " "
        pdf.multi_cell(w, 6, s)
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
    await message.answer("Введите за какое последнее кол-во дней нужен отчет")


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
