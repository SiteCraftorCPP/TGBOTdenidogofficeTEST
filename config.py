import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int((os.getenv("ADMIN_ID") or "0").strip() or 0)
TELEGRAM_REQUEST_TIMEOUT = float(
    (os.getenv("TELEGRAM_REQUEST_TIMEOUT") or "120").strip() or 120
)


def telegram_proxy_url() -> str | None:
    raw = (os.getenv("TELEGRAM_PROXY") or "").strip()
    if not raw:
        return None
    if "://" in raw:
        return raw
    parts = raw.split(":", 3)
    if len(parts) == 4:
        host, port, user, password = parts
        u = quote(user, safe="")
        p = quote(password, safe="")
        return f"socks5://{u}:{p}@{host}:{port}"
    return raw

_raw_emp = os.getenv("EMPLOYEE_IDS", "") or ""
EMPLOYEE_IDS = {
    int(x.strip())
    for x in _raw_emp.split(",")
    if x.strip().isdigit()
}


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def is_employee(user_id: int) -> bool:
    return user_id in EMPLOYEE_IDS


def has_access(user_id: int) -> bool:
    return is_admin(user_id) or is_employee(user_id)
