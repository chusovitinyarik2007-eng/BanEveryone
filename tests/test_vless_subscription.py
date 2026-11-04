"""Integration: append Vless config ONLY at end of eligible JSON subscriptions."""
import asyncio
import importlib
import json
import os
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import classes
import vless_config as vc

UUID = '123e4567-e89b-42d3-a456-426614174001'
LINK = f'vless://{UUID}@de.example:443?security=reality&pbk=PUBLIC&fp=chrome#Germany'


def full(expire=None, used=1, total=100, enabled=True, ids=None):
    return {'obj':{
        'client':{'enable':enabled,'subId':'id-rest','expiryTime':expire if expire is not None else int(time.time()*1000)+600000,'totalGB':total},
        'inboundIds':[5] if ids is None else ids,
        'usedTraffic':used,
    }}


class SubAppTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        old_db=classes.DB_PATH;classes.DB_PATH=os.path.join(tmp.name,'new.db')
        self.addCleanup(setattr,classes,'DB_PATH',old_db)
        old_restricted=classes.settings.get('restricted_inbounds')
        classes.settings['restricted_inbounds']=[5]
        self.addCleanup(lambda: classes.settings.__setitem__('restricted_inbounds',old_restricted) if old_restricted is not None else classes.settings.pop('restricted_inbounds',None))
        old_url=classes.settings.get('XUI_SUB_URL');classes.settings['XUI_SUB_URL']='https://panel.test/sub/json/{sub_id}'
        self.addCleanup(lambda: classes.settings.__setitem__('XUI_SUB_URL',old_url) if old_url is not None else classes.settings.pop('XUI_SUB_URL',None))
        vc.add_links(LINK);vc.update_settings(enabled=True)
        stub_bot=types.ModuleType('tgbot')
        stub_bot.sendmsg_admin=AsyncMock()
        stub_backup=types.ModuleType('backup_db');stub_backup.backup_schedule=AsyncMock()
        stub_userbot=types.ModuleType('userBot.bot');stub_userbot.run_bot=AsyncMock()
        self.patch_modules=patch.dict(sys.modules,{'tgbot':stub_bot,'backup_db':stub_backup,'userBot.bot':stub_userbot})
        self.patch_modules.start();self.addCleanup(self.patch_modules.stop)
        import main
        self.appmod=main
        classes.settings['XUI_SUB_URL']='https://panel.test/sub/json/{sub_id}'
        classes.settings['restricted_inbounds']=[5]
        self.primary=[{'remarks':'Germany 3x-ui','inbounds':[{'tag':'socks'}],'outbounds':[{'protocol':'vless','settings':{'vnext':[]}}]}]
        self.rest=[{'remarks':'Anti 3x-ui','inbounds':[{'tag':'socks'}],'outbounds':[{'protocol':'vless','settings':{'vnext':[]}}]}]

    def run_fetch(self, rest_info, *, rest_status=200, json_mode=True):
        first=httpx.Response(200,content=json.dumps(self.primary).encode() if json_mode else b'vless://example\n',
                             headers={'content-type':'application/json' if json_mode else 'text/plain'})
        second=httpx.Response(rest_status,content=json.dumps(self.rest).encode(),headers={'content-type':'application/json'})
        class FakeClient:
            calls=0
            async def __aenter__(self):return self
            async def __aexit__(self,*a):return None
            async def get(self,url,headers=None):
                self.calls+=1
                return first if self.calls==1 else second
        async def rest_sub_id(_id):return 'id-rest'
        async def inbounds():return []
        def fake_auto(content,primary,rest=None,has_reserve=False):
            try:
                arr=json.loads(content)
                if isinstance(arr,list):
                    return json.dumps([{'remarks':'BASE AUTO'},*arr]).encode()
            except ValueError:pass
            return content
        patches=[
            patch.object(self.appmod.httpx,'AsyncClient',return_value=FakeClient()),
            patch.object(importlib.import_module('subscription_service'),'get_rest_sub_id',rest_sub_id),
            patch.object(self.appmod.xui_connection,'get_inbounds_full',inbounds),
            patch.object(self.appmod.smart_routing,'sync_panel_inbounds',return_value=None),
            patch.object(self.appmod.smart_routing,'link_profiles_to_inbounds',return_value=None),
            patch.object(self.appmod.smart_routing,'remember',return_value=None),
            patch.object(self.appmod.smart_routing,'append_auto',side_effect=fake_auto),
            patch.object(vc,'parse_vless',side_effect=AssertionError('no recompile on fetch')),
        ]
        from contextlib import ExitStack
        request=types.SimpleNamespace(headers={'user-agent':'Happ/2.0'})
        with ExitStack() as stack:
            for p in patches:stack.enter_context(p)
            return asyncio.run(self.appmod.fetch_real_sub('SUB',request,2,1,100,1,rest_info=rest_info))

    def test_added_last_for_active_rest_and_json(self):
        out=self.run_fetch(full())
        items=json.loads(out.body)
        self.assertEqual([x['remarks'] for x in items],['BASE AUTO','Germany 3x-ui','Anti 3x-ui',vc.DEFAULT['name']])
        self.assertEqual(items[-1]['routing']['balancers'][0]['strategy']['type'],'leastPing')

    def test_no_custom_for_nonjson_or_invalid_rest(self):
        for kw in (
            {'rest_info':full(), 'json_mode':False},
            {'rest_info':full(enabled=False)},
            {'rest_info':full(expire=int(time.time()*1000)-1)},
            {'rest_info':full(used=100)},
            {'rest_info':full(ids=[99])},
            {'rest_info':{}},
            {'rest_info':full(), 'rest_status':404},
        ):
            with self.subTest(kw=kw):
                resp=self.run_fetch(**kw)
                self.assertNotIn('manual_balance',resp.body.decode())


if __name__=='__main__':unittest.main()
