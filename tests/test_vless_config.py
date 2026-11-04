"""Regression/security tests for the precompiled manual VLESS profile."""
import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient

import classes
import admin_auth
import admin_web
import vless_config as vc

UUID1 = '123e4567-e89b-42d3-a456-426614174001'
UUID2 = '123e4567-e89b-42d3-a456-426614174002'
LINK1 = f'vless://{UUID1}@de.example:443?security=reality&type=tcp&pbk=PUBKEY&sid=abcd&fp=chrome&sni=www.example.com#Germany'
LINK2 = f'vless://{UUID2}@fr.example:443?type=ws&security=tls&sni=fr.example&host=cdn.example&path=%2Fws#France'


class VlessConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        previous = classes.DB_PATH
        classes.DB_PATH = os.path.join(self.tmp.name,'test.sqlite')
        self.addCleanup(setattr,classes,'DB_PATH',previous)

    def test_vless_parsing_and_balancer(self):
        st = vc.add_links(LINK1+'\n'+LINK2)
        self.assertEqual(len(st['nodes']),2)
        self.assertEqual(st['active_nodes'],2)
        vc.update_settings(enabled=True)
        profile=json.loads(vc.cached_json())
        self.assertEqual(profile['routing']['balancers'][0]['strategy']['type'],'leastPing')
        expected_tags = [o['tag'] for o in profile['outbounds'] if o['protocol']=='vless']
        self.assertEqual(profile['routing']['balancers'][0]['selector'], expected_tags)
        self.assertEqual(profile['observatory']['subjectSelector'], expected_tags)
        self.assertEqual(len(expected_tags), 2)
        self.assertEqual(len({o['tag'] for o in profile['outbounds']}), len(profile['outbounds']))
        self.assertEqual(len([o for o in profile['outbounds'] if o['protocol']=='vless']),2)
        self.assertEqual(profile['outbounds'][0]['streamSettings']['realitySettings']['publicKey'],'PUBKEY')
        self.assertEqual(profile['outbounds'][1]['streamSettings']['wsSettings']['headers']['Host'],'cdn.example')
        self.assertEqual(profile['routing']['rules'][-1]['balancerTag'],'manual_balance')
        self.assertEqual(profile['routing']['rules'][-1]['inboundTag'],
                         ['manual_socks', 'manual_http'])
        # Xray refuses any `type: field` rule that has no match criteria.
        for rule in profile['routing']['rules']:
            self.assertTrue(any(rule.get(key) for key in
                                ('inboundTag', 'domain', 'ip', 'port', 'network')),
                            msg=f'Xray routing rule lacks matcher: {rule}')
        self.assertIn('outboundTag',profile['routing']['rules'][0])
        self.assertEqual(profile['routing']['rules'][0]['outboundTag'],'manual_direct')
        self.assertEqual(profile['routing']['balancers'][0]['fallbackTag'],'manual_block')

    def test_catchall_with_default_and_empty_direct_rules(self):
        vc.add_links(LINK1 + '\n' + LINK2)
        vc.update_settings(enabled=True, direct_rules=[])
        compiled = json.loads(vc.cached_json())
        self.assertEqual(compiled['routing']['rules'], [{
            'type': 'field',
            'inboundTag': ['manual_socks', 'manual_http'],
            'balancerTag': 'manual_balance',
        }])
        expected_tags = [o['tag'] for o in compiled['outbounds'] if o['protocol']=='vless']
        self.assertEqual(compiled['routing']['balancers'][0]['selector'], expected_tags)
        self.assertEqual(compiled['observatory']['subjectSelector'], expected_tags)
        self.assertEqual(compiled['observatory']['probeInterval'], '30s')
        vc.update_settings(strategy='leastLoad')
        compiled = json.loads(vc.cached_json())
        self.assertIn('burstObservatory', compiled)
        self.assertNotIn('observatory', compiled)
        self.assertEqual(compiled['routing']['rules'][-1]['inboundTag'],
                         ['manual_socks', 'manual_http'])

    def test_existing_bad_cached_config_upgraded_once_without_regeneration(self):
        vc.add_links(LINK1)
        vc.update_settings(enabled=True)
        old = json.loads(vc.cached_json())
        del old['routing']['rules'][-1]['inboundTag']
        # The deployed old generator used prefix-only selectors.
        old['routing']['balancers'][0]['selector'] = ['manual_node_']
        old['observatory']['subjectSelector'] = ['manual_node_']
        # Also verify that older snapshots with a duplicate utility tag recover.
        old['outbounds'].append({'tag':'manual_direct','protocol':'freedom','settings':{}})
        # Simulate an already deployed SQLite snapshot created by the old generator.
        with sqlite3.connect(classes.DB_PATH) as con:
            con.execute('UPDATE vless_config_settings SET compiled=? WHERE id=1',
                        (json.dumps(old, ensure_ascii=False),))
            con.execute('UPDATE vless_config_compiler_version SET version=0 WHERE id=1')
        revision_before = vc.state()['revision']
        # The first read upgraded the snapshot once. URI conversion is not run.
        # Reset again to exercise the first-read migration inside cached_json().
        with sqlite3.connect(classes.DB_PATH) as con:
            con.execute('UPDATE vless_config_settings SET compiled=?,revision=revision-1 WHERE id=1',
                        (json.dumps(old, ensure_ascii=False),))
            con.execute('UPDATE vless_config_compiler_version SET version=0 WHERE id=1')
        with patch.object(vc, 'parse_vless', side_effect=AssertionError('regenerated URIs!')):
            new = json.loads(vc.cached_json())
            self.assertEqual(new['routing']['rules'][-1]['inboundTag'],
                             ['manual_socks', 'manual_http'])
            expected_tags = [o['tag'] for o in new['outbounds'] if o['protocol']=='vless']
            self.assertEqual(new['routing']['balancers'][0]['selector'], expected_tags)
            self.assertEqual(new['observatory']['subjectSelector'], expected_tags)
            self.assertEqual(len(new['outbounds']),len({o['tag'] for o in new['outbounds']}))
            cached_twice = vc.cached_json()
        self.assertEqual(cached_twice, json.dumps(new, ensure_ascii=False, separators=(',', ':')))
        with sqlite3.connect(classes.DB_PATH) as con:
            version = con.execute('SELECT version FROM vless_config_compiler_version WHERE id=1').fetchone()[0]
            rev = con.execute('SELECT revision FROM vless_config_settings WHERE id=1').fetchone()[0]
        self.assertEqual(version, vc.PROFILE_FORMAT_VERSION)
        self.assertEqual(rev, revision_before)

    def test_large_explicit_balancer_matches_every_active_node(self):
        # The balancer used to contain a one-element PREFIX selector. Verify
        # that every real outbound appears explicitly in both selector pools.
        many = '\n'.join(
            f'vless://{UUID1}@node{i}.example:443?security=reality&pbk=PUB&fp=chrome#Node{i}'
            for i in range(32)
        )
        vc.add_links(many)
        vc.update_settings(enabled=True)
        profile = json.loads(vc.cached_json())
        tags = [o['tag'] for o in profile['outbounds'] if o['protocol']=='vless']
        self.assertEqual(len(tags), 32)
        self.assertEqual(len(tags), len(set(tags)))
        self.assertEqual(profile['routing']['balancers'][0]['selector'], tags)
        self.assertEqual(profile['observatory']['subjectSelector'], tags)
        self.assertEqual(len(profile['outbounds']), len({o['tag'] for o in profile['outbounds']}))
        self.assertEqual(profile['routing']['rules'][-1], {
            'type':'field', 'inboundTag':['manual_socks','manual_http'],
            'balancerTag':'manual_balance'})
        # Removing members must update both pools, but reading must not rebuild.
        first_id = vc.state()['nodes'][0]['id']
        vc.update_node(first_id, enabled=False)
        profile = json.loads(vc.cached_json())
        tags = [o['tag'] for o in profile['outbounds'] if o['protocol']=='vless']
        self.assertEqual(len(tags), 31)
        self.assertEqual(profile['routing']['balancers'][0]['selector'], tags)
        self.assertEqual(profile['observatory']['subjectSelector'], tags)
        with patch.object(vc,'parse_vless',side_effect=AssertionError('must read cache')):
            self.assertEqual(json.loads(vc.cached_json()), profile)

    def test_xhttp_and_unicode_and_unsupported(self):
        link=f'vless://{UUID1}@de.example:443?security=reality&pbk=PUB&type=xhttp&mode=auto&path=%2Fx&extra='+quote('{"noSSEHeader":true}')+'#Тест'
        name,out=vc.parse_vless(link)
        self.assertEqual(name,'Тест')
        self.assertEqual(out['streamSettings']['xhttpSettings']['extra'],{'noSSEHeader':True})
        with self.assertRaisesRegex(ValueError,'Неподдерживаемые'):
            vc.parse_vless(link.replace('#Тест','&futureAuth=abc#Тест'))
        with self.assertRaisesRegex(ValueError,'пока не поддерживается'):
            vc.parse_vless(f'vless://{UUID1}@de.example:443?type=kcp')
        with self.assertRaises(ValueError):
            vc.parse_vless('vless://bad@x:443')

    def test_atomic_bulk_and_cache_not_recompiled_on_read(self):
        vc.add_links(LINK1)
        rev=vc.state()['revision']
        with self.assertRaises(ValueError):vc.add_links(LINK2+'\nnot-vless://invalid')
        self.assertEqual(vc.state()['revision'],rev)
        self.assertEqual(len(vc.state()['nodes']),1)
        vc.update_settings(enabled=True,name='NEW PROFILE',interval='1m')
        cached=vc.cached_json()
        with patch.object(vc,'parse_vless',side_effect=AssertionError('built during request!')):
            self.assertEqual(vc.cached_json(),cached)
            a=vc.append_cached(json.dumps([{'remarks':'BASE'}]).encode())
        self.assertEqual([x['remarks'] for x in json.loads(a)],['BASE','NEW PROFILE'])
        self.assertEqual(json.loads(a)[1]['observatory']['probeInterval'],'1m')
        self.assertEqual(vc.append_cached(b'vless://not-a-json'),b'vless://not-a-json')
        self.assertEqual(vc.append_cached(json.dumps({'outbounds':[]}).encode()),json.dumps({'outbounds':[]}).encode())
        self.assertEqual(len(json.loads(vc.append_cached(a))),2)

    def test_disable_edit_clear(self):
        vc.add_links(LINK1+'\n'+LINK2)
        st=vc.state(include_secrets=True)
        ident=st['nodes'][0]['id']
        vc.update_settings(enabled=True)
        vc.update_node(ident,enabled=False,name='Отдельное имя')
        after_disable = json.loads(vc.cached_json())
        active_tags = [o['tag'] for o in after_disable['outbounds'] if o['protocol']=='vless']
        self.assertEqual(len(active_tags),1)
        self.assertEqual(after_disable['routing']['balancers'][0]['selector'], active_tags)
        self.assertEqual(after_disable['observatory']['subjectSelector'], active_tags)
        self.assertEqual(vc.state()['nodes'][0]['name'],'Отдельное имя')
        vc.delete_node(ident)
        self.assertEqual(len(vc.state()['nodes']),1)
        vc.update_settings(enabled=False)
        self.assertIsNone(vc.cached_json())
        vc.clear_nodes()
        self.assertFalse(vc.state()['compiled'])

    def test_rest_permission_checks(self):
        exp=int(time.time()*1000)+600_000
        o={'obj':{'client':{'enable':True,'subId':'rest-id','expiryTime':exp,'totalGB':100},
                  'usedTraffic':5,'inboundIds':[5]}}
        self.assertTrue(vc.rest_active(o,restricted_ids=['5']))
        self.assertFalse(vc.rest_active(o,restricted_ids=['6']))
        self.assertFalse(vc.rest_active({},restricted_ids=[5]))
        for change in ({'enable':False}, {'expiryTime':int(time.time()*1000)-1000}, {'subId':''}):
            c=json.loads(json.dumps(o));c['obj']['client'].update(change)
            self.assertFalse(vc.rest_active(c,restricted_ids=[5]))
        c=json.loads(json.dumps(o));c['obj']['usedTraffic']=100
        self.assertFalse(vc.rest_active(c,restricted_ids=[5]))
        c=json.loads(json.dumps(o));c['obj']['client']['totalGB']=0
        self.assertTrue(vc.rest_active(c,restricted_ids=[5]))  # unlimited quota


class VlessAdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        original_db=classes.DB_PATH
        classes.DB_PATH=os.path.join(self.tmp.name,'api.sqlite')
        self.addCleanup(setattr,classes,'DB_PATH',original_db)
        self.old_sub=classes.settings.get('sub')
        classes.settings['sub']='api-sub'
        self.addCleanup(lambda:classes.settings.__setitem__('sub',self.old_sub) if self.old_sub is not None else classes.settings.pop('sub',None))
        self.patch=patch.dict(os.environ,{'ADMINS':'12345'})
        self.patch.start();self.addCleanup(self.patch.stop)
        api=FastAPI();api.include_router(admin_web.router,prefix='/api-sub/adm')
        self.client=TestClient(api,base_url='https://testserver');self.addCleanup(self.client.close)
        self.root='/api-sub/adm'
        item=admin_auth.new_challenge('127.0.0.1','browser')
        self.assertTrue(admin_auth.decision(item['id'],12345,True))
        status,ses=admin_auth.claim(item['id'],item['verifier'])
        self.assertEqual(status,'approved')
        self.csrf=ses['csrf']
        self.client.cookies.set('auto_admin_session',ses['token'],path=self.root)

    def request(self,method,path,payload=None,token=True):
        headers={'Origin':'https://testserver'}
        if token:headers['X-CSRF-Token']=self.csrf
        return self.client.request(method,self.root+'/api/vless'+path,json=payload,headers=headers)

    def test_crud_and_csrf_and_json_preview(self):
        self.assertEqual(self.request('POST','/keys',{'links':LINK1},False).status_code,403)
        self.assertEqual(self.request('POST','/keys',{'links':LINK1},True).status_code,200)
        data=self.client.get(self.root+'/api/vless/state').json()
        self.assertEqual(len(data['nodes']),1)
        self.assertIn('uri',data['nodes'][0])
        ident=data['nodes'][0]['id']
        self.assertEqual(self.request('PATCH','/keys/'+ident,{'name':'new-name'}).status_code,200)
        self.assertEqual(self.request('PATCH','/keys/'+ident,{'enabled':'true'}).status_code,422)
        self.assertEqual(self.request('PATCH','/settings',{'enabled':True,'strategy':'leastPing'}).status_code,200)
        prof=self.client.get(self.root+'/api/vless/profile')
        self.assertEqual(prof.status_code,200)
        self.assertEqual(prof.json()['remarks'],vc.DEFAULT['name'])
        self.assertEqual(self.request('DELETE','/keys/'+ident,{}).status_code,200)
        self.assertEqual(self.client.get(self.root+'/api/vless/profile').status_code,404)
        self.assertEqual(self.request('DELETE','/keys',{}).status_code,200)
        self.assertEqual(self.request('POST','/keys',{'links':'bogus'}).status_code,400)

    def test_no_auth(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get(self.root+'/api/vless/state').status_code,401)
        self.assertEqual(self.request('POST','/keys',{'links':LINK1}).status_code,401)
        self.assertEqual(self.client.get(self.root+'/api/vless/profile').status_code,401)


if __name__=='__main__':unittest.main()
