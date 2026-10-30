"""New configurable balancers, legacy DB migration and browser discrimination."""
import json
import os
import sqlite3
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import classes
import routing_balancers as rb
import smart_routing as s
from subscription_clients import classify, NO_CACHE


class DynamicRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = classes.DB_PATH
        self.addCleanup(setattr, classes, 'DB_PATH', self.old)
        classes.DB_PATH = os.path.join(self.tmp.name, 'subserver.db')

    @staticmethod
    def profile(addr, port, label):
        return {
            'remarks': label,
            'dns': {'servers': ['9.9.9.9'], 'tag': 'dns_out'},
            'inbounds': [{'tag': 'mixed', 'protocol': 'socks', 'port': 10808}],
            'outbounds': [
                {'tag': 'proxy', 'protocol': 'vless', 'settings': {
                    'address': addr, 'port': port, 'id': 'tester', 'encryption': 'none'},
                 'streamSettings': {'network': 'xhttp', 'security': 'tls'}}],
            'routing': {'rules': [
                {'type': 'field', 'domain': ['domain:panel.example'], 'outboundTag': 'direct'}]},
        }

    def install(self):
        main = [self.profile('a.test', 10001, 'MainA'), self.profile('b.test', 10002, 'MainB')]
        reserve = [self.profile('c.test', 10003, 'ReserveC')]
        ins = [{'id': 1, 'protocol': 'vless', 'port': 10001, 'remark': 'MainA'},
               {'id': 2, 'protocol': 'vless', 'port': 10002, 'remark': 'MainB'},
               {'id': 3, 'protocol': 'vless', 'port': 10003, 'remark': 'ReserveC'}]
        s.sync_panel_inbounds(ins)
        s.link_profiles_to_inbounds(main, ins)
        s.link_profiles_to_inbounds(reserve, ins, reserve=True)
        return main, reserve

    def test_existing_db_options_nodes_migrated_once(self):
        with sqlite3.connect(classes.DB_PATH) as con:
            con.execute('CREATE TABLE smart_nodes (id TEXT PRIMARY KEY,name TEXT NOT NULL,pools TEXT NOT NULL)')
            con.execute('CREATE TABLE smart_options (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            con.execute('INSERT INTO smart_nodes VALUES (?,?,?)', ('inbound:88','Legacy',['all'].__str__().replace("'", '"')))
            con.execute('INSERT INTO smart_options VALUES (?,?)', ('rules_gemini', json.dumps(['domain:newgemini.tld'])))
            con.execute('INSERT INTO smart_options VALUES (?,?)', ('enabled_other', 'false'))
        v1 = rb.all_balancers()
        v2 = rb.all_balancers()
        self.assertEqual(v1,v2)
        self.assertEqual([x['id'] for x in v1], ['gemini','other','all','reserve'])
        self.assertFalse(rb.get('other')['enabled'])
        self.assertEqual(rb.get('gemini')['rules'], ['domain:newgemini.tld'])
        self.assertEqual(s.nodes()[0]['pools'], ['all'])

    def test_custom_fallback_chains_and_rules(self):
        main, reserve = self.install()
        a = rb.create('Streaming', 'main')
        b = rb.create('Backup streaming', 'reserve')
        rb.update(a, strategy='roundRobin', fallback=b,
                  rules=['domain:video.test', 'ip:198.51.100.0/24', 'geoip:private'], priority=-10)
        rb.update(b, strategy='leastLoad', fallback='direct')
        rb.update('gemini', fallback=a)
        s.toggle_node(s.panel_key(1), a)
        s.toggle_node(s.panel_key(2), 'other')
        s.toggle_node(s.panel_key(3), b)
        rb.set_interval('45s')
        c = s.build_auto(json.dumps(main), json.dumps(reserve), has_reserve=True)
        bal = {x['tag']: x for x in c['routing']['balancers']}
        self.assertEqual(bal['auto_lb_'+a]['strategy']['type'], 'roundRobin')
        self.assertEqual(bal['auto_lb_'+a]['fallbackTag'], 'auto_to_'+b)
        self.assertEqual(bal['auto_lb_'+b]['strategy']['type'], 'leastLoad')
        self.assertEqual(bal['auto_lb_'+b]['fallbackTag'], 'auto_panel_direct')
        self.assertEqual(c['observatory']['probeInterval'], '45s')
        rules = c['routing']['rules']
        self.assertEqual(rules[0]['domain'], ['domain:panel.example'])
        self.assertEqual(rules[0]['inboundTag'][0], 'mixed')
        self.assertIn('auto_fallback_'+b, rules[0]['inboundTag'])
        self.assertEqual(c['dns'], main[0]['dns'])
        self.assertTrue(any(r.get('domain') == ['domain:video.test'] and r.get('balancerTag')=='auto_lb_'+a for r in rules))
        self.assertTrue(any(r.get('ip') == ['198.51.100.0/24', 'geoip:private'] for r in rules))
        self.assertEqual(len([o for o in c['outbounds'] if o['tag'].startswith('auto_node_')]),3)

    def test_no_reserve_never_leaks(self):
        main, reserve = self.install()
        s.toggle_node(s.panel_key(1), 'gemini')
        s.toggle_node(s.panel_key(2), 'other')
        s.toggle_node(s.panel_key(3), 'reserve')
        c = s.build_auto(json.dumps(main), json.dumps(reserve), has_reserve=False)
        self.assertNotIn('auto_lb_reserve', {x['tag'] for x in c['routing']['balancers']})
        self.assertNotIn('auto_node_reserve_', str(c))

    def test_fallback_cycle_and_delete_validation(self):
        a, b = rb.create('X'), rb.create('Y')
        rb.update(a, fallback=b)
        with self.assertRaisesRegex(ValueError, 'цикл'):
            rb.update(b, fallback=a)
        with self.assertRaisesRegex(ValueError, 'fallback'):
            rb.delete(b)
        rb.update(a, fallback='block')
        rb.delete(b)
        self.assertIsNone(rb.get(b))

    def test_unsupported_invalid_settings(self):
        a = rb.create('X')
        for strategy in ('leastping','auto','roundrobin'):
            with self.assertRaises(ValueError):
                rb.update(a, strategy=strategy)
        for interval in ('0s','2s','3h','100m','junk'):
            with self.assertRaises(ValueError):
                rb.set_interval(interval)
        with self.assertRaises(ValueError):
            rb.update(a, rules=['garbage.test'])


class ClientDetectionTests(unittest.TestCase):
    def test_happ_webview_prefers_html_despite_hwid(self):
        self.assertEqual(classify({'user-agent':'Happ/6.0.0', 'accept':'text/html,application/xhtml+xml', 'X-HWID':'x'}), 'browser')
        self.assertEqual(classify({'user-agent':'Mozilla/5.0 Happ', 'sec-fetch-dest':'document','x-hwid':'a'}),'browser')

    def test_supported_clients(self):
        self.assertEqual(classify({'User-Agent':'Happ/6.0.0','Accept':'*/*','x-hwid':'xyz'}), 'happ')
        self.assertEqual(classify({'User-Agent':'INCY/1.0/iOS','Accept':'*/*'}), 'incy')
        self.assertEqual(classify({'User-Agent':'ok','x-client':'INCY','x-hwid':'xyz'}),'incy')

    def test_other_apps_unsupported_and_browsers_html(self):
        self.assertEqual(classify({'user-agent':'Shadowrocket/2.0','Accept':'*/*'}),'unsupported')
        self.assertEqual(classify({'User-Agent':'curl/8.7.1','Accept':'*/*'}),'unsupported')
        self.assertEqual(classify({'user-agent':'Mozilla/5.0','accept':'text/html'}),'browser')
        self.assertIn('no-store', NO_CACHE['Cache-Control'])


if __name__ == '__main__':
    unittest.main()

class ManualMembershipRegressionTests(unittest.TestCase):
    """All choices are shown, but generated memberships are never inherited."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = classes.DB_PATH
        self.addCleanup(setattr, classes, 'DB_PATH', self.old)
        classes.DB_PATH = os.path.join(self.tmp.name, 'manual.db')

    def test_panel_refresh_does_not_filter_protocols_or_change_memberships(self):
        rows = [
            {'id': 11, 'protocol': 'vless', 'remark': 'VLESS'},
            {'id': 12, 'protocol': 'custom-future-protocol', 'remark': 'Future'},
            {'id': 13, 'protocol': 'hysteria2', 'remark': 'HY2'},
            {'id': 14, 'protocol': 'wireguard', 'remark': 'WG'},
            {'id': 15, 'protocol': '', 'remark': 'Other'},
            {'id': 12, 'protocol': 'custom-future-protocol', 'remark': 'Future duplicate'},
        ]
        shown = s.panel_nodes(rows)
        self.assertEqual({n['id'] for n in shown}, {'inbound:11','inbound:12','inbound:13','inbound:14','inbound:15'})
        s.set_node_membership('inbound:12', 'all', True)
        shown = s.panel_nodes(rows)
        self.assertEqual(next(n['pools'] for n in shown if n['id'] == 'inbound:12'), ['all'])
        self.assertEqual(len(shown), 5)

    def test_manual_all_balancer_never_inherits_gemini_or_other(self):
        profiles = [DynamicRoutingTests.profile('a.test',10001,'A'),
                    DynamicRoutingTests.profile('b.test',10002,'B'),
                    DynamicRoutingTests.profile('c.test',10003,'C')]
        inbounds = [{'id':i,'protocol':'vless','remark':p['remarks'],'port':10000+i}
                    for i,p in enumerate(profiles, 1)]
        s.sync_panel_inbounds(inbounds)
        s.link_profiles_to_inbounds(profiles, inbounds)
        s.set_node_membership('inbound:1', 'gemini', True)
        s.set_node_membership('inbound:2', 'other', True)
        s.set_node_membership('inbound:3', 'all', True)
        for _ in range(3):
            s.panel_nodes(inbounds)
            config = s.build_auto(json.dumps(profiles))
            balancers = {b['tag']:b['selector'] for b in config['routing']['balancers']}
            self.assertEqual(len(balancers['auto_lb_gemini']),1)
            self.assertEqual(len(balancers['auto_lb_other']),1)
            self.assertEqual(len(balancers['auto_lb_all']),1)
            self.assertEqual(len(set(sum(balancers.values(), []))), 3)
        s.set_node_membership('inbound:3', 'all', False)
        balancers = {b['tag']:b['selector'] for b in s.build_auto(json.dumps(profiles))['routing']['balancers']}
        self.assertNotIn('auto_lb_all', balancers)
