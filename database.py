import json
import aiosqlite
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "bot.db"

_STAY_PATCHABLE = frozenset(
    {
        "dog_info",
        "notes",
        "photo_file_id",
        "owner_info",
        "checkin_date",
        "checkin_time",
        "checkout_date",
        "checkout_time",
        "daily_price",
        "location",
        "services_json",
        "manual_services_json",
        "total_amount",
        "total_formula",
    }
)


async def _migrate(db: aiosqlite.Connection) -> None:
    cur = await db.execute("PRAGMA table_info(stays)")
    rows = await cur.fetchall()
    names = {r[1] for r in rows}
    alters = [
        ("is_active", "INTEGER NOT NULL DEFAULT 1"),
        ("actual_out_date", "TEXT"),
        ("actual_out_time", "TEXT"),
        ("payment_amount", "INTEGER"),
        ("checkout_final_total", "INTEGER"),
        ("checkout_final_formula", "TEXT"),
        ("checkout_time", "TEXT"),
    ]
    for col, typ in alters:
        if col not in names:
            await db.execute(f"ALTER TABLE stays ADD COLUMN {col} {typ}")
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS debtors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stay_id INTEGER NOT NULL,
            owner_info TEXT NOT NULL,
            amount_owed INTEGER NOT NULL
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS stay_price_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_number INTEGER NOT NULL,
            name TEXT NOT NULL,
            price INTEGER NOT NULL
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS services_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            price_per_day INTEGER NOT NULL
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS locations_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL
        )
        """
    )


async def _seed_settings_data(db: aiosqlite.Connection) -> None:
    cur = await db.execute("SELECT COUNT(*) FROM stay_price_slots")
    if (await cur.fetchone())[0] == 0:
        prices = [
            (1, 1000),
            (2, 1100),
            (3, 1200),
            (4, 1300),
            (5, 1400),
            (6, 1500),
            (7, 1800),
            (8, 2200),
            (9, 3000),
            (10, 3500),
        ]
        for sn, pr in prices:
            await db.execute(
                "INSERT INTO stay_price_slots (slot_number, name, price) VALUES (?, ?, ?)",
                (sn, f"Цена {sn}", pr),
            )
    cur = await db.execute("SELECT COUNT(*) FROM services_catalog")
    if (await cur.fetchone())[0] == 0:
        for slug, name, pr in (
            ("training", "Дрессировка", 1500),
            ("report", "Отчет о собаке", 500),
            ("walk", "Доп. прогулка", 750),
        ):
            await db.execute(
                """
                INSERT INTO services_catalog (slug, name, price_per_day)
                VALUES (?, ?, ?)
                """,
                (slug, name, pr),
            )
    cur = await db.execute("SELECT COUNT(*) FROM locations_catalog")
    if (await cur.fetchone())[0] == 0:
        for slug, name in (
            ("byt1", "Бытовка 1"),
            ("byt2", "Бытовка 2"),
            ("vol", "Вольеры"),
            ("ban", "Баня"),
        ):
            await db.execute(
                "INSERT INTO locations_catalog (slug, name) VALUES (?, ?)",
                (slug, name),
            )


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS stays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                dog_info TEXT,
                notes TEXT,
                photo_file_id TEXT,
                owner_info TEXT,
                checkin_date TEXT,
                checkin_time TEXT,
                checkout_date TEXT,
                checkout_time TEXT,
                daily_price INTEGER,
                location TEXT,
                services_json TEXT,
                manual_services_json TEXT,
                total_amount INTEGER,
                total_formula TEXT
            )
            """
        )
        await _migrate(db)
        await _seed_settings_data(db)
        await db.commit()


async def insert_stay(
    *,
    telegram_user_id: int,
    dog_info: str,
    notes: str,
    photo_file_id: str | None,
    owner_info: str,
    checkin_date: str,
    checkin_time: str,
    checkout_date: str,
    checkout_time: str,
    daily_price: int,
    location: str,
    services: dict,
    manual_services: list,
    total_amount: int,
    total_formula: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO stays (
                telegram_user_id, dog_info, notes, photo_file_id, owner_info,
                checkin_date, checkin_time, checkout_date, checkout_time, daily_price, location,
                services_json, manual_services_json, total_amount, total_formula,
                is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                telegram_user_id,
                dog_info,
                notes,
                photo_file_id,
                owner_info,
                checkin_date,
                checkin_time,
                checkout_date,
                checkout_time,
                daily_price,
                location,
                json.dumps(services, ensure_ascii=False),
                json.dumps(manual_services, ensure_ascii=False),
                total_amount,
                total_formula,
            ),
        )
        await db.commit()
        return int(cur.lastrowid)


async def patch_active_stay(stay_id: int, **kwargs) -> bool:
    cols: dict[str, object] = {}
    for k, v in kwargs.items():
        if k not in _STAY_PATCHABLE:
            continue
        if k in ("services_json", "manual_services_json"):
            if isinstance(v, (dict, list)):
                cols[k] = json.dumps(v, ensure_ascii=False)
            else:
                cols[k] = v
        else:
            cols[k] = v
    if not cols:
        return False
    sets = ", ".join(f'"{key}" = ?' for key in cols.keys())
    sql = f"""
        UPDATE stays SET {sets}
        WHERE id = ? AND COALESCE(is_active, 1) = 1
    """
    vals = list(cols.values()) + [stay_id]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(sql, vals)
        await db.commit()
        return cur.rowcount > 0


