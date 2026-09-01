import asyncio
import os
import sqlite3
import json
import warnings
from dotenv import load_dotenv
from classes import def_sett, settings
import classes
import xui_connection

load_dotenv()
def_size = int(os.getenv("def_size", "50"))
clear_trash = int(os.getenv("CLEAR_TRASH_EVERY_MIN", "60"))

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
        except sqlite3.OperationalError:
            pass

        """SERVER SETTINGS"""
        cur.execute('''
            SELECT * FROM settings
        ''')
        r = cur.fetchall()
        if r:
            for key, val in r:
                settings[key] = json.loads(val)
    db.close()



"""UUID UPDATE"""

def update_name_by_uuid(uuid, name):
    db = sqlite3.connect("subserver.db")
    with db:
        cur = db.cursor()
        cur.execute("INSERT OR REPLACE INTO Users (uuid, name, max_device, hwid) VALUES (?, ?, ?, ?)"
                    "ON CONFLICT(uuid) DO UPDATE SET name = excluded.name"
                    ,
                    (uuid, name, def_size, json.dumps([])))
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
                return classes.user(uuid_name, 'n/a',   def_size, 'n/a')
            uuid, mx_device, hwid, name = res
            hwid = json.loads(hwid)
            return classes.user(uuid, name, mx_device, hwid)
    finally:
        db.close()

def find_all_less(mx):
    db = sqlite3.connect("subserver.db")
    try:
        with db:
            cur = db.cursor()
            cur.execute('''
            SELECT * FROM Users WHERE max_device <= ?''', (mx,))
            row = cur.fetchall()
            rows = ""
            for w in row:
                rows += (f'Client: <code>{w[-1]}</code>\n'
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

async def clear_trash_loop():
    await asyncio.sleep(5)
    while True:
        try:
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

        await asyncio.sleep(clear_trash * 60)

