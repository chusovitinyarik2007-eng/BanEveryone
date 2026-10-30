"""Isolated AUTO admin surface; all state-changing routes require session + CSRF.
Telegram only approves a short-lived, browser-bound one-time challenge.
"""
import hmac
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.routing import APIRoute
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

import admin_auth as auth
import routing_balancers as rb
import smart_routing as smart
import xui_connection

class ProtectedRoute(APIRoute):
    """Check admin identity before FastAPI even parses/validates API payloads."""
    def get_route_handler(self):
        original = super().get_route_handler()

        async def secured(request: Request):
            if '/api/' in request.url.path:
                if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                    _write_access(request)
                    # Cap even bodies with an incorrect or absent Content-Length.
                    if len(await request.body()) > 65536:
                        raise HTTPException(413, 'Слишком большой запрос')
                else:
                    _session(request)
            return await original(request)
        return secured


router = APIRouter(route_class=ProtectedRoute)
ROOT = Path(__file__).resolve().parent
SESSION_COOKIE = 'auto_admin_session'
CHALLENGE_COOKIE = 'auto_admin_login'
HEADERS = {'Cache-Control':'private, no-store, no-cache, max-age=0',
           'Pragma':'no-cache', 'X-Content-Type-Options':'nosniff',
           'X-Frame-Options':'DENY', 'Referrer-Policy':'no-referrer',
           'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"}


class StrictData(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class CreateBalancer(StrictData):
    name: StrictStr = Field(min_length=1, max_length=70)
    scope: StrictStr = 'main'


class UpdateBalancer(StrictData):
    name: StrictStr | None = None
    scope: StrictStr | None = None
    strategy: StrictStr | None = None
    fallback: StrictStr | None = None
    rules: list[StrictStr] | None = Field(default=None, max_length=10000)
    enabled: StrictBool | None = None
    is_default: StrictBool | None = None
    priority: StrictInt | None = Field(default=None, ge=-100000, le=100000)


class SetNodes(StrictData):
    node_id: StrictStr
    enabled: StrictBool


class ChangeGlobal(StrictData):
    action: StrictStr
    value: StrictStr | StrictBool | list[StrictStr]


def secure_cookie(resp, name, value, max_age):
    # Allow browser sessions on both HTTP and HTTPS (TLS is enforced by the reverse proxy).
    resp.set_cookie(name, value, max_age=max_age, httponly=True, secure=False,
                    samesite='strict', path=router_prefix())


def router_prefix():
    # Router is mounted at /<subscription-prefix>/adm in main.py.
    import classes
    return '/' + classes.settings['sub'].strip('/') + '/adm'


def _same_origin(req):
    # Origin is a browser-provided CSRF signal; still require a session and
    # CSRF token. Never accept forwarded host/proto from an untrusted client.
    value = req.headers.get('origin', '')
    if not value:
        return False
    parsed = urlsplit(value)
    return (parsed.scheme in ('https','http')
            and parsed.netloc.lower() == req.headers.get('host', '').lower()
            and not parsed.username and not parsed.password
            and parsed.path == '' and not parsed.query and not parsed.fragment)


def _session(req):
    token = req.cookies.get(SESSION_COOKIE, '')
    result = auth.get_session(token)
    if not result:
        raise HTTPException(status_code=401, detail='Требуется авторизация', headers=HEADERS)
    return result


def _write_access(req):
    result = _session(req)
    if not _same_origin(req):
        raise HTTPException(status_code=403, detail='Неверный Origin', headers=HEADERS)
    csrf = req.headers.get('x-csrf-token', '')
    if not csrf or not hmac.compare_digest(result['csrf'], csrf):
        raise HTTPException(status_code=403, detail='Неверный CSRF-токен', headers=HEADERS)
    if int(req.headers.get('content-length', '0') or '0') > 65536:
        raise HTTPException(status_code=413, detail='Слишком большой запрос')
    return result


@router.get('', response_class=HTMLResponse)
@router.get('/', response_class=HTMLResponse, include_in_schema=False)
async def admin_page(req: Request):
    template = (ROOT / 'web_subscription' / 'templates' / 'auto_admin.html').read_text(encoding='utf-8')
    return HTMLResponse(template, headers=HEADERS)


@router.post('/auth/start')
async def begin(req: Request):
    if not _same_origin(req):
        raise HTTPException(403, 'Неверный Origin')
    import tgbot
    try:
        account = await tgbot.bot.get_me()
        if not account.username:
            raise RuntimeError('У бота нет username')
        # Use the direct peer address only; spoofed X-Forwarded-For is ignored.
        challenge = auth.new_challenge(req.client.host if req.client else 'unknown',
                                       req.headers.get('user-agent',''))
    except ValueError as exc:
        raise HTTPException(429, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, 'Не удалось связаться с Telegram-ботом') from exc
    response = JSONResponse({'url': f'https://t.me/{account.username}?start=adm_{challenge["id"]}',
                             'code':challenge['code'], 'expires_in':auth.CHALLENGE_TTL}, headers=HEADERS)
    secure_cookie(response, CHALLENGE_COOKIE,
                  challenge['id'] + '.' + challenge['verifier'], auth.CHALLENGE_TTL)
    return response


@router.post('/auth/status')
async def auth_status(req: Request):
    if not _same_origin(req):
        raise HTTPException(403, 'Неверный Origin')
    cookie = req.cookies.get(CHALLENGE_COOKIE, '')
    if '.' not in cookie or len(cookie) > 160:
        return JSONResponse({'status':'missing'}, headers=HEADERS)
    ident, verifier = cookie.split('.', 1)
    status, data = auth.claim(ident, verifier)
    response = JSONResponse({'status':status, 'expires_at':data['expires'] if data else None},
                            headers=HEADERS)
    if data:
        secure_cookie(response, SESSION_COOKIE, data['token'], auth.SESSION_TTL)
        response.delete_cookie(CHALLENGE_COOKIE, path=router_prefix(), secure=False, samesite='strict')
    elif status in ('invalid','expired','rejected'):
        response.delete_cookie(CHALLENGE_COOKIE, path=router_prefix(), secure=False, samesite='strict')
    return response


@router.get('/api/state')
async def state(req: Request):
    user = _session(req)
    # The panel is authoritative: never show stale rows kept in SQLite after
    # a rename/deletion. Keep pool assignments linked by immutable inbound ID.
    try:
        panel_rows = await xui_connection.get_inbounds_full()
        current_nodes = smart.panel_nodes(panel_rows)
    except Exception as exc:
        # Fail closed instead of silently falling back to obsolete cached nodes.
        raise HTTPException(502, 'Не удалось получить актуальные инбаунды 3x-ui', headers=HEADERS) from exc
    return JSONResponse({
        'admin_id': user['admin_id'], 'expires_at':user['expires'], 'csrf':user['csrf'],
        'enabled':smart.enabled(), 'auto_name':smart.auto_name(),
        'interval':rb.interval(), 'ru_rules':smart.pool_rules('ru'),
        'strategies':rb.STRATEGIES, 'scopes':rb.SCOPES,
        'balancers':rb.all_balancers(), 'nodes':current_nodes,
    }, headers=HEADERS)


@router.post('/api/nodes/refresh')
async def refresh(req: Request):
    _write_access(req)
    try:
        rows = await xui_connection.get_inbounds_full()
        entries = smart.panel_nodes(rows)
    except Exception as exc:
        raise HTTPException(502, 'Не удалось получить инбаунды 3x-ui') from exc
    return JSONResponse({'nodes':entries}, headers=HEADERS)


@router.post('/api/global')
async def global_update(req: Request, payload: ChangeGlobal):
    _write_access(req)
    try:
        if payload.action == 'enabled' and type(payload.value) is bool:
            smart.set_option('enabled', payload.value)
        elif payload.action == 'name' and isinstance(payload.value, str):
            smart.set_auto_name(payload.value if payload.value != '-' else None)
        elif payload.action == 'interval' and isinstance(payload.value, str):
            rb.set_interval(payload.value)
        elif payload.action == 'ru_rules' and isinstance(payload.value, list):
            # Global RU direct matches domain expressions only.
            if len(payload.value) > 10000 or not all(
                isinstance(r, str) and 0 < len(r) <= 512 and
                rb._RULE.fullmatch(r) and r.lower().startswith(('domain:','full:','geosite:','regexp:','keyword:'))
                for r in payload.value):
                raise ValueError('RU DIRECT: допустимы только доменные правила Xray')
            smart.set_option('rules_ru', payload.value)
        else:
            raise ValueError('Неизвестная настройка или тип')
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {'ok':True}


@router.post('/api/balancers')
async def create(req: Request, payload: CreateBalancer):
    _write_access(req)
    try:
        ident = rb.create(payload.name, payload.scope)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {'id':ident}


@router.patch('/api/balancers/{ident}')
async def update(req: Request, ident: str, payload: UpdateBalancer):
    _write_access(req)
    values = payload.model_dump(exclude_unset=True)
    if not values or any(v is None for v in values.values()):
        raise HTTPException(400, 'Укажите изменения')
    try:
        return rb.update(ident, **values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete('/api/balancers/{ident}')
async def delete(req: Request, ident: str):
    _write_access(req)
    try:
        rb.delete(ident)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {'ok':True}


@router.put('/api/balancers/{ident}/nodes')
async def change_node(req: Request, ident: str, payload: SetNodes):
    _write_access(req)
    try:
        # Sync with current panel; don't let a caller assign a removed or fake inbound.
        rows = await xui_connection.get_inbounds_full()
        valid = {n['id'] for n in smart.panel_nodes(rows)}
        if payload.node_id not in valid:
            raise ValueError('Инбаунд отсутствует в панели')
        result = smart.set_node_membership(payload.node_id, ident, payload.enabled)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, 'Ошибка получения списка инбаундов') from exc
    return {'pools':result}


@router.post('/api/logout')
async def logout(req: Request):
    _write_access(req)
    auth.revoke(req.cookies.get(SESSION_COOKIE))
    resp = JSONResponse({'ok':True}, headers=HEADERS)
    resp.delete_cookie(SESSION_COOKIE, path=router_prefix(), secure=False, samesite='strict')
    resp.delete_cookie(CHALLENGE_COOKIE, path=router_prefix(), secure=False, samesite='strict')
    return resp
