from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import has_access, is_admin
from keyboards import MAIN_MENU_CAPTION, admin_main_kb, employee_main_kb

router = Router(name="common")


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id if message.from_user else 0
    if not has_access(uid):
        await message.answer("Нет доступа.")
        return
    if is_admin(uid):
        await message.answer(MAIN_MENU_CAPTION, reply_markup=admin_main_kb())
    else:
        await message.answer(MAIN_MENU_CAPTION, reply_markup=employee_main_kb())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id if message.from_user else 0
    if not has_access(uid):
        await message.answer("Нет доступа.")
        return
    if is_admin(uid):
        await message.answer(MAIN_MENU_CAPTION, reply_markup=admin_main_kb())
    else:
        await message.answer(MAIN_MENU_CAPTION, reply_markup=employee_main_kb())
