"""Build an optional Xray JSON-only AUTO profile from 3x-ui subscription profiles.

The generated profile is independent of the original subscription nodes.
Only observed dead nodes trigger fallbacks (not arbitrary failed requests).
"""
import copy
import hashlib
import json
import sqlite3
import re
from contextlib import contextmanager

import classes
import routing_balancers as rb

POOL_NAMES = {"gemini": "✨ Gemini", "other": "🌍 Все сайты", "all": "🔄 Все серверы", "reserve": "🛟 Антиглушилка"}
DEFAULT_RULES = {
    "ru": ["regexp:(^|\\.)ru$", "regexp:(^|\\.)рф$", "domain:gosuslugi.ru", "domain:vk.com", "domain:mail.ru", "domain:yandex.ru"],
    "gemini": ["domain:gemini.google.com", "domain:generativelanguage.googleapis.com", "domain:aistudio.google.com", "domain:ai.google.dev"],
}
PROXY_PROTOCOLS = {"vless", "vmess", "trojan", "shadowsocks", "socks", "http", "wireguard", "hysteria", "hysteria2", "tuic", "anytls"}
AUTO_DEFAULT_NAME = "🔀 AUTO — умная маршрутизация"
AUTO_DEFAULT_DESCRIPTION = "RU → DIRECT · Gemini / Все сайты / Все серверы / Антиглушилка"


@contextmanager
def db():
    """Commit or roll back atomically and always close the SQLite handle."""
    con = sqlite3.connect(classes.DB_PATH)
    try:
        with con:
            con.execute("CREATE TABLE IF NOT EXISTS smart_options (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS smart_nodes (id TEXT PRIMARY KEY,name TEXT NOT NULL,pools TEXT NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS smart_aliases (alias TEXT PRIMARY KEY,node_id TEXT NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS smart_endpoint_map (endpoint_id TEXT PRIMARY KEY,node_id TEXT NOT NULL)")
            # One-time migration of the former RU pool. RU routing now uses
            # DIRECT; existing RU-assigned servers belong in the shared pool.
            # Migrate atomically before every read/write that depends on pools.
            marker = con.execute("SELECT 1 FROM smart_options WHERE key=?", ("migration_ru_to_all_v1",)).fetchone()
            if not marker:
                for ident, raw in con.execute("SELECT id,pools FROM smart_nodes").fetchall():
                    try:
                        pools = json.loads(raw)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(pools, list) or "ru" not in pools:
                        continue
                    pools = [p for p in pools if p != "ru"]
                    if "all" not in pools:
                        pools.append("all")
                    con.execute("UPDATE smart_nodes SET pools=? WHERE id=?", (json.dumps(pools), ident))
                con.execute("INSERT OR REPLACE INTO smart_options VALUES (?,?)",
                            ("migration_ru_to_all_v1", "true"))
            yield con
    finally:
        con.close()


