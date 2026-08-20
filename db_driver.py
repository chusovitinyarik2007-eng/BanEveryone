import sqlite3
import json
import warnings

from custom_exceptions import ArgsError

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
db.close()

def push_data(uuid, mx_device, hwid):
    db = sqlite3.connect("subserver.db")
    try:
        with db:
            cur = db.cursor()
            cur.execute('''
                INSERT OR IGNORE INTO Users (uuid, max_device, hwid)
                VALUES (?, ?, ?)
                ''', (uuid, mx_device, json.dumps(hwid)))
            if cur.rowcount == 0:
                warnings.warn("Data already in table, use method update")
                return False
            return True
    finally:
        db.close()

def update_device_data_by_uuid(uuid, current_devices):
    db = sqlite3.connect("subserver.db")
    try:
        with db:
            cur = db.cursor()
            cur.execute('''
            UPDATE Users SET hwid = ? WHERE uuid = ?
            ''', (json.dumps(current_devices), uuid))
            if cur.rowcount == 0:
                warnings.warn("No data has been updated")
    finally:
        db.close()

def update_max_device_data_by_uuid(uuid, current_max_devices):
    db = sqlite3.connect("subserver.db")
    try:
        with db:
            cur = db.cursor()
            cur.execute('''
            UPDATE Users SET max_device = ? WHERE uuid = ?
            ''', (current_max_devices, uuid))
            if cur.rowcount == 0:
                warnings.warn("No data has been updated")
    finally:
        db.close()

def get_data_by_uuid(uuid):
    db = sqlite3.connect("subserver.db")
    try:
        with db:
            cur = db.cursor()
            cur.execute('''
            SELECT uuid, max_device, hwid FROM Users WHERE uuid = ?
            ''', (uuid,))
            res = cur.fetchone()
            if not res:
                push_data(uuid, 50, [])
                return (50, [])
            uuid, mx_device, hwid = res
            hwid = json.loads(hwid)
            return (mx_device, hwid)
    finally:
        db.close()
