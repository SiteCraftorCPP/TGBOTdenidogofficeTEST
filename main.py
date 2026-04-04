import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from config import BOT_TOKEN, TELEGRAM_REQUEST_TIMEOUT, telegram_proxy_url
from database import init_db
from handlers import setup_routers


def _network_help() -> None:
    print(
        "\nНет соединения с api.telegram.org (блокировка или сеть).\n"
        "Включите VPN с системным прокси ИЛИ задайте в .env локальный прокси VPN:\n"
        "  TELEGRAM_PROXY=http://127.0.0.1:7890\n"
        "  TELEGRAM_PROXY=socks5://127.0.0.1:1080\n"
        "(порт возьмите из настроек Clash / v2rayN / другого клиента.)\n",
        file=sys.stderr,
    )


async def main() -> None:
    if not BOT_TOKEN:
        print("Задайте BOT_TOKEN в .env", file=sys.stderr)
        sys.exit(1)
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    await init_db()

    proxy = telegram_proxy_url()
    session = (
        AiohttpSession(proxy=proxy, timeout=TELEGRAM_REQUEST_TIMEOUT)
        if proxy
        else AiohttpSession(timeout=TELEGRAM_REQUEST_TIMEOUT)
    )
    bot = Bot(
        BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    @dp.errors()
    async def _log_errors(event: ErrorEvent) -> bool:
        logging.exception("Ошибка в обработчике: %s", event.exception)
        return True

    dp.include_router(setup_routers())
    try:
        await dp.start_polling(bot)
    except TelegramNetworkError as exc:
        logging.error("%s: %s", type(exc).__name__, exc)
        _network_help()
        sys.exit(1)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОстановлено.", flush=True)
        sys.exit(0)