def option(key, default=None):
    with db() as con:
        row = con.execute("SELECT value FROM smart_options WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_option(key, value):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO smart_options VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))
    if key.startswith(('rules_', 'enabled_')):
        suffix = key.split('_', 1)[1]
        if suffix in POOL_NAMES:
            rb.update(suffix, **{('rules' if key.startswith('rules_') else 'enabled'): value})


def auto_name():
    """Visible name of the generated AUTO profile; unrelated to panel inbounds."""
    configured = option("auto_name", AUTO_DEFAULT_NAME)
    return configured if isinstance(configured, str) and configured.strip() else AUTO_DEFAULT_NAME


def set_auto_name(name):
    """Persist an AUTO-only display name. None resets the default."""
    if name is None:
        with db() as con:
            con.execute("DELETE FROM smart_options WHERE key=?", ("auto_name",))
        return AUTO_DEFAULT_NAME
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100 or "\n" in name or "\r" in name:
        raise ValueError("Название AUTO должно содержать 1–100 символов в одной строке")
    clean = name.strip()
    set_option("auto_name", clean)
    return clean


def enabled():
    return bool(option("enabled", True))


def pool_enabled(pool):
    return bool(option("enabled_" + pool, True))


def pool_rules(pool):
    return option("rules_" + pool, DEFAULT_RULES.get(pool, []))


def nodes():
    with db() as con:
        rows = con.execute("SELECT id,name,pools FROM smart_nodes ORDER BY name").fetchall()
    return [{"id": row[0], "name": row[1], "pools": json.loads(row[2])} for row in rows]


def _endpoint_pairs(out):
    """Happ flat settings and standard Xray nested settings (no credentials)."""
    settings = out.get("settings") or {}
    if not isinstance(settings, dict):
        return []
    proto = str(out.get("protocol") or "").lower()
    if proto in ("vless", "vmess"):
        servers = settings.get("vnext") or []
    else:
        servers = settings.get("servers") or []
    pairs = [(str(s.get("address") or "").lower(), str(s.get("port") or ""))
             for s in servers if isinstance(s, dict) and s.get("address") and s.get("port")]
    if settings.get("address") and settings.get("port"):
        pairs.append((str(settings["address"]).lower(), str(settings["port"])))
    return sorted(set(pairs))


def node_key(out):
    """Identify a public endpoint without user UUIDs/passwords.

    Never hash an empty endpoint: that previously collapsed ALL Happ flat VLESS
    nodes into one ID and silently removed configured pools.
    """
    endpoint = {"protocol": str(out.get("protocol") or "").lower(),
                "addresses": _endpoint_pairs(out),
                "streamSettings": out.get("streamSettings") or {}}
    if not endpoint["addresses"]:
        return None
    # Stream options can contain incidental per-user values; endpoint address
    # and TLS host are sufficient to distinguish different advertised nodes.
    stream = endpoint.pop("streamSettings")
    endpoint["network"] = stream.get("network")
    endpoint["security"] = stream.get("security")
    for k in ("tlsSettings", "realitySettings"):
        if isinstance(stream.get(k), dict):
            endpoint[k] = {x: stream[k].get(x) for x in ("serverName", "publicKey", "shortId") if stream[k].get(x)}
    return hashlib.sha256(json.dumps(endpoint, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def read_profiles(content):
    try:
        value = json.loads(content)
    except (ValueError, UnicodeError, TypeError):
        return None
    if isinstance(value, list) and all(isinstance(x, dict) for x in value):
        return value
    if isinstance(value, dict) and isinstance(value.get("outbounds"), list):
        return [value]
    return None


def candidates(profiles, is_reserve=False):
    results = []
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        for outbound in profile.get("outbounds", []):
            if not isinstance(outbound, dict) or outbound.get("protocol") not in PROXY_PROTOCOLS:
                continue
            if not outbound.get("settings"):
                continue
            tag = str(outbound.get("tag") or "")
            if tag in ("direct", "block", "dns-out") or outbound.get("protocol") == "freedom":
                continue
            ident = node_key(outbound)
            if ident is None:
                continue
            name = str(profile.get("remarks") or outbound.get("tag") or ident)[:110]
            results.append((ident, name, copy.deepcopy(outbound), is_reserve))
    return results


def _profile_mapping_key(endpoint_id, name, reserve=False):
    """Distinguish separate panel profiles sharing the same endpoint.

    Keep the old endpoint-only mapping as a fallback for databases generated
    before this change. Scope by subscription to avoid main/--rest collisions.
    """
    source = "reserve" if reserve else "main"
    label = hashlib.sha256(_name_key(name).encode("utf-8")).hexdigest()[:16]
    return f"profile:{source}:{endpoint_id}:{label}"


def remember(profiles, reserve=False):
    """Do not infer panel identities from stale aliases or old endpoint IDs.

    Only link_profiles_to_inbounds(), which sees *both* current datasets,
    may write trusted mappings. A read of a subscription must never revive an
    obsolete association.
    """
    return len(candidates(profiles, reserve))


def _read_dict(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            result = json.loads(value)
            return result if isinstance(result, dict) else {}
        except ValueError:
            pass
    return {}


def _outbound_port_protocol(outbound):
    proto = str(outbound.get("protocol") or "").lower()
    return {(proto, int(port)) for _, port in _endpoint_pairs(outbound) if port.isdigit()}


def link_profiles_to_inbounds(profiles, inbounds, reserve=False):
    """Associate profiles with panel IDs only when correspondence is unique.

    Prefer an exact, unique remark match. Use (protocol, port) only if that
    pair occurs *exactly once on each side*; matching a single panel inbound
    to several profiles on port 443 used to put every node in one balancer.
    Never guess from a stale alias or an old endpoint-only mapping.

    Each refresh removes previous mappings for the currently supplied profiles
    before inserting verified matches, so changes fail closed, not into the
    wrong pool. The administrator's stored node memberships/rules are intact.
    """
    from collections import Counter, defaultdict

    profiles_list = candidates(profiles, reserve)
    panel_by_name = defaultdict(set)
    panel_by_pair = defaultdict(set)
    for inbound in inbounds:
        try:
            node_id = panel_key(inbound['id'])
        except (KeyError, ValueError, TypeError):
            continue
        panel_by_name[_name_key(inbound.get('remark'))].add(node_id)
        try:
            panel_by_pair[(str(inbound.get('protocol') or '').lower(),
                           int(inbound['port']))].add(node_id)
        except (KeyError, ValueError, TypeError):
            pass

    names = Counter(_name_key(name) for _, name, _, _ in profiles_list)
    pairs = Counter(pair for _, _, outbound, _ in profiles_list
                    for pair in _outbound_port_protocol(outbound))
    verified = []
    for ident, name, outbound, _ in profiles_list:
        normalized = _name_key(name)
        node_id = None
        name_matches = panel_by_name.get(normalized, set())
        if normalized and names[normalized] == 1 and len(name_matches) == 1:
            node_id = next(iter(name_matches))
        else:
            # Unique-on-both-sides fallback, never infer identity from a
            # shared standard port (443/80/etc).
            choices = set()
            for pair in _outbound_port_protocol(outbound):
                if pairs[pair] == 1 and len(panel_by_pair[pair]) == 1:
                    choices.update(panel_by_pair[pair])
            if len(choices) == 1:
                node_id = next(iter(choices))
        if node_id:
            verified.append((_profile_mapping_key(ident, name, reserve), node_id))

    with db() as con:
        for ident, name, _, _ in profiles_list:
            con.execute('DELETE FROM smart_endpoint_map WHERE endpoint_id=?',
                        (_profile_mapping_key(ident, name, reserve),))
            # Older format was not scoped and may be wrong or ambiguous.
            con.execute('DELETE FROM smart_endpoint_map WHERE endpoint_id=?', (ident,))
        # If two profiles resolve to the same panel node, do not accept either:
        # one inbound should not silently capture multiple distinct profiles.
        counts = Counter(node_id for _, node_id in verified)
        for profile_key, node_id in verified:
            if counts[node_id] == 1:
                con.execute('INSERT OR REPLACE INTO smart_endpoint_map VALUES (?,?)',
                            (profile_key, node_id))


def _merge_pools(a, b):
    result = []
    for pool in a + b:
        if pool in ("gemini", "other", "all", "reserve") and pool not in result:
            result.append(pool)
    return result


def migrate_legacy_nodes(con, valid):
    """Reconcile old duplicate endpoint records without discarding memberships."""
    rows = con.execute("SELECT id,name,pools FROM smart_nodes").fetchall()
    aliases = {}
    for ident, name, pools in rows:
        if ident in valid:
            aliases.setdefault(_name_key(name), []).append(ident)
    for ident, name, pools in rows:
        if ident in valid or ident.startswith("inbound:"):
            continue
        matches = aliases.get(_name_key(name), [])
        if len(matches) != 1:
            continue
        target = matches[0]
        old = con.execute("SELECT pools FROM smart_nodes WHERE id=?", (target,)).fetchone()
        try:
            merged = _merge_pools(json.loads(old[0]), json.loads(pools))
        except (ValueError, TypeError):
            continue
        con.execute("UPDATE smart_nodes SET pools=? WHERE id=?", (json.dumps(merged), target))
        con.execute("INSERT OR REPLACE INTO smart_endpoint_map VALUES (?,?)", (ident, target))
        con.execute("UPDATE smart_endpoint_map SET node_id=? WHERE node_id=?", (target, ident))
        con.execute("DELETE FROM smart_nodes WHERE id=?", (ident,))


def panel_key(inbound_id):
    return "inbound:" + str(int(inbound_id))


def _name_key(name):
    return re.sub(r"\s+", " ", str(name or "").strip()).casefold()


def sync_panel_inbounds(inbounds):
    """Authoritative unique choices come from panel inbound IDs, not user profiles."""
    seen = set()
    valid = []
    with db() as con:
        for inbound in inbounds:
            try:
                ident = panel_key(inbound["id"])
            except (TypeError, KeyError, ValueError):
                continue
            if ident in seen:
                continue
            seen.add(ident)
            name = str(inbound.get("remark") or f"Инбаунд #{inbound['id']}").strip()[:110]
            old = con.execute("SELECT name FROM smart_nodes WHERE id=?", (ident,)).fetchone()
            if old:
                # Keep aliases after renaming, but update the visible name.
                if old[0] != name:
                    con.execute("INSERT OR IGNORE INTO smart_aliases (alias,node_id) VALUES (?,?)", (_name_key(old[0]), ident))
                con.execute("UPDATE smart_nodes SET name=? WHERE id=?", (name, ident))
            else:
                con.execute("INSERT INTO smart_nodes VALUES (?,?,?)", (ident, name, '[]'))
            con.execute("INSERT OR REPLACE INTO smart_aliases VALUES (?,?)", (_name_key(name), ident))
            valid.append(ident)
        # Never merge old endpoint memberships into panel IDs by a matching name.
        # It can silently repopulate pools after an administrator clears them.
    return valid


def panel_nodes(inbounds):
    valid = set(sync_panel_inbounds(inbounds))
    return [n for n in nodes() if n['id'] in valid]


def _resolve_node_id(con, name, endpoint_id):
    name_key = _name_key(name)
    # A panel-ID match is authoritative. A remark can be reused by multiple
    # inbounds or change, so a name must never overwrite a verified mapping.
    mapped = con.execute("SELECT node_id FROM smart_endpoint_map WHERE endpoint_id=?", (endpoint_id,)).fetchone()
    if mapped and con.execute("SELECT 1 FROM smart_nodes WHERE id=?", (mapped[0],)).fetchone():
        return mapped[0]
    alias = con.execute("SELECT node_id FROM smart_aliases WHERE alias=?", (name_key,)).fetchone()
    if alias and con.execute("SELECT 1 FROM smart_nodes WHERE id=?", (alias[0],)).fetchone():
        return alias[0]
    # Support old saved memberships; prefer explicitly named panel inbound.
    existing = con.execute("SELECT id FROM smart_nodes WHERE id=?", (endpoint_id,)).fetchone()
    return existing[0] if existing else None


def toggle_node(ident, pool):
    if pool not in {b["id"] for b in rb.all_balancers()}:
        raise ValueError("Неизвестный пул")
    with db() as con:
        row = con.execute("SELECT pools FROM smart_nodes WHERE id=?", (ident,)).fetchone()
        if not row:
            raise ValueError("Сервер не найден")
        pools = json.loads(row[0])
        pools = [x for x in pools if x != pool] if pool in pools else pools + [pool]
        con.execute("UPDATE smart_nodes SET pools=? WHERE id=?", (json.dumps(pools), ident))
    return pools


# Preserve only the panel's general direct/blackhole rules. Proxy/balancer
# rules are intentionally excluded: AUTO supplies its own proxy selection.
RULE_CONDITIONS = ("domain", "ip", "port", "sourcePort", "network", "protocol",
                   "source", "sourceIP", "user", "attrs")


def _global_panel_rules(profiles, inbound_tags, fallback_tags=()):
    """Extract ordered DIRECT/BLOCK rules from the panel-issued profiles.

    Those rules are generally identical for every advertised node, but a
    profile without any rules must be a valid input. Extend matching
    DIRECT/BLOCK rules to active synthetic loopback inbounds so panel
    restrictions still win after any balancer fallback (including reserve).
    Do not copy proxy/balancer rules.
    """
    for profile in profiles:
        routing = profile.get("routing") or {}
        if not isinstance(routing, dict):
            continue
        original_rules = routing.get("rules") or []
        if not isinstance(original_rules, list):
            continue
        outbound_map = {
            ob.get("tag"): ob for ob in profile.get("outbounds", [])
            if isinstance(ob, dict) and isinstance(ob.get("tag"), str)
        }
        kept = []
        definitions = {}
        for original in original_rules:
            if not isinstance(original, dict) or original.get("type", "field") != "field":
                continue
            original_tag = original.get("outboundTag")
            if not isinstance(original_tag, str):
                continue
            target = outbound_map.get(original_tag, {})
            protocol = target.get("protocol")
            # Some panel versions omit an explicit outbound definition while
            # using the conventional 'direct' or 'block' tag.
            if protocol not in ("freedom", "blackhole"):
                if original_tag in ("direct", "auto_panel_direct") and not target:
                    protocol = "freedom"
                elif original_tag in ("block", "blackhole", "auto_panel_block") and not target:
                    protocol = "blackhole"
                else:
                    continue
            conditions = {k: copy.deepcopy(original[k]) for k in RULE_CONDITIONS if k in original}
            if "inboundTag" in original:
                requested = original["inboundTag"]
                if not isinstance(requested, list):
                    continue
                covered = [tag for tag in inbound_tags if tag in requested]
                if not covered:
                    continue
            else:
                covered = inbound_tags
            # At fallback, loopback re-enters routing under another inbound
            # tag. A panel rule limited to the original app inbounds would
            # otherwise be bypassed on that second routing pass.
            # We only widen rules which apply to at least one real inbound.
            applicable_tags = list(dict.fromkeys([*covered, *fallback_tags]))
            rewritten_tag = "auto_panel_direct" if protocol == "freedom" else "auto_panel_block"
            rewritten = {"type": "field", "inboundTag": applicable_tags, **conditions,
                         "outboundTag": rewritten_tag}
            kept.append(rewritten)
            if rewritten_tag not in definitions:
                definition = copy.deepcopy(target) if target else {"protocol": protocol, "settings": {}}
                definition["tag"] = rewritten_tag
                definitions[rewritten_tag] = definition
        if kept:
            return kept, list(definitions.values()), {
                "domainStrategy": routing.get("domainStrategy", "AsIs"),
                "domainMatcher": routing.get("domainMatcher", "hybrid")
            }
    return [], [], {}


def _panel_dns(profiles):
    """Copy an actual DNS block from a panel-issued JSON profile, untouched.

    Profiles may not all have a DNS section.  Never manufacture DNS servers,
    defaults, tags, or fallbacks: if none is present, omit the AUTO DNS field.
    """
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        panel_dns = profile.get("dns")
        if isinstance(panel_dns, dict) and panel_dns:
            return copy.deepcopy(panel_dns)
    return None


def build_auto(primary, rest=None, has_reserve=False):
    """Generate Xray AUTO from editable pool definitions and active panel nodes.

    Panel DIRECT/BLOCK are always first, including during each fallback
    loopback pass. An absent --rest never exposes reserve nodes to base users.
    """
    if not enabled():
        return None
    base = read_profiles(primary)
    extra = read_profiles(rest) if rest else None
    if not base:
        return None
    profiles = candidates(base) + (candidates(extra, True) if (has_reserve and extra) else [])
    if not profiles:
        return None
    definitions = [b for b in rb.all_balancers() if b['enabled']]
    if not definitions:
        return None
    configured = {n['id']: n['pools'] for n in nodes()}
    with db() as con:
        mapped = dict(con.execute("SELECT endpoint_id,node_id FROM smart_endpoint_map").fetchall())
    endpoint_owners = {}
    for ep_id, display_name, _, is_reserve in profiles:
        owner = mapped.get(_profile_mapping_key(ep_id, display_name, is_reserve))
        endpoint_owners.setdefault((is_reserve, ep_id), set()).add(owner)
    unique = {}
    memberships = {b['id']: [] for b in definitions}
    outputs = []
    for ident, name, outbound, is_rest in profiles:
        profile_key = _profile_mapping_key(ident, name, is_rest)
        resolved_id = mapped.get(profile_key)
        # An endpoint without a verified panel-inbound mapping has no pool
        # membership. Skip it BEFORE computing a collision-specific tag:
        # resolved_id is None for those endpoints, and calling .replace()
        # on it used to crash subscription generation.
        membership = configured.get(resolved_id, []) if resolved_id else []
        eligible = [b for b in definitions if b['scope'] == ('reserve' if is_rest else 'main')
                    and (not is_rest or has_reserve)]
        selected = [b['id'] for b in eligible if b['id'] in membership]
        if not selected:
            continue
        collision = len(endpoint_owners[(is_rest, ident)]) > 1
        scoped_ident = ("reserve_" if is_rest else "main_") + (
            ident + "_" + resolved_id.replace(":", "_") if collision else ident)
        tag = unique.get(scoped_ident)
        if tag is None:
            tag = 'auto_node_' + scoped_ident
            unique[scoped_ident] = tag
            outbound['tag'] = tag
            outputs.append(outbound)
        for pool in selected:
            if tag not in memberships[pool]:
                memberships[pool].append(tag)
    active = {p for p, members in memberships.items() if members}
    if not active or not any(b['scope'] == 'main' and b['id'] in active for b in definitions):
        return None
    active_defs = [b for b in definitions if b['id'] in active]
    all_definitions = {b['id']: b for b in rb.all_balancers()}
    def resolve_fallback(b):
        """Skip absent optional pools without creating dangling loopbacks."""
        target = b['fallback']
        visited = {b['id']}
        while target in all_definitions and target not in active and target not in visited:
            visited.add(target)
            target = all_definitions[target]['fallback']
        return target if target in active or target in ('direct', 'block', 'none') else 'block'

    destinations = {b['id']: resolve_fallback(b) for b in active_defs}
    # Only targets of actual fallback edges need a loopback inbound.
    fallback_targets = {target for target in destinations.values() if target in active}
    fallback_tags = {b['id']: 'auto_fallback_' + b['id'] for b in definitions if b['id'] in fallback_targets}
    # Each routed fallback re-enters the routing engine with its own inboundTag.
    for p in fallback_tags:
        outputs.append({'tag': 'auto_to_' + p, 'protocol': 'loopback',
                        'settings': {'inboundTag': fallback_tags[p]}})
    outputs.append({'tag': 'auto_exhausted', 'protocol': 'blackhole', 'settings': {}})
    balancers = []
    for b in active_defs:
        target = destinations[b['id']]
        # If an optional pool is absent, fail closed instead of referencing a
        # non-existent balancing target or routing into an infinite loop.
        fallback = ('auto_to_' + target if target in active
                    else 'auto_panel_direct' if target == 'direct'
                    else 'auto_exhausted')
        balancers.append({'tag': 'auto_lb_' + b['id'],
                          'selector': memberships[b['id']],
                          'strategy': {'type': b['strategy']},
                          'fallbackTag': fallback})
    template = base[0]
    inbounds = copy.deepcopy(template.get('inbounds') or [])
    tags = list(dict.fromkeys(x['tag'] for x in inbounds if isinstance(x, dict) and x.get('tag')))
    if not tags:
        return None
    panel_rules, panel_outbounds, panel_routing = _global_panel_rules(base, tags, list(fallback_tags.values()))
    # The panel has absolute priority over ALL new custom balancers/fallbacks.
    rules = list(panel_rules)
    if (pool_rules('ru') or any(target == 'direct' for target in destinations.values())) and not any(
            out['tag'] == 'auto_panel_direct' for out in outputs):
        outputs.append({'tag': 'auto_panel_direct', 'protocol': 'freedom', 'settings': {}})
    # Replace the default direct if panel defines a custom freedom outbound.
    for item in panel_outbounds:
        outputs[:] = [o for o in outputs if o['tag'] != item['tag']]
        outputs.append(item)
    for pool in reversed(list(fallback_tags)):
        rules.append({'type': 'field', 'inboundTag': [fallback_tags[pool]],
                      'balancerTag': 'auto_lb_' + pool})
    ru_domains = pool_rules('ru')
    if ru_domains:
        rules.append({'type': 'field', 'inboundTag': tags, 'domain': ru_domains,
                      "outboundTag": "auto_panel_direct"})
    # Specific rules are checked in ascending user-defined priority.
    # An empty rule list does not automatically match all traffic: only the
    # explicitly chosen default balancer is used for the final catchall.
    for b in active_defs:
        domains, ips = rb.split_rules(b['rules'])
        for kind, values in (('domain', domains), ('ip', ips)):
            if values:
                rules.append({'type': 'field', 'inboundTag': tags, kind: values,
                              'balancerTag': 'auto_lb_' + b['id']})
    default = next((b for b in active_defs if b['is_default'] and b['scope'] == 'main'), None)
    if default is None:
        default = next((b for b in active_defs if b['scope'] == 'main'), None)
    if default is None:
        return None
    rules.append({'type': 'field', 'inboundTag': tags, 'balancerTag': 'auto_lb_' + default['id']})
    config = {
        'remarks': auto_name(),
        'meta': {'serverDescription': auto_name() if option('auto_name') else AUTO_DEFAULT_DESCRIPTION},
        'inbounds': inbounds,
        'outbounds': outputs,
        'routing': {'domainStrategy': panel_routing.get('domainStrategy', 'AsIs'),
                    'domainMatcher': panel_routing.get('domainMatcher', 'hybrid'),
                    'balancers': balancers, 'rules': rules},
        'observatory': {'enableConcurrency': True, 'probeInterval': rb.interval(),
                        'probeUrl': 'https://www.gstatic.com/generate_204',
                        'subjectSelector': ['auto_node_']},
    }
    panel_dns = _panel_dns(base + (extra if has_reserve and extra else []))
    if panel_dns is not None:
        config['dns'] = panel_dns
    return config


def is_auto_profile(profile):
    """Recognise generated AUTO independent of its editable visible name.

    Needed when a subscription still includes an AUTO profile with an old
    remark: updates must replace it rather than append duplicate AUTO nodes.
    """
    if not isinstance(profile, dict):
        return False
    routing = profile.get("routing")
    if isinstance(routing, dict) and isinstance(routing.get("balancers"), list):
        if any(isinstance(b, dict) and str(b.get("tag", "")).startswith("auto_lb_")
               for b in routing["balancers"]):
            return True
    outbounds = profile.get("outbounds")
    if isinstance(outbounds, list) and any(
            isinstance(o, dict) and str(o.get("tag", "")).startswith("auto_node_")
            for o in outbounds):
        return True
    # Name alone is NOT enough: a real panel inbound may have this remark.
    # Panel profiles must always remain in the subscription unchanged.
    return False


def append_auto(content, primary, rest=None, has_reserve=False):
    profiles = read_profiles(content)
    if profiles is None:
        return content
    auto = build_auto(primary, rest, has_reserve)
    if not auto:
        return content
    # List is the 3x-ui JSON subscription style; dict is a single Xray config.
    if isinstance(json.loads(content), dict):
        return content  # cannot append another profile to a single object safely
    profiles = [p for p in profiles if not is_auto_profile(p)]
    profiles.insert(0, auto)
    return json.dumps(profiles, ensure_ascii=False, separators=(",", ":")).encode()


def set_node_membership(ident, pool, selected):
    """Idempotent update for web UI (unlike Telegram's toggle action)."""
    if type(selected) is not bool:
        raise ValueError('Некорректное состояние')
    if pool not in {b['id'] for b in rb.all_balancers()}:
        raise ValueError('Неизвестный балансировщик')
    with db() as con:
        row = con.execute('SELECT pools FROM smart_nodes WHERE id=?', (ident,)).fetchone()
        if not row:
            raise ValueError('Инбаунд не найден')
        pools = json.loads(row[0])
        if selected and pool not in pools:
            pools.append(pool)
        if not selected:
            pools = [p for p in pools if p != pool]
        con.execute('UPDATE smart_nodes SET pools=? WHERE id=?', (json.dumps(pools), ident))
    return pools
