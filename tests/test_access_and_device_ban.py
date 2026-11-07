import copy
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import classes
import db_driver
import bot_access
import device_bans
import smart_routing
import routing_balancers as rb
from subscription_service import _rest_client_from_base


class AccessAndBansTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        previous = classes.DB_PATH
        self.addCleanup(setattr, classes, 'DB_PATH', previous)
        classes.DB_PATH = os.path.join(self.dir.name, 'access.db')
        db_driver.init_db()
        with sqlite3.connect(classes.DB_PATH) as con:
            con.execute("INSERT INTO bot_visitors(tg_id) VALUES (1001)")
            con.execute("INSERT INTO Users(uuid,max_device,hwid,name,tg_id) VALUES ('sub1',2,'[]','test_user',1002)")
        bot_access.init()
        device_bans.init()

    def test_existing_users_grandfathered_not_new_user(self):
        self.assertEqual(bot_access.status(1001),'approved')
        self.assertEqual(bot_access.status(1002),'approved')
        self.assertEqual(bot_access.status(1003),'new')
        bot_access.init()
        self.assertEqual(bot_access.status(1003),'new')

    def test_request_atomic_dedup_and_revoke(self):
        self.assertEqual(bot_access.request_access(1003,'test'),'created')
        self.assertEqual(bot_access.request_access(1003,'test'),'pending')
        self.assertEqual(len(bot_access.list_users('pending')),1)
        self.assertTrue(bot_access.resolve(1003,'pending','approved'))
        self.assertFalse(bot_access.resolve(1003,'pending','approved'))
        self.assertEqual(bot_access.request_access(1003,'test'),'approved')
        bot_access.set_status(1003,'revoked')
        self.assertEqual(bot_access.request_access(1003,'test'),'created')
        bot_access.set_status(1003,'blocked')
        self.assertEqual(bot_access.request_access(1003,'test'),'blocked')

    def test_panel_restore_keeps_previous_disabled_clients_disabled(self):
        bot_access.remember_panel_client_state(1002,'a',True)
        bot_access.remember_panel_client_state(1002,'b',False)
        bot_access.remember_panel_client_state(1002,'a',False)
        self.assertEqual(bot_access.panel_client_states(1002), {'a':True,'b':False})
        bot_access.clear_panel_client_state(1002,'a')
        self.assertEqual(bot_access.panel_client_states(1002), {'b':False})

    def test_per_subscription_bans(self):
        device_bans.ban('sub1','hwid-123')
        device_bans.ban('sub1','hwid-123')
        self.assertTrue(device_bans.is_banned('sub1','hwid-123'))
        self.assertFalse(device_bans.is_banned('sub2','hwid-123'))
        self.assertEqual(len(device_bans.list_banned('sub1')),1)
        self.assertTrue(device_bans.unban('sub1','hwid-123'))
        self.assertFalse(device_bans.is_banned('sub1','hwid-123'))

    def test_rest_flow_forced(self):
        obj={'email':'test','id':'cred','subId':'sub-a','flow':''}
        result=_rest_client_from_base(obj,'test--rest')
        self.assertEqual(result['flow'],'xtls-rprx-vision')
        self.assertNotIn('subId',result)


class ReserveAutoTest(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        previous=classes.DB_PATH
        self.addCleanup(setattr,classes,'DB_PATH',previous)
        classes.DB_PATH=os.path.join(self.dir.name,'balancers.db')
        rb.all_balancers()
        smart_routing.set_option('enabled',True)

    def profile(self,host):
        return {'remarks':host,'inbounds':[{'tag':'mixed','protocol':'socks','port':10808}],
                'outbounds':[{'tag':'proxy','protocol':'vless',
                              'settings':{'address':host,'port':443,'id':'a'},
                              'streamSettings':{'network':'tcp','security':'reality'}}],
                'routing':{'rules':[]}}

    def test_reserve_auto_imports_duplicates_without_panel_mapping(self):
        main=[self.profile('a.example')]
        rest=[self.profile('b.example'),self.profile('b.example'),self.profile('c.example')]
        main_id=smart_routing.node_key(main[0]['outbounds'][0])
        with smart_routing.db() as con:
            # Current main mapping remains manually controlled.
            con.execute('INSERT INTO smart_nodes VALUES (?,?,?)',('inbound:1','Main','["other"]'))
            con.execute('INSERT INTO smart_endpoint_map(endpoint_id,node_id) VALUES (?,?)',
                        (smart_routing._profile_mapping_key(main_id,'a.example',False),'inbound:1'))
        config=smart_routing.build_auto(json.dumps(main),json.dumps(rest),has_reserve=True)
        self.assertIsNotNone(config)
        reserve=[o for o in config['outbounds'] if o['tag'].startswith('auto_node_reserve_')]
        self.assertEqual(len(reserve),3)
        self.assertEqual(len({o['tag'] for o in reserve}),3)
        selector=next(b['selector'] for b in config['routing']['balancers'] if b['tag']=='auto_lb_reserve')
        self.assertEqual(len(selector),3)
        # Reserve pool manual rules are ignored by the output compiler.
        self.assertFalse(any(rule.get('balancerTag')=='auto_lb_reserve' and 'domain' in rule
                             for rule in config['routing']['rules']))

    def test_empty_rest_means_no_reserve(self):
        main=[self.profile('a.example')]
        main_id=smart_routing.node_key(main[0]['outbounds'][0])
        with smart_routing.db() as con:
            con.execute('INSERT INTO smart_nodes VALUES (?,?,?)',('inbound:1','Main','["other"]'))
            con.execute('INSERT INTO smart_endpoint_map(endpoint_id,node_id) VALUES (?,?)',
                        (smart_routing._profile_mapping_key(main_id,'a.example',False),'inbound:1'))
        config=smart_routing.build_auto(json.dumps(main),json.dumps([]),has_reserve=True)
        self.assertIsNotNone(config)
        self.assertFalse(any(b['tag']=='auto_lb_reserve' for b in config['routing']['balancers']))


if __name__=='__main__':
    unittest.main()
