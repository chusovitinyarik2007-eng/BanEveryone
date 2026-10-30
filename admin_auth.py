"""SQLite-backed, one-time Telegram admin approval with browser-bound challenges.

No password, Telegram token, session token or client UUID is exposed in URLs.
Database reuses the existing DB_PATH and migrates automatically.
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import classes

CHALLENGE_TTL = 180
SESSION_TTL = 6 * 60 * 60


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def allowed_admin(telegram_id):
    try:
        allowed = {int(s.strip()) for s in os.getenv('ADMINS', '').split(',') if s.strip()}
        return int(telegram_id) in allowed
    except (TypeError, ValueError):
        return False


@contextmanager
def database():
    con = sqlite3.connect(classes.DB_PATH, timeout=15)
    try:
        con.execute('PRAGMA busy_timeout = 15000')
        with con:
            con.execute('''CREATE TABLE IF NOT EXISTS admin_web_challenges (
                id TEXT PRIMARY KEY, browser_hash TEXT NOT NULL,
                verify_code TEXT NOT NULL, created INTEGER NOT NULL,
                expires INTEGER NOT NULL, status TEXT NOT NULL,
                admin_id INTEGER, request_ip TEXT NOT NULL, user_agent TEXT NOT NULL)''')
            con.execute('''CREATE TABLE IF NOT EXISTS admin_web_sessions (
                token_hash TEXT PRIMARY KEY, admin_id INTEGER NOT NULL,
                csrf_token TEXT NOT NULL, created INTEGER NOT NULL,
                expires INTEGER NOT NULL)''')
            con.execute('CREATE INDEX IF NOT EXISTS admin_challenge_created ON admin_web_challenges(created)')
            con.execute('CREATE INDEX IF NOT EXISTS admin_session_expires ON admin_web_sessions(expires)')
            yield con
    finally:
        con.close()


def new_challenge(ip, ua):
    now = int(time.time())
    identifier = secrets.token_urlsafe(18)
    verifier = secrets.token_urlsafe(32)
    code = str(secrets.randbelow(900000) + 100000)
    with database() as con:
        con.execute('DELETE FROM admin_web_challenges WHERE expires < ?', (now - 60,))
        con.execute('DELETE FROM admin_web_sessions WHERE expires < ?', (now,))
        # Bound repeated attempts; do not let outsiders exhaust the challenge table.
        if con.execute('SELECT COUNT(*) FROM admin_web_challenges WHERE request_ip=? AND created>?',
                       (ip, now - 600)).fetchone()[0] >= 25:
            raise ValueError('Слишком много попыток. Повторите позже.')
        con.execute('''INSERT INTO admin_web_challenges
                     (id,browser_hash,verify_code,created,expires,status,admin_id,request_ip,user_agent)
                     VALUES (?,?,?,?,?,?,?,?,?)''',
                    (identifier, digest(verifier), code, now, now+CHALLENGE_TTL,
                     'pending', None, str(ip)[:120], str(ua)[:240]))
    return {'id': identifier, 'verifier': verifier, 'code': code, 'expires': now+CHALLENGE_TTL}


def challenge_info(identifier):
    with database() as con:
        row = con.execute('''SELECT verify_code,created,expires,status,request_ip,user_agent
                             FROM admin_web_challenges WHERE id=?''', (identifier,)).fetchone()
    if not row or row[2] <= int(time.time()) or row[3] != 'pending':
        return None
    return {'code':row[0], 'created':datetime.fromtimestamp(row[1],timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
            'ip':row[4], 'user_agent':row[5]}


def decision(identifier, telegram_id, approve):
    if not allowed_admin(telegram_id):
        return False
    with database() as con:
        cursor = con.execute('''UPDATE admin_web_challenges SET status=?,admin_id=?
                         WHERE id=? AND status='pending' AND expires>?''',
                         ('approved' if approve else 'rejected', int(telegram_id), identifier, int(time.time())))
        return cursor.rowcount == 1


def claim(identifier, verifier):
    if not identifier or not verifier:
        return 'missing', None
    now = int(time.time())
    with database() as con:
        row = con.execute('''SELECT browser_hash,expires,status,admin_id FROM admin_web_challenges
                             WHERE id=?''', (identifier,)).fetchone()
        if not row or not hmac.compare_digest(row[0], digest(verifier)):
            return 'invalid', None
        if row[1] <= now:
            return 'expired', None
        if row[2] in ('pending','rejected'):
            return row[2], None
        if row[2] != 'approved' or not allowed_admin(row[3]):
            return 'invalid', None
        # Single-use, atomic confirmation; a stolen deep-link cannot redeem without
        # the HttpOnly browser binding cookie.
        cursor = con.execute('''UPDATE admin_web_challenges SET status='claimed'
                         WHERE id=? AND status='approved' AND expires>?''', (identifier, now))
        if cursor.rowcount != 1:
            return 'invalid', None
        token = secrets.token_urlsafe(40)
        csrf = secrets.token_urlsafe(32)
        con.execute('INSERT INTO admin_web_sessions VALUES (?,?,?,?,?)',
                    (digest(token), int(row[3]), csrf, now, now+SESSION_TTL))
    return 'approved', {'token':token,'csrf':csrf, 'admin_id':int(row[3]), 'expires':now+SESSION_TTL}


def get_session(token):
    if not token or len(token) > 200:
        return None
    with database() as con:
        row = con.execute('SELECT admin_id,csrf_token,expires FROM admin_web_sessions WHERE token_hash=?',
                          (digest(token),)).fetchone()
    if not row or row[2] <= int(time.time()) or not allowed_admin(row[0]):
        return None
    return {'admin_id':row[0], 'csrf':row[1], 'expires':row[2]}


def revoke(token):
    if token:
        with database() as con:
            con.execute('DELETE FROM admin_web_sessions WHERE token_hash=?', (digest(token),))
