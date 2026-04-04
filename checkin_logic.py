import json
import re
from datetime import datetime

_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")
_TIME_IN_TEXT_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
_DATE_IN_TEXT_RE = re.compile(
    r"(?<!\d)(\d{1,2}\.\d{1,2}\.\d{2,4})(?!\d)"
)


def stay_services_from_row(row: dict) -> tuple[set[str], list]:
    s = json.loads(row.get("services_json") or "{}")
    selected = {k for k, v in s.items() if v}
    manual = json.loads(row.get("manual_services_json") or "[]")
    if not isinstance(manual, list):
        manual = []
    return selected, manual


def dog_label(dog_info: str) -> str:
    parts = [p.strip() for p in (dog_info or "").split(",") if p.strip()]
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1]}"
    if parts:
        return parts[0]
    return (dog_info or "")[:40] or "—"


def parse_dmY(s: str) -> datetime:
    s = s.strip()
    for fmt in ("%d.%m.%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError("bad_date")


def parse_hm(s: str) -> tuple[int, int]:
    m = _TIME_RE.match(s or "")
    if not m:
        raise ValueError("bad_time")
    h, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mm <= 59):
        raise ValueError("bad_time")
    return h, mm


def normalize_time_input(s: str, *, pick_last: bool = True) -> str:
    raw = (s or "").strip()
    if not raw:
        raise ValueError("bad_time")
    if _TIME_RE.match(raw):
        parse_hm(raw)
        return raw.strip()
    found: list[str] = []
    for m in _TIME_IN_TEXT_RE.finditer(raw):
        frag = m.group(0).strip()
        try:
            parse_hm(frag)
            found.append(frag)
        except ValueError:
            continue
    if not found:
        raise ValueError("bad_time")
    return found[-1] if pick_last else found[0]


def normalize_date_input(s: str, *, pick_last: bool = False) -> str:
    raw = (s or "").strip()
    if not raw:
        raise ValueError("bad_date")
    try:
        parse_dmY(raw)
        return raw
    except ValueError:
        pass
    found: list[str] = []
    for m in _DATE_IN_TEXT_RE.finditer(raw):
        frag = m.group(1)
        try:
            parse_dmY(frag)
            found.append(frag)
        except ValueError:
            continue
    if not found:
        for part in [p.strip() for p in raw.split(",") if p.strip()]:
            try:
                parse_dmY(part)
                found.append(part)
            except ValueError:
                continue
    if not found:
        raise ValueError("bad_date")
    return found[-1] if pick_last else found[0]


PLANNED_CHECKOUT_TIME = "00:00"


def parse_checkin_planned_block(raw: str) -> tuple[str, str, str] | None:
    parts = [p.strip() for p in (raw or "").split(",")]
    if len(parts) != 3:
        return None
    d_in, t_in, d_out = parts
    if not d_in or not t_in or not d_out:
        return None
    try:
        d1 = normalize_date_input(d_in, pick_last=False)
        tm = normalize_time_input(t_in, pick_last=False)
        d2 = normalize_date_input(d_out, pick_last=False)
    except ValueError:
        return None
    return d1, tm, d2


def parse_date_time_pair(raw: str) -> tuple[str, str] | None:
    s = (raw or "").strip()
    if "," not in s:
        return None
    d_raw, t_raw = s.split(",", 1)
    d_raw, t_raw = d_raw.strip(), t_raw.strip()
    try:
        d = normalize_date_input(d_raw, pick_last=False)
        t = normalize_time_input(t_raw, pick_last=True)
    except ValueError:
        return None
    return d, t


def stay_range_datetimes(
    checkin_d: str,
    checkin_t: str,
    checkout_d: str,
    checkout_t: str,
) -> tuple[datetime, datetime]:
    a = parse_dmY(checkin_d)
    b = parse_dmY(checkout_d)
    h1, m1 = parse_hm(checkin_t)
    h2, m2 = parse_hm(checkout_t)
    start = a.replace(hour=h1, minute=m1, second=0, microsecond=0)
    end = b.replace(hour=h2, minute=m2, second=0, microsecond=0)
    return start, end


def billable_days(
    checkin_d: str,
    checkin_t: str,
    checkout_d: str,
    checkout_t: str,
) -> int:
    start, end = stay_range_datetimes(checkin_d, checkin_t, checkout_d, checkout_t)
    if end < start:
        raise ValueError("checkout_before_checkin")
    a = parse_dmY(checkin_d)
    b = parse_dmY(checkout_d)
    d_days = (b.date() - a.date()).days
    h1, m1 = parse_hm(checkin_t)
    h2, m2 = parse_hm(checkout_t)
    t1 = h1 * 60 + m1
    t2 = h2 * 60 + m2
    if t2 > t1:
        return max(1, d_days + 1)
    return max(1, d_days)


def build_total(
    *,
    nights: int,
    daily_price: int,
    selected_keys: set[str],
    manual: list[dict],
    service_catalog: dict[str, tuple[str, int]],
) -> tuple[int, str]:
    parts: list[str] = []
    acc = 0

    acc += nights * daily_price
    parts.append(f"{nights}*{daily_price}")

    for key in sorted(selected_keys):
        if key not in service_catalog:
            continue
        _name, per_day = service_catalog[key]
        add = nights * per_day
        acc += add
        parts.append(f"{nights}*{per_day}")

    for m in manual:
        amt = int(m["amount"])
        acc += amt
        parts.append(str(amt))

    formula = "+".join(parts) + f"={acc} ₽"
    return acc, formula
