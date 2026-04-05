from __future__ import annotations

import re
import ssl
import urllib.request
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

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_NOTO_URL = (
    "https://raw.githubusercontent.com/notofonts/noto-fonts/main/"
    "hinted/ttf/NotoSans/NotoSans-Regular.ttf"
)


def _pdf_font_path() -> Path | None:
    import os

    candidates: list[Path] = [
        _PROJECT_ROOT / "fonts" / "NotoSans-Regular.ttf",
        _PROJECT_ROOT / "fonts" / "DejaVuSans.ttf",
    ]
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for name in ("arial.ttf", "Arial.ttf"):
        candidates.append(Path(windir) / "Fonts" / name)
    candidates.extend(
        [
            Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
            Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
        ]
    )
    for p in candidates:
        if p.is_file():
            return p
    return None


def _try_download_noto_font() -> Path | None:
    dest = _PROJECT_ROOT / "fonts" / "NotoSans-Regular.ttf"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(
            _NOTO_URL,
            headers={"User-Agent": "ddhotel-bot/1"},
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=45) as resp:
            data = resp.read()
        if len(data) < 10_000:
            return None
        dest.write_bytes(data)
        return dest
    except OSError:
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
        raise ModuleNotFoundError("pip install fpdf2") from exc

    font_file = _pdf_font_path()
    if font_file is None:
        font_file = _try_download_noto_font()
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
        "За сколько последних календарных дней отчёт?\n"
        "Число от 1 до 3650 (1 — сутки, 30 — месяц)."
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
    await message.answer("Экспорт:", reply_markup=_export_kb())


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
                "PDF: нет модуля fpdf2. На сервере: pip install fpdf2"
            )
            await query.message.answer(MAIN_MENU_CAPTION, reply_markup=admin_main_kb())
        return
    except FileNotFoundError:
        await state.clear()
        if query.message:
            await query.message.answer(
                "PDF: нет шрифта. В проекте fonts/NotoSans-Regular.ttf "
                "или на сервере: apt install fonts-noto-core"
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
