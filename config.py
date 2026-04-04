import logging
import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

_raw_admins = (os.getenv("ADMIN_IDS") or "").strip()


def _parse_id_csv(raw: str) -> set[int]:
    return {
        int(x.strip())
        for x in (raw or "").split(",")
        if x.strip().isdigit()
    }

ADMIN_IDS: set[int] = _parse_id_csv(_raw_admins)

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
EMPLOYEE_IDS: set[int] = _parse_id_csv(_raw_emp)


def sync_access_ids(admins: set[int], employees: set[int]) -> None:
    ADMIN_IDS.clear()
    ADMIN_IDS.update(admins)
    EMPLOYEE_IDS.clear()
    EMPLOYEE_IDS.update(employees)


def _format_id_csv(ids: set[int]) -> str:
    return ",".join(str(x) for x in sorted(ids))


def _line_matches_env_key(stripped: str, key: str) -> bool:
    return stripped.startswith(f"{key}=") or stripped.startswith(f"export {key}=")


def write_access_ids_to_env(admins: set[int], employees: set[int]) -> None:
    if not _ENV_PATH.is_file():
        logging.warning(".env не найден, ID в файл не записаны.")
        return
    admin_csv = _format_id_csv(admins)
    emp_csv = _format_id_csv(employees)
    try:
        text = _ENV_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logging.warning("Не удалось прочитать .env: %s", e)
        return
    lines = text.splitlines()
    out: list[str] = []
    admin_written = False
    emp_written = False
    for line in lines:
        st = line.strip()
        if _line_matches_env_key(st, "ADMIN_IDS"):
            if not admin_written:
                out.append(f"ADMIN_IDS={admin_csv}")
                admin_written = True
            continue
        if _line_matches_env_key(st, "EMPLOYEE_IDS"):
            if not emp_written:
                out.append(f"EMPLOYEE_IDS={emp_csv}")
                emp_written = True
            continue
        out.append(line)
    if not admin_written:
        out.append(f"ADMIN_IDS={admin_csv}")
    if not emp_written:
        out.append(f"EMPLOYEE_IDS={emp_csv}")
    new_text = "\n".join(out)
    if not new_text.endswith("\n"):
        new_text += "\n"
    try:
        _ENV_PATH.write_text(new_text, encoding="utf-8", newline="\n")
    except OSError as e:
        logging.warning("Не удалось записать .env: %s", e)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_employee(user_id: int) -> bool:
    return user_id in EMPLOYEE_IDS


def has_access(user_id: int) -> bool:
    return is_admin(user_id) or is_employee(user_id)
