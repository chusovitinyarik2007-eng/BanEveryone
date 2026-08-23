import os
import sqlite3
import json
import warnings
from dotenv import load_dotenv

import classes

load_dotenv()
def_size = int(os.getenv("def_size", "50"))

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
        try:
            cur.execute("ALTER TABLE Users ADD COLUMN name TEXT NOT NULL DEFAULT 'n/a'")
        except sqlite3.OperationalError:
            pass
    db.close()

"""UUID UPDATE"""

def update_name_by_uuid(uuid, name):
    db = sqlite3.connect("subserver.db")
    with db:
        cur = db.cursor()
        cur.execute("UPDATE Users SET name = ? WHERE uuid = ?", (name, uuid))
        if cur.rowcount == 0:
            cur.execute(
                "INSERT INTO Users (uuid, max_device, hwid, name) VALUES (?, ?, ?, ?)",
                (uuid, def_size, "[]", name),
            )
    db.close()

def get_all_uuid():
    db = sqlite3.connect("subserver.db")
    with db:
        cur = db.cursor()
        cur.execute('''SELECT uuid FROM Users''')
        rows = [r[0] for r in cur.fetchall()]
    db.close()
    return rows


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
            UPDATE Users SET hwid = ? WHERE uuid = ?
            ''', (json.dumps(current_devices), uuid))
            if cur.rowcount == 0:
                cur.execute('''
                            UPDATE Users SET hwid = ? WHERE name = ?
                            ''', (json.dumps(current_devices), uuid))
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
            UPDATE Users SET max_device = ? WHERE uuid = ?
            ''', (current_max_devices, uuid))
            if cur.rowcount == 0:
                cur.execute('''
                            UPDATE Users SET max_device = ? WHERE name = ?
                            ''', (current_max_devices, uuid))
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
            SELECT uuid, max_device, hwid, name FROM Users WHERE uuid = ?
            ''', (uuid_name,))
            res = cur.fetchone()
            if not res:
                cur.execute('''
                            SELECT uuid, max_device, hwid, name FROM Users WHERE name = ?
                            ''', (uuid_name,))
                res = cur.fetchone()
                if not res:
                    return classes.user(def_size, 'n/a',[], 'n/a')
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

