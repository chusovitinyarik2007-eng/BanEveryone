"""Admin-managed, precompiled Xray VLESS group (separate from BASE AUTO).

All changes compile a complete JSON profile and commit it together with nodes
and settings to SQLite.  Subscription requests only read that snapshot, never
parse VLESS URIs or build routing.  The module contains no network calls.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from urllib.parse import parse_qsl, unquote, urlsplit

import classes

DEFAULT = {
    'enabled': False,
    'name': '🚀 Vless config — Smart Auto',
    'interval': '30s',
    'probe_url': 'https://www.gstatic.com/generate_204',
    'strategy': 'leastPing',
    'direct_rules': ['regexp:(^|\\.)ru$', 'domain:gosuslugi.ru', 'domain:vk.com',
                     'domain:yandex.ru', 'geoip:private'],
}
INTERVALS = ('10s', '15s', '30s', '45s', '1m', '2m', '5m', '10m', '30m', '1h')
STRATEGIES = ('leastPing', 'leastLoad', 'roundRobin', 'random')
RULE = re.compile(r'^(?:domain|full|geosite|regexp|keyword|ip|geoip):.+$', re.I)
MAX_NODES = 100
MAX_LINK = 8192
MAX_RULES = 500
# Version of the Xray layout stored in vless_config_settings.compiled.
# 3: fix invalid catch-all, use explicit outbound tags for balancer + observer,
#    and remove any duplicate outbound tags in old cached snapshots.
PROFILE_FORMAT_VERSION = 3


def _repair_compiled_routing(con):
    """Repair an existing cached Xray profile ONCE, without parsing VLESS URIs.

    The version marker avoids costly rebuilding on subscription requests.
    Updating this module upgrades old cache snapshots automatically.
    """
    con.execute("""CREATE TABLE IF NOT EXISTS vless_config_compiler_version
        (id INTEGER PRIMARY KEY CHECK (id=1), version INTEGER NOT NULL)""")
    con.execute('INSERT OR IGNORE INTO vless_config_compiler_version(id, version) VALUES (1,0)')
    version = con.execute('SELECT version FROM vless_config_compiler_version WHERE id=1').fetchone()[0]
    if version >= PROFILE_FORMAT_VERSION:
        return

    row = con.execute('SELECT compiled FROM vless_config_settings WHERE id=1').fetchone()
    if row and row[0]:
        profile = json.loads(row[0])
        if isinstance(profile, dict) and _repair_profile(profile):
            con.execute("""UPDATE vless_config_settings
                SET compiled=?, revision=revision+1 WHERE id=1""",
                (json.dumps(profile, ensure_ascii=False, separators=(',', ':')),))
    con.execute('UPDATE vless_config_compiler_version SET version=? WHERE id=1',
                (PROFILE_FORMAT_VERSION,))


def _repair_profile(profile):
    """Upgrade an old snapshot in place, with no network or URI conversion."""
    changed = False
    routing = profile.get('routing') or {}
    inbounds = profile.get('inbounds') or []
    inbound_tags = [i['tag'] for i in inbounds
                    if isinstance(i, dict) and isinstance(i.get('tag'), str) and i['tag']]
    if inbound_tags:
        for rule in routing.get('rules') or []:
            if (isinstance(rule, dict) and rule.get('type') == 'field'
                    and rule.get('balancerTag') == 'manual_balance'
                    and not any(rule.get(key) for key in
                                ('inboundTag', 'domain', 'ip', 'port', 'network',
                                 'protocol', 'source', 'sourcePort', 'user', 'attrs'))):
                rule['inboundTag'] = inbound_tags
                changed = True

    outputs = profile.get('outbounds') or []
    # Xray outbounds must have unique tags, including freedom / blackhole.
    seen = set()
    unique = []
    for output in outputs:
        if not isinstance(output, dict):
            unique.append(output)
            continue
        tag = output.get('tag')
        if isinstance(tag, str) and tag in seen:
            changed = True
            continue
        if isinstance(tag, str):
            seen.add(tag)
        unique.append(output)
    if len(unique) != len(outputs):
        profile['outbounds'] = unique

    node_tags = [o['tag'] for o in unique if isinstance(o, dict)
                 and isinstance(o.get('tag'), str)
                 and o['tag'].startswith('manual_node_')]
    for balancer in routing.get('balancers') or []:
        if isinstance(balancer, dict) and balancer.get('tag') == 'manual_balance':
            if balancer.get('selector') != node_tags:
                balancer['selector'] = node_tags
                changed = True
    for key in ('observatory', 'burstObservatory'):
        observer = profile.get(key)
        if isinstance(observer, dict) and observer.get('subjectSelector') != node_tags:
            observer['subjectSelector'] = node_tags
            changed = True
    return changed


@contextmanager
def _db():
    con = sqlite3.connect(classes.DB_PATH, timeout=15)
    try:
        with con:
            con.execute('''CREATE TABLE IF NOT EXISTS vless_config_settings
                (id INTEGER PRIMARY KEY CHECK (id=1), settings TEXT NOT NULL,
                 compiled TEXT, revision INTEGER NOT NULL DEFAULT 0)''')
            con.execute('''CREATE TABLE IF NOT EXISTS vless_config_nodes
                (id TEXT PRIMARY KEY, uri TEXT NOT NULL UNIQUE,
                 name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1)''')
            con.execute('INSERT OR IGNORE INTO vless_config_settings(id,settings,compiled,revision) VALUES (1,?,NULL,0)',
                        (json.dumps(DEFAULT, ensure_ascii=False),))
            _repair_compiled_routing(con)
            yield con
    finally:
        con.close()


def _load(con):
    raw, _compiled, rev = con.execute('SELECT settings,compiled,revision FROM vless_config_settings WHERE id=1').fetchone()
    return {**DEFAULT, **json.loads(raw)}, rev


def _nodes(con):
    return [dict(id=r[0], uri=r[1], name=r[2], enabled=bool(r[3]))
            for r in con.execute('SELECT id,uri,name,enabled FROM vless_config_nodes ORDER BY rowid')]


def parse_vless(uri: str) -> tuple[str, dict]:
    if not isinstance(uri, str) or not uri.startswith('vless://') or len(uri)>MAX_LINK:
        raise ValueError('Нужна корректная ссылка vless:// (не более 8192 символов)')
    try:
        u = urlsplit(uri)
        user = unquote(u.username or '')
        if u.password is not None: raise ValueError('Пароль в VLESS URI не поддерживается')
        uid = str(uuid.UUID(user))
        host, port = u.hostname, u.port
        if not host or not port or not 1 <= port <= 65535:
            raise ValueError('Не указан адрес или порт')
    except (ValueError, TypeError) as exc:
        raise ValueError('Неверные UUID, адрес или порт в ссылке VLESS') from exc
    params = {}
    for key, value in parse_qsl(u.query, keep_blank_values=True):
        if key in params:
            raise ValueError('Повторяющийся параметр VLESS: ' + key)
        params[key] = value

    # Prefer explicit failure to silently discard connection-critical options.
    known = {
        'type','security','encryption','flow','sni','serverName','fp','pbk','sid','spx',
        'alpn','allowInsecure','insecure','path','host','serviceName','mode','extra','headerType',
        'authority','seed','quicSecurity','key','fingerprint','show','pqv',
        'noGRPCHeader','noSSEHeader','xPaddingBytes','xPaddingObfsMode',
        'xmux','downloadSettings','scMaxEachPostBytes','scMinPostsIntervalMs',
        'scMaxBufferedPosts','maxConcurrency','maxConnections','maxReuseTimes',
        'h2','tls','ech','echConfigList','echForceQuery','echServerKeys',
    }
    unknown = set(params) - known
    if unknown:
        raise ValueError('Неподдерживаемые параметры VLESS (не будут потеряны молча): ' + ', '.join(sorted(unknown)))

    # Both `insecure` and `allowInsecure` are common share-link aliases.
    # False is Xray's secure default, so omit the deprecated core option.
    # Refuse true rather than emitting a config that disables certificate checks
    # (and may fail on newer Xray releases where allowInsecure was removed).
    insecure_values = []
    for option in ('insecure', 'allowInsecure'):
        if option not in params:
            continue
        value = params[option].strip().lower()
        if value in ('0', 'false', 'no', 'off'):
            insecure_values.append(False)
        elif value in ('1', 'true', 'yes', 'on'):
            insecure_values.append(True)
        else:
            raise ValueError(f'{option}: ожидается 0 или 1 (true или false)')
    if len(set(insecure_values)) > 1:
        raise ValueError('insecure и allowInsecure противоречат друг другу')
    if insecure_values and insecure_values[0]:
        raise ValueError(
            'insecure=1 отключает проверку TLS-сертификата и не поддерживается '
            'для безопасного импорта. Используйте ключ с проверяемым сертификатом.')

    network = params.get('type', 'tcp').lower()
    if network == 'raw':
        network = 'tcp'
    if network not in ('tcp','ws','grpc','xhttp','httpupgrade','http','kcp','quic'):
        raise ValueError('Пока не поддерживается транспорт: ' + network)
    security = params.get('security', 'none').lower()
    if security not in ('none','tls','reality'):
        raise ValueError('Неизвестный тип безопасности: ' + security)
    if params.get('encryption', 'none') != 'none':
        raise ValueError('VLESS Encryption требует отдельной поддержки; ссылка не сохранена')
    if security == 'reality' and not params.get('pbk'):
        raise ValueError('Для REALITY нужен pbk')
    if security == 'reality' and network not in ('tcp','grpc','xhttp'):
        raise ValueError('REALITY с этим транспортом требует отдельной проверки')
    if network in ('http','kcp','quic'):
        raise ValueError('Транспорт ' + network + ' пока не поддерживается конвертером без потери параметров')

    name = unquote(u.fragment or '')[:100].strip() or host
    flow = params.get('flow')
    user_data = {'id':uid, 'encryption':'none'}
    if flow: user_data['flow'] = flow
    stream = {'network': network, 'security': security}
    sni = params.get('sni') or params.get('serverName') or host
    fp = params.get('fp') or params.get('fingerprint')
    if security == 'tls':
        settings = {'serverName':sni}
        if fp: settings['fingerprint'] = fp
        if params.get('alpn'): settings['alpn'] = params['alpn'].split(',')
        # insecure=0 / allowInsecure=0 intentionally use secure Xray default.
        stream['tlsSettings'] = settings
    elif security == 'reality':
        settings = {'serverName':sni,'fingerprint':fp or 'chrome',
                    'publicKey':params['pbk'], 'shortId':params.get('sid',''),
                    'spiderX':params.get('spx','/')}
        if params.get('pqv'): settings['mldsa65Verify'] = params['pqv']
        stream['realitySettings'] = settings
    elif params.get('sni') or params.get('pbk'):
        raise ValueError('TLS/REALITY параметры переданы при security=none')

    path = params.get('path','/')
    host_header = params.get('host','')
    if network == 'ws':
        ws = {'path':path}
        if host_header: ws['headers'] = {'Host':host_header}
        stream['wsSettings'] = ws
    elif network == 'httpupgrade':
        h = {'path':path}
        if host_header: h['host'] = host_header
        stream['httpupgradeSettings'] = h
    elif network == 'grpc':
        g = {'serviceName':params.get('serviceName','')}
        if params.get('authority'): g['authority'] = params['authority']
        stream['grpcSettings'] = g
    elif network == 'xhttp':
        xh = {'path':path}
        if host_header: xh['host'] = host_header
        if params.get('mode'): xh['mode'] = params['mode']
        # Some exporters use `extra=null` (JSON null) to mean "no extra".
        # Genuine objects, including {}, are preserved without modification.
        raw_extra = params.get('extra', '').strip()
        if raw_extra and raw_extra.lower() != 'null':
            try:
                parsed = json.loads(raw_extra)
            except json.JSONDecodeError as exc:
                raise ValueError('xHTTP extra содержит некорректный JSON') from exc
            if not isinstance(parsed, dict):
                raise ValueError('xHTTP extra должен быть JSON-объектом или null')
            xh['extra'] = parsed
        stream['xhttpSettings'] = xh
    elif network == 'tcp' and params.get('headerType') not in (None,'none'):
        # Do not silently misconfigure unusual TCP header camouflage.
        raise ValueError('TCP headerType поддерживается только none')

    # Do not accept options we did not actually translate into Xray settings.
    ignored = set(params) - {'type','security','encryption','flow','sni','serverName','fp',
                             'fingerprint','pbk','sid','spx','pqv','alpn','allowInsecure','insecure',
                             'path','host','serviceName','authority','mode','extra','headerType'}
    if ignored:
        raise ValueError('Параметры требуют специальной поддержки: ' + ', '.join(sorted(ignored)))
    outbound = {'protocol':'vless', 'settings':{'vnext':[
        {'address':host,'port':port,'users':[user_data]}]}, 'streamSettings':stream}
    return name, outbound


def _validate(settings):
    name = settings.get('name')
    if not isinstance(name,str) or not (1 <= len(name.strip()) <= 100) or '\n' in name or '\r' in name:
        raise ValueError('Название должно иметь 1–100 символов в одной строке')
    if type(settings.get('enabled')) is not bool:
        raise ValueError('enabled должен быть true или false')
    if settings.get('interval') not in INTERVALS:
        raise ValueError('Недопустимый интервал проверок')
    if settings.get('strategy') not in STRATEGIES:
        raise ValueError('Неизвестная стратегия')
    url = settings.get('probe_url')
    if not isinstance(url,str) or not (10 <= len(url) <= 300) or urlsplit(url).scheme != 'https' or not urlsplit(url).hostname or urlsplit(url).username:
        raise ValueError('Адрес проверки должен быть HTTPS URL')
    rules = settings.get('direct_rules')
    if not isinstance(rules,list) or len(rules)>MAX_RULES or not all(isinstance(v,str) and 0<len(v)<=512 and RULE.fullmatch(v) for v in rules):
        raise ValueError('DIRECT: до 500 правил domain:, full:, geosite:, regexp:, keyword:, ip:, geoip:')


def _compile(settings, nodes):
    """One deterministic Xray profile. Internal source keys are not public nodes."""
    _validate(settings)
    active = [n for n in nodes if n['enabled']]
    if not active:
        return None
    outputs = []
    node_tags = []
    for node in active:
        _name, outbound = parse_vless(node['uri'])
        outbound['tag'] = 'manual_node_' + node['id']
        node_tags.append(outbound['tag'])
        outputs.append(outbound)
    outputs += [
        {'tag':'manual_direct','protocol':'freedom','settings':{}},
        {'tag':'manual_block','protocol':'blackhole','settings':{}},
    ]
    from routing_balancers import split_rules
    domains, ips = split_rules(settings['direct_rules'])
    # Xray requires at least one matcher on *every* field rule. A rule with
    # only `balancerTag` is NOT a catch-all: it fails at core startup with
    # 'this rule has no effective fields'. Match both proxy inbounds instead.
    inbound_tags = ['manual_socks', 'manual_http']
    rules = []
    if domains: rules.append({'type':'field', 'inboundTag':inbound_tags,
                              'domain':domains, 'outboundTag':'manual_direct'})
    if ips: rules.append({'type':'field', 'inboundTag':inbound_tags,
                          'ip':ips, 'outboundTag':'manual_direct'})
    rules.append({'type':'field', 'inboundTag':inbound_tags,
                  'balancerTag':'manual_balance'})
    config = {
        'remarks':settings['name'].strip(),
        'meta':{'serverDescription':'Vless config · балансировка вручную добавленных VLESS'},
        'inbounds':[
            {'tag':'manual_socks','listen':'127.0.0.1','port':10808,'protocol':'socks',
             'settings':{'auth':'noauth','udp':True},'sniffing':{'enabled':True,'destOverride':['http','tls']}},
            {'tag':'manual_http','listen':'127.0.0.1','port':10809,'protocol':'http','settings':{}},
        ],
        'outbounds':outputs,
        'routing':{'domainStrategy':'AsIs', 'balancers':[
            {'tag':'manual_balance','selector':node_tags,
             'strategy':{'type':settings['strategy']},'fallbackTag':'manual_block'}],
            'rules':rules},
    }
    if settings['strategy'] == 'leastLoad':
        config['burstObservatory'] = {
            'subjectSelector':node_tags,
            'pingConfig':{'destination':settings['probe_url'],'interval':settings['interval'],
                          'timeout':'5s','sampling':2},
        }
    else:
        config['observatory'] = {
            'enableConcurrency':True, 'probeInterval':settings['interval'],
            'probeUrl':settings['probe_url'], 'subjectSelector':node_tags,
        }
    return config


def _rebuild(con, settings):
    profile = _compile(settings, _nodes(con))
    stored = json.dumps(profile, ensure_ascii=False, separators=(',', ':')) if profile else None
    con.execute('UPDATE vless_config_settings SET settings=?,compiled=?,revision=revision+1 WHERE id=1',
                (json.dumps(settings, ensure_ascii=False), stored))


def state(include_secrets=False):
    with _db() as con:
        settings, revision = _load(con)
        nodes = _nodes(con)
        compiled_exists = con.execute('SELECT compiled IS NOT NULL FROM vless_config_settings WHERE id=1').fetchone()[0]
    if not include_secrets:
        nodes = [{k:v for k,v in n.items() if k!='uri'} for n in nodes]
    return {**settings,'revision':revision,'nodes':nodes,
            'compiled':bool(compiled_exists),'active_nodes':sum(n['enabled'] for n in nodes)}


def add_links(text):
    if not isinstance(text,str) or len(text)>100_000:
        raise ValueError('Слишком большой список')
    lines = [(i, line.strip()) for i, line in enumerate(text.splitlines(), 1)
             if line.strip()]
    if not lines:
        raise ValueError('Добавьте хотя бы одну VLESS-ссылку')
    # This editor only generates VLESS outbounds. Skip known other protocols
    # explicitly, report line numbers to the admin, and import the VLESS links.
    skipped = [{'line': i, 'reason': 'Не VLESS (ss://)'}
               for i, line in lines if line.lower().startswith('ss://')]
    vless_lines = [(i, line) for i, line in lines
                   if not line.lower().startswith('ss://')]
    if not vless_lines:
        raise ValueError('Нет VLESS-ссылок. ss:// не поддерживается этим генератором')
    if len(vless_lines) > MAX_NODES:
        raise ValueError('Максимум 100 VLESS-ссылок')
    parsed = []
    for lineno, line in vless_lines:
        try:
            parsed.append((line, parse_vless(line)[0]))
        except ValueError as exc:
            raise ValueError(f'Строка {lineno}: {exc}') from exc
    if len(set(x[0] for x in parsed)) != len(parsed):
        raise ValueError('Ссылки в одном запросе повторяются')
    with _db() as con:
        if len(_nodes(con)) + len(parsed) > MAX_NODES:
            raise ValueError('Максимум 100 серверов')
        for link,name in parsed:
            try:
                con.execute('INSERT INTO vless_config_nodes(id,uri,name,enabled) VALUES (?,?,?,1)',
                            (uuid.uuid4().hex[:16],link,name))
            except sqlite3.IntegrityError as exc:
                raise ValueError('Такой VLESS уже добавлен') from exc
        settings,_ = _load(con)
        _rebuild(con,settings)
    result = state()
    result['added_count'] = len(parsed)
    result['skipped'] = skipped
    return result


def update_node(ident, *, uri=None, name=None, enabled=None):
    with _db() as con:
        row = con.execute('SELECT uri,name,enabled FROM vless_config_nodes WHERE id=?',(ident,)).fetchone()
        if not row: raise ValueError('Сервер не найден')
        new_uri = row[0] if uri is None else uri.strip()
        parsed_name, _ = parse_vless(new_uri)
        new_name = row[1] if name is None else name.strip()
        if uri is not None and name is None: new_name = parsed_name
        if not new_name or len(new_name)>100 or '\n' in new_name or '\r' in new_name:
            raise ValueError('Имя сервера: 1–100 символов')
        if enabled is not None and type(enabled) is not bool:
            raise ValueError('enabled должен быть boolean')
        try:
            con.execute('UPDATE vless_config_nodes SET uri=?,name=?,enabled=? WHERE id=?',
                        (new_uri,new_name,int(bool(row[2]) if enabled is None else enabled),ident))
        except sqlite3.IntegrityError as exc:
            raise ValueError('Этот VLESS уже добавлен') from exc
        settings,_ = _load(con)
        _rebuild(con,settings)
    return state()


def delete_node(ident):
    with _db() as con:
        if not con.execute('DELETE FROM vless_config_nodes WHERE id=?',(ident,)).rowcount:
            raise ValueError('Сервер не найден')
        settings,_ = _load(con)
        _rebuild(con,settings)
    return state()


def clear_nodes():
    with _db() as con:
        con.execute('DELETE FROM vless_config_nodes')
        settings,_ = _load(con)
        _rebuild(con,settings)
    return state()


def update_settings(**changes):
    if not changes or set(changes)-set(DEFAULT):
        raise ValueError('Неизвестный параметр Vless config')
    with _db() as con:
        settings,_ = _load(con)
        settings.update(changes)
        _validate(settings)
        _rebuild(con,settings)
    return state()


def compiled_json():
    """Admin preview of the committed snapshot, even when distribution is off."""
    with _db() as con:
        return con.execute('SELECT compiled FROM vless_config_settings WHERE id=1').fetchone()[0]


def cached_json():
    """Read immutable, already-compiled profile; never regenerate per request."""
    with _db() as con:
        row = con.execute('SELECT settings,compiled FROM vless_config_settings WHERE id=1').fetchone()
    if not json.loads(row[0]).get('enabled'):
        return None
    return row[1]


def append_cached(content):
    """Append cached JSON only to a real JSON array of Xray profiles."""
    cached = cached_json()
    if not cached: return content
    try:
        parsed = json.loads(content)
        if not isinstance(parsed,list) or not all(isinstance(p,dict) for p in parsed):
            return content
        # Avoid duplicates if a prior cache snapshot appears upstream.
        if any(isinstance(p.get('routing'),dict) and any(
            isinstance(b,dict) and b.get('tag')=='manual_balance'
            for b in p['routing'].get('balancers',[])) for p in parsed):
            parsed = [p for p in parsed if not (isinstance(p.get('routing'),dict) and any(
                isinstance(b,dict) and b.get('tag')=='manual_balance'
                for b in p['routing'].get('balancers',[])))]
            prefix = json.dumps(parsed,ensure_ascii=False,separators=(',',':')).encode()
            return prefix[:-1] + (b',' if parsed else b'') + cached.encode() + b']'
        return content.rstrip()[:-1] + (b',' if parsed else b'') + cached.encode() + b']'
    except (ValueError,TypeError,UnicodeDecodeError):
        return content


def rest_active(rest_obj, *, restricted_ids=None, now_ms=None):
    """Current (not historic purchases) entitlement; fail closed on missing fields.

    Caller must have fetched the technical --rest identity from the panel.
    """
    obj = (rest_obj or {}).get('obj') or {}
    client = obj.get('client') or {}
    if not isinstance(client,dict) or not client or client.get('enable') is not True:
        return False
    if not str(client.get('subId') or '').strip():
        return False
    if ('expiryTime' not in client or 'totalGB' not in client or
            ('usedTraffic' not in obj and 'usedTraffic' not in client)):
        return False
    try:
        expiry = int(client.get('expiryTime') or 0)
        total = int(client.get('totalGB') or 0)
        used = int(obj.get('usedTraffic', client.get('usedTraffic', 0)) or 0)
    except (TypeError,ValueError):
        return False
    if expiry < 0 or total < 0 or used < 0:
        return False
    if expiry>0 and (int(time.time()*1000) if now_ms is None else now_ms)>=expiry:
        return False
    if total>0 and used>=total:
        return False
    if restricted_ids is not None:
        ids = {str(v) for v in obj.get('inboundIds') or []}
        if not ids.intersection(str(i) for i in restricted_ids):
            return False
    return True
