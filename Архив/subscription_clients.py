"""Separate browser navigation from supported subscription clients.

Do not use X-HWID as a discriminator: an embedded browser may inherit it.
User-Agent/X-Client can indicate format preferences, not authenticate users.
"""
from collections.abc import Mapping


def classify(headers: Mapping[str, str]) -> str:
    """Return 'browser', 'happ', 'incy', or 'unsupported'.

    First honor *navigation intent*, then supported programmatic clients.
    An app's in-app browser must not receive raw credentials because it uses
    the enclosing app's User-Agent or X-HWID header.
    """
    h = {str(k).lower(): str(v).lower() for k, v in headers.items()}
    ua = h.get('user-agent', '')
    accept = h.get('accept', '')
    dest = h.get('sec-fetch-dest', '')
    # Explicit document navigation trumps app fingerprints and HWID.
    if (dest in ('document', 'iframe') or h.get('sec-fetch-mode') == 'navigate'
            or h.get('upgrade-insecure-requests') == '1'
            or 'text/html' in accept or 'application/xhtml+xml' in accept):
        return 'browser'
    client_hint = h.get('x-client', '').strip()
    if client_hint == 'incy' or ua.startswith('incy/'):
        return 'incy'
    if client_hint == 'happ' or 'happ' in ua:
        return 'happ'
    # Real browsers occasionally omit Accept. They should still see a page.
    if any(marker in ua for marker in ('mozilla/', 'safari/', 'chrome/', 'firefox/', 'edg/', 'opera/')):
        return 'browser'
    return 'unsupported'


NO_CACHE = {
    'Cache-Control': 'private, no-store, no-cache, max-age=0, must-revalidate',
    'Pragma': 'no-cache',
    'Expires': '0',
    'Surrogate-Control': 'no-store',
    'CDN-Cache-Control': 'no-store',
    'X-Accel-Expires': '0',
    'Vary': 'Accept, User-Agent, X-Client, Sec-Fetch-Dest',
    'X-Content-Type-Options': 'nosniff',
}