async def fetch_active_stays() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM stays WHERE COALESCE(is_active, 1) = 1 ORDER BY id DESC"
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def fetch_stay_by_id(stay_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM stays WHERE id = ?", (stay_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def complete_checkout(
    *,
    stay_id: int,
    actual_out_date: str,
    actual_out_time: str,
    paid: int,
    final_total: int,
    final_formula: str,
) -> int | None:
    balance = max(0, final_total - paid)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE stays SET
                is_active = 0,
                actual_out_date = ?,
                actual_out_time = ?,
                payment_amount = ?,
                checkout_final_total = ?,
                checkout_final_formula = ?
            WHERE id = ? AND COALESCE(is_active, 1) = 1
            """,
            (
                actual_out_date,
                actual_out_time,
                paid,
                final_total,
                final_formula,
                stay_id,
            ),
        )
        if cur.rowcount == 0:
            await db.commit()
            return None
        cur2 = await db.execute("SELECT owner_info FROM stays WHERE id = ?", (stay_id,))
        row = await cur2.fetchone()
        owner = (row[0] or "") if row else ""
        if balance > 0:
            await db.execute(
                """
                INSERT INTO debtors (stay_id, owner_info, amount_owed)
                VALUES (?, ?, ?)
                """,
                (stay_id, owner, balance),
            )
        await db.commit()
    return balance


async def fetch_open_debtors() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT d.id AS debtor_id, d.amount_owed,
                   s.dog_info, s.notes, s.owner_info,
                   s.checkin_date, s.checkin_time, s.checkout_date,
                   s.daily_price, s.location,
                   s.services_json, s.manual_services_json,
                   s.actual_out_date, s.actual_out_time, s.payment_amount,
                   s.checkout_final_formula, s.checkout_final_total
            FROM debtors d
            JOIN stays s ON s.id = d.stay_id
            WHERE d.amount_owed > 0
            ORDER BY d.id DESC
            """
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def fetch_debtor_by_id(debtor_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT d.id AS debtor_id, d.amount_owed,
                   s.dog_info, s.notes, s.owner_info,
                   s.checkin_date, s.checkin_time, s.checkout_date,
                   s.daily_price, s.location,
                   s.services_json, s.manual_services_json,
                   s.actual_out_date, s.actual_out_time, s.payment_amount,
                   s.checkout_final_formula, s.checkout_final_total
            FROM debtors d
            JOIN stays s ON s.id = d.stay_id
            WHERE d.id = ?
            """,
            (debtor_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def apply_debt_payment(debtor_id: int, amount: int) -> tuple[int, int] | None:
    if amount < 0:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT amount_owed FROM debtors WHERE id = ?", (debtor_id,)
        )
        row = await cur.fetchone()
        if not row:
            await db.commit()
            return None
        owed = int(row[0])
        applied = min(amount, owed)
        new_bal = owed - applied
        if new_bal <= 0:
            await db.execute("DELETE FROM debtors WHERE id = ?", (debtor_id,))
        else:
            await db.execute(
                "UPDATE debtors SET amount_owed = ? WHERE id = ?",
                (new_bal, debtor_id),
            )
        await db.commit()
    return applied, max(0, new_bal)


async def list_stay_price_slots() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM stay_price_slots ORDER BY slot_number ASC, id ASC"
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_stay_price_slot(slot_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM stay_price_slots WHERE id = ?", (slot_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def update_stay_price_slot(slot_id: int, name: str, price: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE stay_price_slots SET name = ?, price = ? WHERE id = ?",
            (name, price, slot_id),
        )
        await db.commit()


async def delete_stay_price_slot(slot_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM stay_price_slots WHERE id = ?", (slot_id,))
        await db.commit()


async def insert_stay_price_slot(name: str, price: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COALESCE(MAX(slot_number), 0) FROM stay_price_slots"
        )
        mx = int((await cur.fetchone())[0])
        sn = mx + 1
        cur = await db.execute(
            "INSERT INTO stay_price_slots (slot_number, name, price) VALUES (?, ?, ?)",
            (sn, name, price),
        )
        await db.commit()
        return int(cur.lastrowid)


async def list_services_catalog() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM services_catalog ORDER BY id ASC"
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_services_map() -> dict[str, tuple[str, int]]:
    rows = await list_services_catalog()
    return {r["slug"]: (r["name"], int(r["price_per_day"])) for r in rows}


async def get_service_row(svc_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM services_catalog WHERE id = ?", (svc_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def update_service_catalog(svc_id: int, name: str, price_per_day: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE services_catalog SET name = ?, price_per_day = ? WHERE id = ?",
            (name, price_per_day, svc_id),
        )
        await db.commit()


async def delete_service_catalog(svc_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM services_catalog WHERE id = ?", (svc_id,))
        await db.commit()


async def insert_service_catalog(slug: str, name: str, price_per_day: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO services_catalog (slug, name, price_per_day)
            VALUES (?, ?, ?)
            """,
            (slug, name, price_per_day),
        )
        await db.commit()
        return int(cur.lastrowid)


async def list_locations_catalog() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM locations_catalog ORDER BY id ASC"
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_location_row(loc_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM locations_catalog WHERE id = ?", (loc_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def update_location_catalog(loc_id: int, name: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE locations_catalog SET name = ? WHERE id = ?",
            (name, loc_id),
        )
        await db.commit()


async def delete_location_catalog(loc_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM locations_catalog WHERE id = ?", (loc_id,))
        await db.commit()


async def insert_location_catalog(slug: str, name: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO locations_catalog (slug, name) VALUES (?, ?)",
            (slug, name),
        )
        await db.commit()
        return int(cur.lastrowid)


async def fetch_completed_stays_for_report() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT * FROM stays
            WHERE COALESCE(is_active, 1) = 0
              AND actual_out_date IS NOT NULL
              AND TRIM(actual_out_date) != ''
            ORDER BY id DESC
            """
        )
        return [dict(r) for r in await cur.fetchall()]
