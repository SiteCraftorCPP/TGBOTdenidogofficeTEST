import json
import re
from collections import Counter
from datetime import date, datetime, time, timedelta

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


def format_dog_comma_line(dog_info: str) -> str:
    parts = [p.strip() for p in (dog_info or "").split(",") if p.strip()]
    return ", ".join(parts) if parts else "—"


def format_dog_display(dog_info: str) -> str:
    parts = [p.strip() for p in (dog_info or "").split(",") if p.strip()]
    if len(parts) >= 2:
        breed, name = parts[0], parts[1]
        tail = ", ".join(parts[2:]) if len(parts) > 2 else ""
        core = f"{breed} {name}"
        if tail:
            return f"Питомец: {core}, {tail}"
        return f"Питомец: {core}"
    if parts:
        return f"Питомец: {parts[0]}"
    return "Питомец: —"


def parse_manual_service_line(raw: str) -> tuple[str, int] | None:
    s = (raw or "").strip()
    if "," not in s:
        return None
    left, right = s.rsplit(",", 1)
    name = left.strip()
    amt_raw = right.strip().replace(" ", "").replace("\xa0", "")
    if not name or not amt_raw.isdigit():
        return None
    amount = int(amt_raw)
    if amount < 0 or amount > 999_999_999:
        return None
    return name, amount


def inline_button_text(s: str, max_len: int = 64) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


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


def occupancy_calendar_dates(
    checkin_d: str,
    checkin_t: str,
    checkout_d: str,
    checkout_t: str,
) -> set[date]:
    start, end = stay_range_datetimes(checkin_d, checkin_t, checkout_d, checkout_t)
    if end <= start:
        return set()
    days: set[date] = set()
    cur = start.date()
    for _ in range(4000):
        day_start = datetime.combine(cur, time.min)
        next_midnight = datetime.combine(cur + timedelta(days=1), time.min)
        if start < next_midnight and end > day_start:
            days.add(cur)
        if next_midnight >= end:
            break
        cur += timedelta(days=1)
    return days


def count_stays_per_calendar_day(
    stays: list[dict],
    *,
    exclude_stay_id: int | None = None,
) -> Counter:
    c: Counter = Counter()
    for row in stays:
        sid = int(row.get("id") or 0)
        if exclude_stay_id is not None and sid == exclude_stay_id:
            continue
        d_in = (row.get("checkin_date") or "").strip()
        t_in = (row.get("checkin_time") or "").strip() or "00:00"
        d_out = (row.get("checkout_date") or "").strip()
        t_out = (row.get("checkout_time") or "").strip() or PLANNED_CHECKOUT_TIME
        if not d_in or not d_out:
            continue
        try:
            for cd in occupancy_calendar_dates(d_in, t_in, d_out, t_out):
                c[cd] += 1
        except ValueError:
            continue
    return c


def first_capacity_overflow_day(
    *,
    capacity: int,
    occupancy: Counter,
    checkin_d: str,
    checkin_t: str,
    checkout_d: str,
    checkout_t: str,
) -> date | None:
    if capacity <= 0:
        return None
    try:
        new_days = occupancy_calendar_dates(
            checkin_d, checkin_t, checkout_d, checkout_t
        )
    except ValueError:
        return None
    for d in sorted(new_days):
        if occupancy[d] + 1 > capacity:
            return d
    return None


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


def stay_prepayment_lines(paid: int, total: int) -> list[str]:
    p = int(paid or 0)
    t = int(total or 0)
    rest = max(0, t - p)
    return [
        f"Оплачено в боте: {p} ₽",
        f"Остаток: {rest} ₽",
    ]
