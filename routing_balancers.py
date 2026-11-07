"""Editable AUTO balancer definitions, with safe migrations from the four legacy pools.

The old `smart_nodes`, `smart_options`, alias and profile mappings are kept.
Only new balancer metadata is stored in `smart_balancers`; existing node.pools IDs
continue to select panel inbounds. Operations are transactional and cycle-safe.
"""
import json
import re
import sqlite3
from contextlib import contextmanager

import classes

STRATEGIES = ('leastPing', 'leastLoad', 'roundRobin', 'random')
SCOPES = ('main', 'reserve')
LEGACY = (
    ('gemini', '✨ Gemini', 'main', 'all', False),
    ('other', '🌍 Все сайты', 'main', 'all', True),
    ('all', '🔄 Все серверы', 'main', 'reserve', False),
    ('reserve', '🛟 Антиглушилка', 'reserve', 'block', False),
)
_ID = re.compile(r'^[a-z][a-z0-9_]{0,23}$')
_RULE = re.compile(r'^(?:domain|full|geosite|regexp|keyword|ip|geoip):.+$', re.I)

@contextmanager
def database():
    con = sqlite3.connect(classes.DB_PATH)
    try:
        with con:
            # Be compatible with database initialization order and previous builds.
            con.execute('CREATE TABLE IF NOT EXISTS smart_options (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            con.execute('CREATE TABLE IF NOT EXISTS smart_nodes (id TEXT PRIMARY KEY,name TEXT NOT NULL,pools TEXT NOT NULL)')
            con.execute('''CREATE TABLE IF NOT EXISTS smart_balancers
                (id TEXT PRIMARY KEY, name TEXT NOT NULL, scope TEXT NOT NULL,
                 strategy TEXT NOT NULL, fallback TEXT NOT NULL, rules TEXT NOT NULL,
                 enabled INTEGER NOT NULL DEFAULT 1, is_default INTEGER NOT NULL DEFAULT 0,
                 priority INTEGER NOT NULL DEFAULT 100)''')
            if not con.execute("SELECT value FROM smart_options WHERE key='migration_balancers_v1'").fetchone():
                for idx, (ident, name, scope, fallback, is_default) in enumerate(LEGACY):
                    legacy_rules = con.execute('SELECT value FROM smart_options WHERE key=?', ('rules_' + ident,)).fetchone()
                    rules = legacy_rules[0] if legacy_rules else json.dumps(['domain:gemini.google.com', 'domain:generativelanguage.googleapis.com', 'domain:aistudio.google.com', 'domain:ai.google.dev'] if ident == 'gemini' else [])
                    legacy_enabled = con.execute('SELECT value FROM smart_options WHERE key=?', ('enabled_' + ident,)).fetchone()
                    active = bool(json.loads(legacy_enabled[0])) if legacy_enabled else True
                    con.execute('''INSERT OR IGNORE INTO smart_balancers
                         (id,name,scope,strategy,fallback,rules,enabled,is_default,priority)
                         VALUES (?,?,?,?,?,?,?,?,?)''',
                        (ident, name, scope, 'leastPing', fallback, rules, int(active), int(is_default), idx * 10))
                con.execute("INSERT INTO smart_options VALUES ('migration_balancers_v1','true')")
            yield con
    finally:
        con.close()


def _fetch(con):
    result = []
    for row in con.execute('''SELECT id,name,scope,strategy,fallback,rules,enabled,is_default,priority
                              FROM smart_balancers ORDER BY priority,id''').fetchall():
        try:
            rules = json.loads(row[5])
        except (TypeError, ValueError):
            rules = []
        result.append(dict(zip(('id','name','scope','strategy','fallback','rules','enabled','is_default','priority'),
                               (row[0],row[1],row[2],row[3],row[4],rules if row[2] != 'reserve' else [],bool(row[6]),bool(row[7]),row[8]))))
    return result


def all_balancers():
    with database() as con:
        return _fetch(con)


def get(ident):
    return next((b for b in all_balancers() if b['id'] == ident), None)


def _validate_graph(items):
    lookup = {b['id']: b for b in items}
    for b in items:
        target = b['fallback']
        if target not in ('direct','block','none') and target not in lookup:
            raise ValueError('Несуществующий fallback: ' + target)
        if target in lookup and b['scope'] == 'main' and lookup[target]['scope'] == 'reserve':
            # Permitted: reserve will simply be unavailable for basic users.
            pass
    for entry in lookup:
        visited = set()
        at = entry
        while at in lookup:
            if at in visited:
                raise ValueError('Обнаружен цикл fallback: ' + ' → '.join([*visited, at]))
            visited.add(at)
            at = lookup[at]['fallback']


def create(name, scope='main'):
    if scope not in SCOPES:
        raise ValueError('Тип пула должен быть main или reserve')
    name = (name or '').strip()
    if not 1 <= len(name) <= 70 or '\n' in name or '\r' in name:
        raise ValueError('Название: 1–70 символов, одна строка')
    with database() as con:
        existing = {b['id'] for b in _fetch(con)}
        number = 1
        while f'custom_{number}' in existing:
            number += 1
        ident = f'custom_{number}'
        con.execute('''INSERT INTO smart_balancers VALUES (?,?,?,?,?,?,?,?,?)''',
                    (ident, name, scope, 'leastPing', 'block', '[]', 1, 0, 100 + number))
    return ident


def update(ident, **changes):
    permitted = {'name','scope','strategy','fallback','rules','enabled','is_default','priority'}
    if not changes or any(field not in permitted for field in changes):
        raise ValueError('Неизвестное поле балансировщика')
    with database() as con:
        entries = _fetch(con)
        match = next((b for b in entries if b['id'] == ident), None)
        if not match:
            raise ValueError('Балансировщик не найден')
        if 'name' in changes and (not isinstance(changes['name'], str) or not 1 <= len(changes['name'].strip()) <= 70 or '\n' in changes['name'] or '\r' in changes['name']):
            raise ValueError('Название: 1–70 символов')
        if 'name' in changes:
            changes['name'] = changes['name'].strip()
        if 'strategy' in changes and changes['strategy'] not in STRATEGIES:
            raise ValueError('Неизвестный тип балансировщика')
        if 'scope' in changes and changes['scope'] not in SCOPES:
            raise ValueError('Недопустимая группа серверов')
        if 'fallback' in changes and not isinstance(changes['fallback'], str):
            raise ValueError('Некорректный fallback')
        if 'rules' in changes:
            rules = changes['rules']
            if not isinstance(rules, list) or len(rules) > 10000 or not all(
                isinstance(r, str) and 0 < len(r) <= 512 and _RULE.fullmatch(r) for r in rules):
                raise ValueError('Допустимы domain:, full:, geosite:, regexp:, keyword:, ip:, geoip:')
        if 'priority' in changes and (not isinstance(changes['priority'], int) or not -100000 <= changes['priority'] <= 100000):
            raise ValueError('Приоритет должен быть числом от -100000 до 100000')
        if 'is_default' in changes:
            changes['is_default'] = bool(changes['is_default'])
            if changes['is_default']:
                con.execute('UPDATE smart_balancers SET is_default=0 WHERE id<>?', (ident,))
        if 'enabled' in changes:
            changes['enabled'] = bool(changes['enabled'])
        if (changes.get('scope',match['scope']) == 'reserve') and changes.get('rules'):
            raise ValueError('Резервный пул не поддерживает ручные правила')
        if changes.get('scope') == 'reserve':
            changes['rules'] = []
        match.update(changes)
        if match['is_default'] and match['scope'] == 'reserve':
            raise ValueError('Резервный балансировщик не может быть основным')
        _validate_graph([dict(b, **changes) if b['id'] == ident else b for b in entries])
        for key, value in changes.items():
            con.execute('UPDATE smart_balancers SET ' + key + '=? WHERE id=?',
                        (json.dumps(value, ensure_ascii=False) if key == 'rules' else
                         int(value) if key in ('enabled', 'is_default') else value, ident))
            # Preserve legacy configuration API and the old options for rollback.
            if ident in ('gemini','other','all','reserve') and key in ('rules','enabled'):
                legacy_key = ('rules_' if key == 'rules' else 'enabled_') + ident
                con.execute('INSERT OR REPLACE INTO smart_options VALUES (?,?)',
                            (legacy_key, json.dumps(value, ensure_ascii=False)))
    return get(ident)


def delete(ident):
    if ident in {item[0] for item in LEGACY}:
        raise ValueError('Стандартные пулы нельзя удалить; их можно выключить')
    with database() as con:
        entries = _fetch(con)
        if ident not in {b['id'] for b in entries}:
            raise ValueError('Балансировщик не найден')
        if any(b['fallback'] == ident for b in entries):
            raise ValueError('Сначала измените fallback балансировщиков, ссылающихся на этот пул')
        con.execute('DELETE FROM smart_balancers WHERE id=?', (ident,))
        # Keep panel inbound IDs and unrelated memberships untouched.
        for node_id, pools in con.execute('SELECT id,pools FROM smart_nodes').fetchall():
            old = json.loads(pools)
            if ident in old:
                con.execute('UPDATE smart_nodes SET pools=? WHERE id=?',
                            (json.dumps([p for p in old if p != ident]), node_id))


def interval():
    with database() as con:
        row = con.execute('SELECT value FROM smart_options WHERE key=?', ('auto_probe_interval',)).fetchone()
    return json.loads(row[0]) if row else '1m'


def set_interval(value):
    if not isinstance(value, str) or not re.fullmatch(r'([1-9][0-9]*)(s|m|h)', value):
        raise ValueError('Укажите интервал, например 30s, 1m или 5m')
    n = int(value[:-1]) * {'s': 1, 'm':60, 'h':3600}[value[-1]]
    if not 10 <= n <= 3600:
        raise ValueError('Разрешён интервал от 10 секунд до 1 часа')
    with database() as con:
        con.execute('INSERT OR REPLACE INTO smart_options VALUES (?,?)',
                    ('auto_probe_interval', json.dumps(value)))
    return value


def split_rules(rules):
    domains, ips = [], []
    for raw in rules:
        value = raw.strip()
        if value.lower().startswith(('ip:', 'geoip:')):
            ips.append(value[3:] if value.lower().startswith('ip:') else value)
        elif value.lower().startswith('keyword:'):
            domains.append('keyword:' + value[8:])
        else:
            domains.append(value)
    return domains, ips
