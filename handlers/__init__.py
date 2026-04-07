from aiogram import Router

from . import (
    bookings,
    checkin,
    checkout,
    common,
    current_dogs,
    debtors,
    financial_report,
    settings,
)


def setup_routers() -> Router:
    root = Router()
    root.include_router(common.router)
    root.include_router(bookings.router)
    root.include_router(checkin.router)
    root.include_router(checkout.router)
    root.include_router(current_dogs.router)
    root.include_router(debtors.router)
    root.include_router(financial_report.router)
    root.include_router(settings.router)
    return root
