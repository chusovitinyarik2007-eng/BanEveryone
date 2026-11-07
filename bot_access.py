"""Persistent, deny-by-default Telegram gate shared by the user and service bots.

Existing visitors and subscription owners are grandfathered exactly once.  No
existing Users/device rows are modified.  All checks happen in aiogram
middleware, before a handler can process old keyboards, direct commands or FSM.
"""
import sqlite3
import os
from pathlib import Path
from datetime import datetime, timezone

import classes


def access_db_path():
    """Keep access/bans in a separate DB; never change Users.hwid for metadata."""
    return os.getenv('ACCESS_DB_PATH') or str(Path(classes.DB_PATH).parent / 'access_control.db')


def connect():
    return sqlite3.connect(access_db_path(), timeout=15)


def init():
    with connect() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS bot_access (
            tg_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL CHECK(status IN ('approved','pending','revoked','blocked')),
            username TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''')
        con.execute('''CREATE TABLE IF NOT EXISTS bot_access_meta (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )''')
        con.execute('''CREATE TABLE IF NOT EXISTS bot_panel_disabled (
            tg_id INTEGER NOT NULL, email TEXT NOT NULL,
            was_enabled INTEGER NOT NULL,
            PRIMARY KEY(tg_id,email)
        )''')
        con.execute('''CREATE TABLE IF NOT EXISTS bot_access_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''')
        con.execute('''CREATE INDEX IF NOT EXISTS idx_bot_access_requests_date
                       ON bot_access_requests(id DESC)''')
        # Import only users known *before* this rollout, exactly once.
        if not con.execute("SELECT 1 FROM bot_access_meta WHERE key='legacy_import_done'").fetchone():
            # Migration reads the existing subscription DB, but only writes
            # approved records into the NEW access_control.db file.
            with sqlite3.connect(classes.DB_PATH) as legacy:
                for sql in (
                    'SELECT tg_id FROM bot_visitors WHERE tg_id > 0',
                    'SELECT DISTINCT tg_id FROM Users WHERE tg_id > 0',
                    'SELECT DISTINCT tg_id FROM subscription_requests WHERE tg_id > 0',
                    'SELECT DISTINCT tg_id FROM trial_claims WHERE tg_id > 0',
                ):
                    try:
                        for (tg_id,) in legacy.execute(sql).fetchall():
                            con.execute("INSERT OR IGNORE INTO bot_access (tg_id,status) VALUES (?, 'approved')", (int(tg_id),))
                    except sqlite3.OperationalError:
                        pass
            con.execute("INSERT INTO bot_access_meta(key,value) VALUES('legacy_import_done','1')")


def status(tg_id: int) -> str:
    with connect() as con:
        row = con.execute('SELECT status FROM bot_access WHERE tg_id=?', (int(tg_id),)).fetchone()
        return row[0] if row else 'new'


def request_access(tg_id: int, username: str | None) -> str:
    """Atomically create one pending request; no duplicate admin notifications."""
    username = (username or '')[:100]
    with connect() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT status FROM bot_access WHERE tg_id=?', (int(tg_id),)).fetchone()
        state = row[0] if row else 'new'
        if state in ('approved', 'pending', 'blocked'):
            return state
        con.execute('''INSERT INTO bot_access(tg_id,status,username)
                       VALUES(?,'pending',?) ON CONFLICT(tg_id) DO UPDATE
                       SET status='pending',username=excluded.username,updated_at=CURRENT_TIMESTAMP''',
                    (int(tg_id), username))
        con.execute('''INSERT INTO bot_access_requests(tg_id,username,state)
                       VALUES(?,?,'pending')''', (int(tg_id), username))
        return 'created'


def resolve(tg_id: int, expected: str, desired: str) -> bool:
    if expected not in ('pending','approved','revoked','blocked') or desired not in ('approved','revoked','blocked'):
        raise ValueError('Invalid access transition')
    with connect() as con:
        cur = con.execute('''UPDATE bot_access SET status=?,updated_at=CURRENT_TIMESTAMP
                             WHERE tg_id=? AND status=?''', (desired, int(tg_id), expected))
        if cur.rowcount and expected == 'pending':
            con.execute('''UPDATE bot_access_requests SET state=? WHERE id=(
                           SELECT id FROM bot_access_requests
                           WHERE tg_id=? AND state='pending' ORDER BY id DESC LIMIT 1)''',
                        (desired, int(tg_id)))
        return cur.rowcount == 1


def set_status(tg_id: int, desired: str) -> None:
    if desired not in ('approved', 'revoked', 'blocked'):
        raise ValueError('Invalid access status')
    with connect() as con:
        con.execute('''INSERT INTO bot_access(tg_id,status) VALUES(?,?)
                       ON CONFLICT(tg_id) DO UPDATE
                       SET status=excluded.status,updated_at=CURRENT_TIMESTAMP''',
                    (int(tg_id), desired))
        con.execute('''UPDATE bot_access_requests SET state=? WHERE id=(
                       SELECT id FROM bot_access_requests
                       WHERE tg_id=? AND state='pending' ORDER BY id DESC LIMIT 1)''',
                    (desired, int(tg_id)))


def list_users(status_filter: str | None = None, limit: int = 30, offset: int = 0):
    with connect() as con:
        if status_filter:
            rows = con.execute('''SELECT tg_id,status,username,updated_at FROM bot_access
                                  WHERE status=? ORDER BY updated_at DESC, tg_id DESC LIMIT ? OFFSET ?''',
                               (status_filter, min(max(int(limit), 1), 100), max(int(offset), 0))).fetchall()
        else:
            rows = con.execute('''SELECT tg_id,status,username,updated_at FROM bot_access
                                  ORDER BY updated_at DESC, tg_id DESC LIMIT ? OFFSET ?''',(min(max(int(limit), 1), 100), max(int(offset), 0))).fetchall()
    return _fill_known_usernames([dict(zip(('tg_id','status','username','updated_at'),r)) for r in rows])


def remember_panel_client_state(tg_id: int, email: str, was_enabled: bool):
    """Record the original panel state, never overwrite on repeated /botblock."""
    with connect() as con:
        con.execute("INSERT OR IGNORE INTO bot_panel_disabled(tg_id,email,was_enabled) VALUES(?,?,?)",
                    (int(tg_id),str(email),int(bool(was_enabled))))


def panel_client_states(tg_id: int):
    with connect() as con:
        return {email:bool(enabled) for email,enabled in con.execute(
            "SELECT email,was_enabled FROM bot_panel_disabled WHERE tg_id=?",(int(tg_id),))}


def clear_panel_client_state(tg_id: int, email: str):
    with connect() as con:
        con.execute('DELETE FROM bot_panel_disabled WHERE tg_id=? AND email=?',(int(tg_id),str(email)))


def count_users(status_filter: str | None = None) -> int:
    with connect() as con:
        if status_filter:
            return con.execute('SELECT COUNT(*) FROM bot_access WHERE status=?', (status_filter,)).fetchone()[0]
        return con.execute('SELECT COUNT(*) FROM bot_access').fetchone()[0]


def get_user(tg_id: int):
    with connect() as con:
        row = con.execute('SELECT tg_id,status,username,updated_at FROM bot_access WHERE tg_id=?',
                          (int(tg_id),)).fetchone()
    return _fill_known_usernames([dict(zip(('tg_id','status','username','updated_at'), row))])[0] if row else None


def remember_username(tg_id: int, username: str | None):
    # Update profile only; do not grant access.
    username = (username or '').lstrip('@')[:100]
    if not username:
        return
    with connect() as con:
        con.execute('''UPDATE bot_access SET username=?
                       WHERE tg_id=? AND username<>?''', (username, int(tg_id), username))


def _fill_known_usernames(records):
    # Read usernames from legacy request records without modifying its DB.
    missing = [entry for entry in records if not entry['username']]
    if not missing:
        return records
    try:
        with sqlite3.connect(classes.DB_PATH) as legacy:
            for item in missing:
                row = legacy.execute('''SELECT username FROM subscription_requests
                                        WHERE tg_id=? AND username IS NOT NULL AND username <> ''
                                        ORDER BY id DESC LIMIT 1''',
                                     (int(item['tg_id']),)).fetchone()
                if row:
                    item['username'] = row[0][:100]
    except sqlite3.OperationalError:
        pass
    return records


def count_requests():
    with connect() as con:
        return con.execute('SELECT COUNT(*) FROM bot_access_requests').fetchone()[0]


def list_requests(limit: int = 30, offset: int = 0):
    with connect() as con:
        rows = con.execute('''SELECT tg_id,state,username,created_at FROM bot_access_requests
                              ORDER BY id DESC LIMIT ? OFFSET ?''',
                           (min(max(int(limit), 1), 100), max(int(offset), 0))).fetchall()
    return [dict(zip(('tg_id','status','username','updated_at'),row)) for row in rows]
