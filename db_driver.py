import asyncio
import json
import sqlite3
import warnings
from datetime import datetime

import classes
from classes import def_sett, most_important, settings


def _db_path():
    # Путь к самой БД не должен переопределяться значением, сохранённым внутри переносимой БД.
    return classes.DB_PATH or settings.get("DB_PATH") or def_sett.get("DB_PATH") or "subserver.db"


def _connect():
    return sqlite3.connect(_db_path())


def init_db():
    db = _connect()
    with db:
        cur = db.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS Users (
                uuid TEXT PRIMARY KEY,
                max_device INTEGER NOT NULL,
                hwid TEXT NOT NULL
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS subscription_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_type TEXT NOT NULL,
                tg_id INTEGER NOT NULL,
                username TEXT,
                months INTEGER NOT NULL,
                anti_block INTEGER NOT NULL DEFAULT 0,
                sub_ids TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS client_discounts (
                tg_id INTEGER PRIMARY KEY,
                discount INTEGER NOT NULL DEFAULT 0,
                permanent_discount INTEGER NOT NULL DEFAULT 0,
                temporary_discount INTEGER NOT NULL DEFAULT 0
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS alert_log (
                tg_id INTEGER NOT NULL,
                uuid TEXT NOT NULL,
                alert_days INTEGER NOT NULL,
                expiry_time INTEGER NOT NULL,
                sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tg_id, uuid, alert_days, expiry_time)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_tg_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS broadcast_messages (
                broadcast_id INTEGER NOT NULL,
                tg_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                PRIMARY KEY (broadcast_id, tg_id),
                FOREIGN KEY (broadcast_id) REFERENCES broadcasts(id) ON DELETE CASCADE
            )
        ''')

        for key, val in def_sett.items():
            cur.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, json.dumps(val)),
            )

        for statement in (
            "ALTER TABLE Users ADD COLUMN name TEXT NOT NULL DEFAULT 'n/a'",
            "ALTER TABLE Users ADD COLUMN tg_id INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                cur.execute(statement)
            except sqlite3.OperationalError:
                pass

        try:
            cur.execute("ALTER TABLE subscription_requests ADD COLUMN total_cost INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            cur.execute("ALTER TABLE subscription_requests ADD COLUMN discount_amount INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            cur.execute("ALTER TABLE subscription_requests ADD COLUMN discount_type TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass

        # Скидки v2: постоянная и одноразовая. Старое поле discount мигрируем
        # в постоянную скидку, чтобы существующие настройки не потерялись.
        for statement in (
            "ALTER TABLE client_discounts ADD COLUMN permanent_discount INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE client_discounts ADD COLUMN temporary_discount INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                cur.execute(statement)
            except sqlite3.OperationalError:
                pass
        try:
            cur.execute(
                "UPDATE client_discounts SET permanent_discount = discount "
                "WHERE permanent_discount = 0 AND discount > 0"
            )
        except sqlite3.OperationalError:
            pass

        cur.execute("SELECT key, value FROM settings")
        for key, val in cur.fetchall():
            if key in def_sett:
                try:
                    settings[key] = json.loads(val)
                except Exception:
                    settings[key] = def_sett[key]
        for key, val in def_sett.items():
            settings.setdefault(key, val)
        settings.update(most_important)
        settings["DB_PATH"] = classes.DB_PATH

        cur.execute("SELECT uuid, hwid FROM Users")
        for sub_id, hwids in cur.fetchall():
            try:
                parsed = json.loads(hwids)
            except Exception:
                parsed = []
            if parsed and isinstance(parsed, list) and not isinstance(parsed[0], dict):
                parsed = [{"device_os": "n/a", "device_model": "n/a", "hwid": item} for item in parsed]
                cur.execute("UPDATE Users SET hwid = ? WHERE uuid = ?", (json.dumps(parsed), sub_id))
    db.close()


def update_name_by_uuid(uuid, name, tg_id=0):
    db = _connect()
    with db:
        db.execute('''
            INSERT INTO Users (uuid, name, max_device, hwid, tg_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(uuid) DO UPDATE SET name=excluded.name, tg_id=excluded.tg_id
        ''', (uuid, name, settings["DEF_SIZE"], json.dumps([]), int(tg_id or 0)))
    db.close()


def get_all_uuid():
    db = _connect()
    with db:
        rows = [r[0] for r in db.execute("SELECT uuid FROM Users").fetchall()]
    db.close()
    return rows


def update_settings():
    db = _connect()
    with db:
        rows = [(key, json.dumps(settings.get(key, default))) for key, default in def_sett.items()]
        db.executemany("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", rows)
    db.close()


def push_data(usr: classes.user):
    db = _connect()
    try:
        with db:
            cur = db.execute(
                "INSERT OR IGNORE INTO Users (uuid, max_device, hwid, name, tg_id) VALUES (?, ?, ?, ?, ?)",
                (usr.uuid, usr.max_device, json.dumps(usr.current_device or []), usr.name or "n/a", int(usr.tg_id or 0)),
            )
            return cur.rowcount > 0
    finally:
        db.close()


def update_device_data_by(usr: classes.user):
    db = _connect()
    with db:
        db.execute("UPDATE Users SET hwid = ? WHERE uuid = ? OR name = ?", (json.dumps(usr.current_device or []), usr.uuid, usr.uuid))
    db.close()


def update_max_device_data_by(usr: classes.user):
    db = _connect()
    with db:
        db.execute("UPDATE Users SET max_device = ? WHERE uuid = ? OR name = ?", (usr.max_device, usr.uuid, usr.uuid))
    db.close()


def set_subscription_max_device(sub_id: str, limit: int):
    db = _connect()
    with db:
        db.execute("UPDATE Users SET max_device = ? WHERE uuid = ?", (int(limit), str(sub_id)))
    db.close()


def _row_to_user(row):
    if not row:
        return None
    sub_id, max_device, hwid, name, tg_id = row
    try:
        devices = json.loads(hwid) if hwid else []
    except Exception:
        devices = []
    return classes.user(sub_id, name, max_device, devices, tg_id)


def get_data_by(uuid_name):
    db = _connect()
    try:
        row = db.execute(
            "SELECT uuid, max_device, hwid, name, tg_id FROM Users WHERE uuid = ? OR name = ?",
            (uuid_name, uuid_name),
        ).fetchone()
        return _row_to_user(row) or classes.user(uuid_name, "n/a", settings["DEF_SIZE"], [], 0)
    finally:
        db.close()


def get_owned_subscription(sub_id: str, tg_id: int):
    db = _connect()
    try:
        row = db.execute(
            "SELECT uuid, max_device, hwid, name, tg_id FROM Users WHERE uuid = ? AND tg_id = ?",
            (sub_id, int(tg_id)),
        ).fetchone()
        return _row_to_user(row)
    finally:
        db.close()


def get_data_by_tg_id(tg_id):
    db = _connect()
    try:
        rows = db.execute(
            "SELECT uuid, max_device, hwid, name, tg_id FROM Users WHERE tg_id = ? ORDER BY name",
            (int(tg_id),),
        ).fetchall()
        return [_row_to_user(row) for row in rows] if rows else False
    finally:
        db.close()


def set_subscription_tg_id(sub_id: str, tg_id: int):
    db = _connect()
    with db:
        cur = db.execute("UPDATE Users SET tg_id = ? WHERE uuid = ?", (int(tg_id), sub_id))
    db.close()
    return cur.rowcount > 0


def remove_single_hwid(sub_id: str, hwid: str):
    user = get_data_by(sub_id)
    if not user or user.name == "n/a":
        return False
    old = user.current_device or []
    new = [d for d in old if str(d.get("hwid")) != str(hwid)]
    if len(new) == len(old):
        return False
    user.current_device = new
    update_device_data_by(user)
    return True



def register_or_refresh_device(sub_id: str, hwid: str, device_os: str | None, device_model: str | None):
    """Атомарно обновляет существующее устройство или добавляет новое в пределах лимита.

    Возвращает (status, current_count, max_device), где status: existing/added/limit/not_found.
    BEGIN IMMEDIATE не даёт двум одновременным запросам одновременно пройти проверку лимита.
    """
    db = sqlite3.connect(_db_path(), timeout=10)
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT max_device, hwid FROM Users WHERE uuid = ?", (str(sub_id),)).fetchone()
        if not row:
            db.rollback()
            return "not_found", 0, 0

        max_device = int(row[0])
        try:
            devices = json.loads(row[1]) if row[1] else []
        except Exception:
            devices = []

        new_device = {
            "hwid": str(hwid),
            "device_os": device_os or "n/a",
            "device_model": device_model or "n/a",
        }

        for index, device in enumerate(devices):
            if str(device.get("hwid")) == str(hwid):
                devices[index] = new_device
                db.execute("UPDATE Users SET hwid = ? WHERE uuid = ?", (json.dumps(devices), str(sub_id)))
                db.commit()
                return "existing", len(devices), max_device

        if len(devices) >= max_device:
            db.rollback()
            return "limit", len(devices), max_device

        devices.append(new_device)
        db.execute("UPDATE Users SET hwid = ? WHERE uuid = ?", (json.dumps(devices), str(sub_id)))
        db.commit()
        return "added", len(devices), max_device
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def transition_request_status(request_id: int, from_status: str, to_status: str) -> bool:
    """Атомарно переводит заявку между статусами."""
    db = _connect()
    try:
        with db:
            cur = db.execute(
                "UPDATE subscription_requests SET status = ? WHERE id = ? AND status = ?",
                (str(to_status), int(request_id), str(from_status)),
            )
            return cur.rowcount > 0
    finally:
        db.close()


def find_all_less(mx):
    db = _connect()
    try:
        rows = db.execute("SELECT name, max_device, hwid FROM Users WHERE max_device <= ?", (mx,)).fetchall()
        return "".join(
            f"Client: <code>{name}</code>\nLimit: {limit}\nCurrent connections: {len(json.loads(hwids))}\n\n"
            for name, limit, hwids in rows
        )
    finally:
        db.close()


def is_user_exist(uuid_or_name):
    db = _connect()
    try:
        return bool(db.execute("SELECT EXISTS (SELECT 1 FROM Users WHERE uuid = ? OR name = ?)", (uuid_or_name, uuid_or_name)).fetchone()[0])
    finally:
        db.close()


def remove_hwids(uuid, hwids):
    user = get_data_by(uuid)
    if not user or user.name == "n/a":
        return False
    remove_set = {str(x) for x in hwids}
    user.current_device = [d for d in (user.current_device or []) if str(d.get("hwid")) not in remove_set]
    update_device_data_by(user)
    return True



def get_client_discounts(tg_id: int) -> dict:
    """Возвращает обе скидки. Временная имеет приоритет над постоянной."""
    db = _connect()
    try:
        row = db.execute(
            "SELECT permanent_discount, temporary_discount FROM client_discounts WHERE tg_id = ?",
            (int(tg_id),),
        ).fetchone()
        if not row:
            return {"permanent": 0, "temporary": 0}
        return {
            "permanent": max(0, int(row[0] or 0)),
            "temporary": max(0, int(row[1] or 0)),
        }
    finally:
        db.close()


def get_effective_discount(tg_id: int) -> tuple[int, str]:
    discounts = get_client_discounts(tg_id)
    if discounts["temporary"] > 0:
        return discounts["temporary"], "temporary"
    if discounts["permanent"] > 0:
        return discounts["permanent"], "permanent"
    return 0, ""


def get_client_discount(tg_id: int) -> int:
    """Совместимость со старым кодом: возвращает активную скидку."""
    return get_effective_discount(tg_id)[0]


def set_client_discount(tg_id: int, discount: int, discount_type: str = "permanent"):
    discount = max(0, int(discount))
    if discount_type not in {"permanent", "temporary"}:
        raise ValueError("discount_type must be permanent or temporary")
    column = "permanent_discount" if discount_type == "permanent" else "temporary_discount"
    db = _connect()
    try:
        with db:
            db.execute(
                "INSERT OR IGNORE INTO client_discounts(tg_id, discount, permanent_discount, temporary_discount) "
                "VALUES (?, 0, 0, 0)",
                (int(tg_id),),
            )
            db.execute(
                f"UPDATE client_discounts SET {column} = ? WHERE tg_id = ?",
                (discount, int(tg_id)),
            )
            db.execute(
                "DELETE FROM client_discounts WHERE tg_id = ? "
                "AND permanent_discount = 0 AND temporary_discount = 0",
                (int(tg_id),),
            )
    finally:
        db.close()
    return discount


def consume_temporary_discount(tg_id: int, expected_amount: int | None = None) -> bool:
    """Списывает одноразовую скидку после УСПЕШНОГО продления."""
    db = _connect()
    try:
        with db:
            if expected_amount is None:
                cur = db.execute(
                    "UPDATE client_discounts SET temporary_discount = 0 "
                    "WHERE tg_id = ? AND temporary_discount > 0",
                    (int(tg_id),),
                )
            else:
                cur = db.execute(
                    "UPDATE client_discounts SET temporary_discount = 0 "
                    "WHERE tg_id = ? AND temporary_discount = ?",
                    (int(tg_id), int(expected_amount)),
                )
            db.execute(
                "DELETE FROM client_discounts WHERE tg_id = ? "
                "AND permanent_discount = 0 AND temporary_discount = 0",
                (int(tg_id),),
            )
            return cur.rowcount > 0
    finally:
        db.close()


def create_request(request_type: str, tg_id: int, username: str | None, months: int, anti_block=False, sub_ids=None, total_cost=0, discount_amount=0, discount_type=""):
    db = _connect()
    with db:
        cur = db.execute(
            """
            INSERT INTO subscription_requests(request_type, tg_id, username, months, anti_block, sub_ids, total_cost, discount_amount, discount_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_type, int(tg_id), username or "", int(months), int(bool(anti_block)),
                json.dumps(sub_ids or []), int(total_cost or 0), int(discount_amount or 0), str(discount_type or ""),
            ),
        )
        request_id = cur.lastrowid
    db.close()
    return request_id


def get_request(request_id: int):
    db = _connect()
    try:
        row = db.execute('''
            SELECT id, request_type, tg_id, username, months, anti_block, sub_ids, status, created_at, total_cost, discount_amount, discount_type
            FROM subscription_requests WHERE id = ?
        ''', (int(request_id),)).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "request_type": row[1], "tg_id": row[2], "username": row[3],
            "months": row[4], "anti_block": bool(row[5]), "sub_ids": json.loads(row[6] or "[]"),
            "status": row[7], "created_at": row[8], "total_cost": int(row[9] or 0),
            "discount_amount": int(row[10] or 0), "discount_type": str(row[11] or ""),
        }
    finally:
        db.close()


def set_request_status(request_id: int, status: str):
    db = _connect()
    with db:
        cur = db.execute("UPDATE subscription_requests SET status = ? WHERE id = ? AND status = 'pending'", (status, int(request_id)))
    db.close()
    return cur.rowcount > 0


def alert_was_sent(tg_id: int, sub_id: str, days: int, expiry_time: int):
    db = _connect()
    try:
        row = db.execute(
            "SELECT 1 FROM alert_log WHERE tg_id=? AND uuid=? AND alert_days=? AND expiry_time=?",
            (int(tg_id), sub_id, int(days), int(expiry_time)),
        ).fetchone()
        return bool(row)
    finally:
        db.close()


def mark_alert_sent(tg_id: int, sub_id: str, days: int, expiry_time: int):
    db = _connect()
    with db:
        db.execute(
            "INSERT OR IGNORE INTO alert_log(tg_id, uuid, alert_days, expiry_time) VALUES (?, ?, ?, ?)",
            (int(tg_id), sub_id, int(days), int(expiry_time)),
        )
    db.close()


def get_all_tg_id():
    """Возвращает все уникальные Telegram ID пользователей с привязанными подписками."""
    db = _connect()
    try:
        rows = db.execute(
            "SELECT DISTINCT tg_id FROM Users WHERE tg_id IS NOT NULL AND tg_id != 0"
        ).fetchall()
        return {int(row[0]) for row in rows if row and row[0]}
    finally:
        db.close()


def create_broadcast(admin_tg_id: int) -> int:
    db = _connect()
    try:
        with db:
            cur = db.execute(
                "INSERT INTO broadcasts(admin_tg_id) VALUES (?)",
                (int(admin_tg_id),),
            )
            return int(cur.lastrowid)
    finally:
        db.close()


def add_broadcast_message(broadcast_id: int, tg_id: int, message_id: int):
    db = _connect()
    try:
        with db:
            db.execute(
                "INSERT OR REPLACE INTO broadcast_messages(broadcast_id, tg_id, message_id) VALUES (?, ?, ?)",
                (int(broadcast_id), int(tg_id), int(message_id)),
            )
    finally:
        db.close()


def get_broadcast_messages(broadcast_id: int):
    db = _connect()
    try:
        return [
            {"tg_id": int(tg_id), "message_id": int(message_id)}
            for tg_id, message_id in db.execute(
                "SELECT tg_id, message_id FROM broadcast_messages WHERE broadcast_id = ?",
                (int(broadcast_id),),
            ).fetchall()
        ]
    finally:
        db.close()


def delete_broadcast(broadcast_id: int):
    db = _connect()
    try:
        with db:
            db.execute("DELETE FROM broadcast_messages WHERE broadcast_id = ?", (int(broadcast_id),))
            db.execute("DELETE FROM broadcasts WHERE id = ?", (int(broadcast_id),))
    finally:
        db.close()


async def clear_trash_loop():
    await asyncio.sleep(5)
    while True:
        try:
            import xui_connection
            users = set((await xui_connection.get_users_()).keys())
            db = _connect()
            with db:
                for (sub_id,) in db.execute("SELECT uuid FROM Users").fetchall():
                    if sub_id not in users:
                        db.execute("DELETE FROM Users WHERE uuid = ?", (sub_id,))
            db.close()
        except Exception as e:
            warnings.warn(f"Error while clearing trash loop: {e}")
        await asyncio.sleep(settings["CLEAR_TRASH"] * 60)


def has_active_request(tg_id: int, request_type: str) -> bool:
    """Есть ли у пользователя необработанная заявка указанного типа."""
    db = _connect()
    try:
        row = db.execute(
            "SELECT 1 FROM subscription_requests "
            "WHERE tg_id = ? AND request_type = ? AND status IN ('pending','processing') LIMIT 1",
            (int(tg_id), str(request_type)),
        ).fetchone()
        return bool(row)
    finally:
        db.close()
