"""Isolated authentication/security regression tests: no Telegram network required."""
import asyncio
import os
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import admin_auth
import admin_web
import classes
import routing_balancers as rb
import smart_routing as smart


class AdminWebSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.original_db = classes.DB_PATH
        self.addCleanup(setattr, classes, 'DB_PATH', self.original_db)
        classes.DB_PATH = os.path.join(self.tmp.name, 'db.sqlite')
        self.original_sub = classes.settings.get('sub')
        self.addCleanup(lambda: (classes.settings.__setitem__('sub', self.original_sub) if self.original_sub is not None else classes.settings.pop('sub', None)))
        classes.settings['sub'] = 'get-info-api'
        self.env = patch.dict(os.environ, {'ADMINS': '12345,67890'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.api = FastAPI()
        self.api.include_router(admin_web.router, prefix='/get-info-api/adm')
        self.client = TestClient(self.api, base_url='https://testserver')
        self.addCleanup(self.client.close)
        self.root = '/get-info-api/adm'
        async def empty_panel():
            return []
        self.panel_patch = patch.object(admin_web.xui_connection, 'get_inbounds_full', empty_panel)
        self.panel_patch.start(); self.addCleanup(self.panel_patch.stop)
        self.origin = {'Origin': 'https://testserver'}

    def post(self, endpoint, value=None, token=None, method='POST', headers=None):
        h = dict(self.origin)
        if token: h['X-CSRF-Token'] = token
        h.update(headers or {})
        return self.client.request(method, self.root+endpoint, json=value or {}, headers=h)

    def authorized(self):
        challenge = admin_auth.new_challenge('10.0.0.1','test-browser')
        self.assertTrue(admin_auth.decision(challenge['id'], 12345, True))
        status, session = admin_auth.claim(challenge['id'], challenge['verifier'])
        self.assertEqual(status, 'approved')
        self.client.cookies.set('auto_admin_session', session['token'], path=self.root)
        return session

    def test_access_denied_without_session_to_all_routes(self):
        self.assertEqual(self.client.get(self.root+'/api/state').status_code, 401)
        self.assertEqual(self.client.get(self.root+'/api/nodes/refresh').status_code, 405)
        for path, method in [('/api/global','POST'),('/api/balancers','POST'),
                            ('/api/balancers/gemini','PATCH'),('/api/balancers/gemini','DELETE'),
                            ('/api/balancers/gemini/nodes','PUT'),('/api/logout','POST'),
                            ('/api/nodes/refresh','POST')]:
            self.assertIn(self.post(path, {'action':'enabled','value':False}, method=method).status_code,
                          (401,), (path,method))
        self.assertTrue(smart.enabled())

    def test_only_approved_admin_can_create_browser_session(self):
        item=admin_auth.new_challenge('127.0.0.1','browser')
        self.assertIsNotNone(admin_auth.challenge_info(item['id']))
        self.assertFalse(admin_auth.decision(item['id'],888,True))
        self.assertEqual(admin_auth.claim(item['id'],'not the verifier')[0],'invalid')
        self.assertEqual(admin_auth.claim(item['id'],item['verifier'])[0],'pending')
        self.assertTrue(admin_auth.decision(item['id'],12345,True))
        self.assertEqual(admin_auth.claim(item['id'],'wrong verifier')[0],'invalid')
        status, session=admin_auth.claim(item['id'],item['verifier'])
        self.assertEqual(status,'approved')
        self.assertEqual(admin_auth.claim(item['id'],item['verifier'])[0],'invalid')
        self.assertFalse(admin_auth.decision(item['id'],12345,True))
        self.assertIsNotNone(admin_auth.get_session(session['token']))
        self.assertIsNone(admin_auth.get_session('wrong token'))

    def test_rejected_and_expired_challenges(self):
        item=admin_auth.new_challenge('127.0.0.1','browser')
        self.assertTrue(admin_auth.decision(item['id'],67890,False))
        self.assertEqual(admin_auth.claim(item['id'],item['verifier'])[0],'rejected')
        item2=admin_auth.new_challenge('127.0.0.1','browser')
        with admin_auth.database() as con:
            con.execute('UPDATE admin_web_challenges SET expires=? WHERE id=?',
                        (int(time.time()) - 2,item2['id']))
        self.assertEqual(admin_auth.claim(item2['id'],item2['verifier'])[0],'expired')

    def test_session_cannot_bypass_csrf_or_origin(self):
        token=self.authorized()
        self.assertEqual(self.post('/api/global',{'action':'enabled','value':False}).status_code,403)
        self.assertEqual(self.post('/api/global',{'action':'enabled','value':False},token='wrong').status_code,403)
        self.assertEqual(self.post('/api/global',{'action':'enabled','value':False},token=token['csrf'],
                                   headers={'Origin':'https://evil.test'}).status_code,403)
        self.assertTrue(smart.enabled())
        resp=self.post('/api/global',{'action':'enabled','value':False},token=token['csrf'])
        self.assertEqual(resp.status_code,200,resp.text)
        self.assertFalse(smart.enabled())
        self.assertEqual(self.post('/api/logout', token=token['csrf']).status_code,200)
        self.assertIsNone(admin_auth.get_session(token['token']))
        self.assertEqual(self.client.get(self.root+'/api/state').status_code,401)

    def test_expired_and_revoked_admin(self):
        t=self.authorized()
        with admin_auth.database() as con:
            con.execute('UPDATE admin_web_sessions SET expires=0')
        self.assertEqual(self.client.get(self.root+'/api/state').status_code,401)
        # Authorized user removed from ADMINS also loses access immediately.
        with admin_auth.database() as con:
            con.execute('UPDATE admin_web_sessions SET expires=?', (int(time.time())+3600,))
        with patch.dict(os.environ, {'ADMINS': '8888'}):
            self.assertEqual(self.client.get(self.root+'/api/state').status_code,401)

    def test_full_crud_and_inbound_assignment(self):
        t=self.authorized()
        csrf=t['csrf']
        self.assertEqual(self.client.get(self.root+'/api/state').status_code,200)
        created=self.post('/api/balancers',{'name':'Видео','scope':'main'},csrf)
        self.assertEqual(created.status_code,200,created.text)
        ident=created.json()['id']
        change=self.post('/api/balancers/'+ident,{'priority':-25,'strategy':'roundRobin',
                 'rules':['domain:video.test'], 'fallback':'direct'},csrf,method='PATCH')
        self.assertEqual(change.status_code,200,change.text)
        self.assertEqual(rb.get(ident)['priority'],-25)
        self.assertEqual(self.post('/api/balancers/'+ident,{'fallback':ident},csrf,method='PATCH').status_code,400)
        self.assertEqual(self.post('/api/balancers/'+ident,{'strategy':'injected'},csrf,method='PATCH').status_code,400)
        self.assertEqual(self.post('/api/balancers/'+ident,{'priority':0,'extra':'unknown'},csrf,method='PATCH').status_code,422)
        self.assertEqual(self.post('/api/balancers/'+ident,{'priority':'0'},csrf,method='PATCH').status_code,422)
        self.assertEqual(self.post('/api/balancers/'+ident,{'scope':'reserve','is_default':True},csrf,method='PATCH').status_code,400)
        self.assertEqual(self.post('/api/balancers/'+ident,{},csrf,method='DELETE').status_code,200)
        self.assertIsNone(rb.get(ident))

    def test_node_rejects_missing_inbound_and_idempotent(self):
        t=self.authorized()
        async def mock_inbounds():
            return [{'id':77,'remark':'Test 77','protocol':'vless','port':443}]
        with patch.object(admin_web.xui_connection,'get_inbounds_full',mock_inbounds):
            self.assertEqual(self.post('/api/nodes/refresh',token=t['csrf']).status_code,200)
            path='/api/balancers/gemini/nodes'
            self.assertEqual(self.post(path,{'node_id':'inbound:78','enabled':True},t['csrf'],method='PUT').status_code,400)
            self.assertEqual(self.post(path,{'node_id':'inbound:77','enabled':True},t['csrf'],method='PUT').status_code,200)
            self.assertEqual(self.post(path,{'node_id':'inbound:77','enabled':True},t['csrf'],method='PUT').status_code,200)
            self.assertEqual(smart.nodes()[0]['pools'].count('gemini'),1)

    def test_state_uses_live_panel_not_stale_sqlite_nodes(self):
        session = self.authorized()
        smart.sync_panel_inbounds([
            {'id': 11, 'remark': 'Старое имя', 'protocol': 'vless', 'port': 443},
            {'id': 12, 'remark': 'Удалённый', 'protocol': 'vless', 'port': 444},
        ])
        smart.set_node_membership('inbound:11', 'gemini', True)

        async def current_panel():
            return [{'id': 11, 'remark': 'Новое имя', 'protocol': 'vless', 'port': 443},
                    {'id': 13, 'remark': 'Новый сервер', 'protocol': 'vless', 'port': 445}]

        with patch.object(admin_web.xui_connection, 'get_inbounds_full', current_panel):
            response = self.client.get(self.root + '/api/state')
            self.assertEqual(response.status_code, 200, response.text)
            nodes = response.json()['nodes']
            self.assertEqual({n['id'] for n in nodes}, {'inbound:11', 'inbound:13'})
            self.assertEqual(next(n for n in nodes if n['id'] == 'inbound:11')['name'], 'Новое имя')
            self.assertIn('gemini', next(n for n in nodes if n['id'] == 'inbound:11')['pools'])
            self.assertNotIn('inbound:12', {n['id'] for n in nodes})
            self.assertEqual(self.post('/api/nodes/refresh', token=session['csrf']).json()['nodes'], nodes)

    def test_state_panel_failure_never_displays_cached_ghosts(self):
        self.authorized()
        smart.sync_panel_inbounds([{'id': 90, 'remark': 'Устаревший', 'protocol': 'vless', 'port': 443}])
        async def unavailable():
            raise RuntimeError('offline')
        with patch.object(admin_web.xui_connection, 'get_inbounds_full', unavailable):
            response = self.client.get(self.root + '/api/state')
        self.assertEqual(response.status_code, 502)

    def test_web_binds_approval_to_cookie(self):
        fake=types.ModuleType('tgbot')
        fake.bot=types.SimpleNamespace(get_me=self._bot_user)
        with patch.dict(sys.modules,{'tgbot':fake}):
            r=self.post('/auth/start',{})
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.cookies['auto_admin_login'])
        # A different browser without the binding cannot claim even if Telegram approved.
        cookie = self.client.cookies.get('auto_admin_login')
        ident=cookie.split('.',1)[0]
        self.assertTrue(admin_auth.decision(ident,12345,True))
        other=TestClient(self.api,base_url='https://testserver')
        try:
            self.assertEqual(other.post(self.root+'/auth/status',headers=self.origin).json()['status'],'missing')
            r=self.post('/auth/status',{})
            self.assertEqual(r.json()['status'],'approved')
            self.assertEqual(self.client.get(self.root+'/api/state').status_code,200)
            self.assertEqual(self.post('/auth/status',{}).json()['status'],'missing')
        finally:
            other.close()

    def test_http_cookie_login_and_logout(self):
        """Login must work over plain HTTP and still require Telegram admin approval."""
        self.assertEqual(admin_auth.CHALLENGE_TTL, 180)
        fake = types.ModuleType('tgbot')
        fake.bot = types.SimpleNamespace(get_me=self._bot_user)
        with TestClient(self.api, base_url='http://testserver') as browser:
            http_origin = {'Origin': 'http://testserver'}
            with patch.dict(sys.modules, {'tgbot': fake}):
                started = browser.post(self.root + '/auth/start', headers=http_origin)
            self.assertEqual(started.status_code, 200, started.text)
            self.assertEqual(started.json()['expires_in'], 180)
            self.assertIn('httponly', started.headers['set-cookie'].lower())
            self.assertNotIn('secure;', started.headers['set-cookie'].lower())
            self.assertEqual(browser.post(self.root + '/auth/status', headers=http_origin).json()['status'], 'pending')
            ident = browser.cookies.get('auto_admin_login').split('.', 1)[0]
            self.assertTrue(admin_auth.decision(ident, 12345, True))
            confirmed = browser.post(self.root + '/auth/status', headers=http_origin)
            self.assertEqual(confirmed.json()['status'], 'approved')
            self.assertNotIn('secure;', confirmed.headers['set-cookie'].lower())
            state = browser.get(self.root + '/api/state')
            self.assertEqual(state.status_code, 200, state.text)
            csrf = state.json()['csrf']
            logged_out = browser.post(self.root + '/api/logout', headers={**http_origin,'X-CSRF-Token':csrf}, json={})
            self.assertEqual(logged_out.status_code, 200, logged_out.text)
            self.assertEqual(browser.get(self.root + '/api/state').status_code, 401)

    def test_legacy_tables_are_preserved_by_auth_migration(self):
        import sqlite3
        with sqlite3.connect(classes.DB_PATH) as con:
            con.execute('CREATE TABLE smart_options (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            con.execute("INSERT INTO smart_options VALUES ('auto_name', '\"Keep Original\"')")
            con.execute('CREATE TABLE smart_nodes (id TEXT PRIMARY KEY, name TEXT NOT NULL, pools TEXT NOT NULL)')
            con.execute('INSERT INTO smart_nodes VALUES (?,?,?)', ('inbound:123', 'Keep Node', '["all"]'))
        admin_auth.new_challenge('127.0.0.1', 'browser')
        with sqlite3.connect(classes.DB_PATH) as con:
            self.assertEqual(con.execute("SELECT value FROM smart_options WHERE key='auto_name'").fetchone()[0],
                             '"Keep Original"')
            self.assertEqual(con.execute('SELECT pools FROM smart_nodes WHERE id=?', ('inbound:123',)).fetchone()[0],
                             '["all"]')

    def test_early_security_gate_and_request_size(self):
        self.assertEqual(self.post('/api/balancers', {'bad-field': 1}).status_code, 401)
        token=self.authorized()
        self.assertEqual(self.post('/api/global', {'action': 'enabled', 'value':False},
                                   token=token['csrf'], headers={'Origin': 'https://other.test'}).status_code,403)
        self.assertEqual(self.post('/api/global', {'action': 'name', 'value':'x'*70000},
                                   token=token['csrf']).status_code,413)
        self.assertTrue(smart.enabled())

    async def _bot_user(self):
        return types.SimpleNamespace(username='TestAutoBot')


if __name__=='__main__':unittest.main()
