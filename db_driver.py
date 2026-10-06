import asyncio
import sqlite3
import json
import uuid
import warnings
from classes import def_sett, settings, most_important
import classes

def init_db():
    db = sqlite3.connect("subserver.db")
    with db:
        cur = db.cursor()
        cur.execute('''
        CREATE TABLE IF NOT EXISTS Users (
        uuid TEXT PRIMARY KEY,
        max_device INTEGER NOT NULL,
        hwid TEXT NOT NULL
        )
        ''')
        cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
                    """)
        for key, val in def_sett.items():
            js = json.dumps(val)
            cur.execute('''
                INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)
            ''', (key, js))
        try:
            cur.execute("ALTER TABLE Users ADD COLUMN name TEXT NOT NULL DEFAULT 'n/a'")
        except sqlite3.OperationalError as e:
            warnings.warn(f"DB ADD COLUMN name ERROR: \n{e}")

        try:
            cur.execute("ALTER TABLE Users ADD COLUMN tg_id INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError as e:
            warnings.warn(f"DB ADD COLUMN tg_id ERROR: \n{e}")

        """SERVER SETTINGS"""
        cur.execute('''
            SELECT * FROM settings
        ''')
        r = cur.fetchall()
        if r:
            for key, val in r:
                settings[key] = json.loads(val)
        for key, val in most_important.items():
            settings[key] = val

        cur.execute('''
            SELECT uuid, hwid FROM Users
        ''')
        r = cur.fetchall()
        if r:
            for uuid, hwids in r:
                tp_h = json.loads(hwids)
                if tp_h and isinstance(tp_h, list) and not isinstance(tp_h[0], dict):
                    if tp_h:
                        for i, hwid in enumerate(tp_h):
                            tp_h[i] = {"device_os" : "n/a",
                                    'device_model' : 'n/a',
                                    "hwid" : hwid}
                    cur.execute('''
                        UPDATE Users SET hwid = ? WHERE uuid = ?
                    ''', (json.dumps(tp_h), uuid))

        warnings.warn(f"DB INIT: \n{settings}")
    db.close()



"""UUID UPDATE"""

def update_name_by_uuid(uuid, name, tg_id = 0):
    db = sqlite3.connect("subserver.db")
    with db:
        cur = db.cursor()
        cur.execute("INSERT OR REPLACE INTO Users (uuid, name, max_device, hwid, tg_id) VALUES (?, ?, ?, ?, ?)"
                    "ON CONFLICT(uuid) DO UPDATE SET name = excluded.name, tg_id = excluded.tg_id",
                    (uuid, name, settings["DEF_SIZE"], json.dumps([]), tg_id))
    db.close()

def get_all_uuid():
    db = sqlite3.connect("subserver.db")
    with db:
        cur = db.cursor()
        cur.execute('''SELECT uuid FROM Users''')
        rows = [r[0] for r in cur.fetchall()]
    db.close()
    return rows

def update_settings():
    db = sqlite3.connect("subserver.db")
    with db:
        cur = db.cursor()
        sett = [(key,json.dumps(val)) for key, val in settings.items()]
        cur.executemany('''
            INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)
        ''', sett)
        db.commit()
    db.close()

"""SAVE AND LOAD DATA"""

def push_data(usr:classes.user):
    uuid = usr.uuid
    mx_device = usr.max_device
    hwid = usr.current_device

    db = sqlite3.connect("subserver.db")
    try:
        with db:
            cur = db.cursor()
            cur.execute('''
                INSERT OR IGNORE INTO Users (uuid, max_device, hwid, name) VALUES (?, ?, ?, ?)
                ''', (uuid, mx_device, json.dumps(hwid), "n/a"))
            if cur.rowcount == 0:
                warnings.warn("Data already in table, use method update")
                return False
            return True
    finally:
        db.close()

def update_device_data_by(usr:classes.user):
    uuid = usr.uuid
    current_devices = usr.current_device

    db = sqlite3.connect("subserver.db")
    try:
        with db:
            cur = db.cursor()
            cur.execute('''
            UPDATE Users SET hwid = ? WHERE uuid = ? OR name = ?
            ''', (json.dumps(current_devices), uuid, uuid))
            if cur.rowcount == 0:
                warnings.warn("No data has been updated")
    finally:
        db.close()

def update_max_device_data_by(usr:classes.user):
    uuid = usr.uuid
    current_max_devices = usr.max_device
    db = sqlite3.connect("subserver.db")
    try:
        with db:
            cur = db.cursor()
            cur.execute('''
            UPDATE Users SET max_device = ? WHERE uuid = ? OR name = ?
            ''', (current_max_devices, uuid, uuid))
            if cur.rowcount == 0:
                warnings.warn("No data has been updated")
    finally:
        db.close()

def get_data_by(uuid_name):
    db = sqlite3.connect("subserver.db")
    try:
        with db:
            cur = db.cursor()
            cur.execute('''
            SELECT uuid, max_device, hwid, name FROM Users WHERE uuid = ? OR name = ?
            ''', (uuid_name, uuid_name))
            res = cur.fetchone()
            if not res:
                return classes.user(uuid_name, 'n/a', settings["DEF_SIZE"], [])
            uuid, mx_device, hwid, name = res
            hwid = json.loads(hwid)
            return classes.user(uuid, name, mx_device, hwid)
    finally:
        db.close()

def get_data_by_tg_id(tg_id):
    db = sqlite3.connect("subserver.db")
    try:
        with db:
            cur = db.cursor()
            cur.execute('''
            SELECT uuid, max_device, hwid, name, tg_id FROM Users WHERE tg_id = ?
            ''', (tg_id,))
            res = cur.fetchall()
            if not res:
                return False
            users = []
            for user in res:
                uuid, mx_device, hwid, name, tg_id = user
                hwid = json.loads(hwid)
                users.append(classes.user(uuid, name, mx_device, hwid))
            return users
    finally:
        db.close()



def find_all_less(mx):
    db = sqlite3.connect("subserver.db")
    try:
        with db:
            cur = db.cursor()
            cur.execute('''
            SELECT name, max_device, hwid FROM Users WHERE max_device <= ?''', (mx,))
            row = cur.fetchall()
            rows = ""
            for w in row:
                rows += (f'Client: <code>{w[0]}</code>\n'
                            f'Limit: {w[1]}\n'
                            f'Current connections: {len(json.loads(w[2]))}\n\n')
            return rows
    finally:
        db.close()

def is_user_exist(uuid_or_name):
    db = sqlite3.connect("subserver.db")
    with db:
        cur = db.cursor()
        cur.execute('''
            SELECT EXISTS (SELECT 1 FROM Users WHERE uuid = ? OR name = ?)
        ''', (uuid_or_name, uuid_or_name))
        res = cur.fetchone()[0]
        return True if res else False
def remove_hwids(uuid, hwids):
    db = sqlite3.connect("subserver.db")
    try:
        with db:
            cur = db.cursor()
            cur.execute('''
                SELECT hwid FROM USERS WHERE uuid = ? OR name = ?
            ''', (uuid, uuid))
            res = cur.fetchone()
            if not res:
                return False
            res = json.loads(res[0])
            to_be_updated = []
            for i in res:
                if i['hwid'] not in hwids:
                    to_be_updated.append(i)
            cur.execute('''
            UPDATE Users SET hwid = ? WHERE uuid = ? OR name = ?
            ''', (json.dumps(to_be_updated), uuid, uuid))
            return True
    finally:
        db.close()
    return False


async def clear_trash_loop():
    await asyncio.sleep(5)
    while True:
        try:
            import xui_connection
            users = await xui_connection.get_users_()
            users = set(users.keys())
            db = sqlite3.connect("subserver.db")
            try:
                with db:
                    cur = db.cursor()
                    cur.execute('''SELECT uuid FROM Users''')
                    db_uuids = [i[0] for i in cur.fetchall()]
                    for uuid in db_uuids:
                        if uuid not in users:
                            cur.execute("DELETE FROM Users WHERE uuid = ?", (uuid,))
                            print(f"removed trash uuid: {uuid}")
            finally:
                db.close()
        except Exception as e:
            warnings.warn(f"Error while clearing trash loop: {e}")

        await asyncio.sleep(settings["CLEAR_TRASH"] * 60)
