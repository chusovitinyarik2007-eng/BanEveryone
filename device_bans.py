"""Subscription-specific HWID denylist, independent of the existing Users.hwid JSON.
The HWID header is supplied by a client and is not cryptographically attested.
"""
import sqlite3
import bot_access


def connect():
    return sqlite3.connect(bot_access.access_db_path(), timeout=15)


def init():
    with connect() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS banned_hwids (
            sub_id TEXT NOT NULL, hwid TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(sub_id,hwid)
        )''')


def ban(sub_id, hwid):
    if not sub_id or not hwid:
        raise ValueError('sub_id/hwid required')
    with connect() as con:
        con.execute('INSERT OR IGNORE INTO banned_hwids(sub_id,hwid) VALUES(?,?)', (str(sub_id),str(hwid)))


def unban(sub_id, hwid):
    with connect() as con:
        cur=con.execute('DELETE FROM banned_hwids WHERE sub_id=? AND hwid=?', (str(sub_id),str(hwid)))
        return cur.rowcount > 0


def is_banned(sub_id, hwid):
    if not hwid:
        return False
    with connect() as con:
        return con.execute('SELECT 1 FROM banned_hwids WHERE sub_id=? AND hwid=?', (str(sub_id),str(hwid))).fetchone() is not None


def list_banned(sub_id):
    with connect() as con:
        return [{'hwid':r[0], 'created_at':r[1]} for r in con.execute(
            'SELECT hwid,created_at FROM banned_hwids WHERE sub_id=? ORDER BY created_at DESC',(str(sub_id),))]
