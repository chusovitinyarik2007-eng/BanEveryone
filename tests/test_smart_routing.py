"""Regression tests for AUTO generation. Run: python -m unittest discover -s tests -v"""
import copy
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import classes
import smart_routing as s


class AutoRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.previous_db = classes.DB_PATH
        self.addCleanup(setattr, classes, 'DB_PATH', self.previous_db)
        classes.DB_PATH = os.path.join(self.temp.name, 'auto.db')

    @staticmethod
    def outbound(address, port, user='uuid-A', protocol='vless'):
        return {'tag': 'node', 'protocol': protocol, 'settings': {
            'address': address, 'port': port, 'id': user, 'encryption': 'none'},
            'streamSettings': {'network': 'tcp', 'security': 'none'}}

    def profile(self, address, port, label='node', rules=None, special=False):
        out = self.outbound(address, port)
        source = [out, {'protocol': 'freedom', 'tag': 'internet-direct',
                        'settings': {'domainStrategy': 'UseIP'}},
                  {'protocol': 'blackhole', 'tag': 'internet-block', 'settings': {}}]
        if special:
            source = [out]  # simulate a profile without default outbounds
        return {'remarks': label,
                'dns': {'tag': 'dns_out', 'servers': ['9.9.9.9']},
                'inbounds': [{'tag': 'mixed', 'protocol': 'socks', 'port': 10808}],
                'outbounds': source,
                'routing': {'domainStrategy': 'IPIfNonMatch', 'domainMatcher': 'hybrid',
                            'rules': copy.deepcopy(rules) if rules is not None else []}}

    def configure(self, profiles, memberships, reserve=None):
        """Install panel IDs and assign each candidate's membership by panel ID."""
        all_profiles = profiles + (reserve or [])
        inbounds = []
        for idx, (profile, pools) in enumerate(zip(all_profiles, memberships), 1):
            o = profile['outbounds'][0]
            inbounds.append({'id': idx, 'remark': profile['remarks'],
                             'protocol': o['protocol'], 'port': o['settings']['port']})
        s.sync_panel_inbounds(inbounds)
        s.link_profiles_to_inbounds(profiles, inbounds)
        if reserve:
            s.link_profiles_to_inbounds(reserve, inbounds, reserve=True)
        for idx, pools in enumerate(memberships, 1):
            for pool in pools:
                s.toggle_node(s.panel_key(idx), pool)
        return inbounds

    def test_overlapping_pools_single_outbound_and_four_balancers(self):
        main = [self.profile(f'192.0.2.{i}', 20000+i, f'Node {i}') for i in range(1, 5)]
        rest = [self.profile('198.51.100.5', 30005, 'Antiblock')]
        memberships = [('all', 'gemini'), ('all', 'other'), ('gemini', 'other'),
                       ('other',), ('reserve',)]
        inbounds = self.configure(main, memberships, rest)
        for i in range(8):
            s.link_profiles_to_inbounds(main, inbounds)
            s.remember(main)
        conf = s.build_auto(json.dumps(main), json.dumps(rest), has_reserve=True)
        self.assertIsNotNone(conf)
        outs = [x for x in conf['outbounds'] if x['tag'].startswith('auto_node_')]
        self.assertEqual(len(outs), 5)
        lb = {x['tag']: x['selector'] for x in conf['routing']['balancers']}
        self.assertEqual(len(lb), 4)
        n1 = 'auto_node_main_' + s.node_key(main[0]['outbounds'][0])
        n2 = 'auto_node_main_' + s.node_key(main[1]['outbounds'][0])
        n3 = 'auto_node_main_' + s.node_key(main[2]['outbounds'][0])
        self.assertIn(n1, lb['auto_lb_all'])
        self.assertIn(n1, lb['auto_lb_gemini'])
        self.assertIn(n2, lb['auto_lb_all'])
        self.assertIn(n2, lb['auto_lb_other'])
        self.assertIn(n3, lb['auto_lb_gemini'])
        self.assertIn(n3, lb['auto_lb_other'])
        self.assertEqual(len(lb['auto_lb_all']), 4)
        self.assertEqual(len(lb['auto_lb_reserve']), 1)
        self.assertEqual(len(set(x['tag'] for x in conf['outbounds'])), len(conf['outbounds']))
        self.assertTrue(all(len(x) == len(set(x)) for x in lb.values()))
        self.assertEqual(len(s.nodes()), 5)
        # AUTO inherits panel DNS verbatim; it never creates a default list.
        self.assertEqual(conf['dns'], main[0]['dns'])
        # Repeat API delivery never produces extra AUTO configs or entries.
        delivered = s.append_auto(json.dumps(main).encode(), json.dumps(main).encode(),
                                  json.dumps(rest).encode(), True)
        self.assertEqual(sum(1 for p in json.loads(delivered)
                             if p['remarks'] == '🔀 AUTO — умная маршрутизация'), 1)

    def test_panel_doh_dns_copied_verbatim(self):
        panel_dns = {
            'queryStrategy': 'UseIP',
            'servers': [
                {'address': 'https://77.88.8.8/dns-query',
                 'domains': ['geosite:yandex', 'geosite:ozon', 'geosite:vk',
                             'geosite:category-gov-ru', 'vcdn.service-ya.fun',
                             'cdn.service-ya.fun']},
                {'address': 'https://8.8.8.8/dns-query', 'skipFallback': False},
            ],
            'tag': 'dns_out',
            'disableFallbackIfMatch': False,
        }
        main = [self.profile('192.0.2.51', 20051, 'Main')]
        main[0]['dns'] = copy.deepcopy(panel_dns)
        before = copy.deepcopy(main)
        self.configure(main, [('all', 'other')])
        config = s.build_auto(json.dumps(main))
        self.assertEqual(config['dns'], panel_dns)
        self.assertEqual(main, before)
        self.assertNotIn('1.1.1.1', str(config['dns']))
        self.assertNotIn('77.88.8.1', str(config['dns']))

    def test_dns_uses_another_panel_profile_when_first_has_no_dns(self):
        main = [self.profile('192.0.2.61', 20061, 'No DNS'),
                self.profile('192.0.2.62', 20062, 'With DNS')]
        main[0].pop('dns')
        main[1]['dns'] = {'servers': [
            {'address': 'https://1.1.1.1/dns-query', 'skipFallback': True}],
            'tag': 'dns_out', 'queryStrategy': 'UseIPv4'}
        self.configure(main, [('all',), ('other',)])
        config = s.build_auto(json.dumps(main))
        self.assertEqual(config['dns'], main[1]['dns'])

    def test_dns_can_be_taken_from_authorized_reserve_profile(self):
        main = [self.profile('192.0.2.71', 20071, 'Main')]
        reserve = [self.profile('198.51.100.71', 30071, 'Reserve')]
        main[0].pop('dns')
        reserve[0]['dns'] = {'servers': ['https://8.8.8.8/dns-query'],
                             'tag': 'dns_out'}
        self.configure(main, [('all',), ('reserve',)], reserve)
        with_rest = s.build_auto(json.dumps(main), json.dumps(reserve), True)
        self.assertEqual(with_rest['dns'], reserve[0]['dns'])
        without_rest = s.build_auto(json.dumps(main), json.dumps(reserve), False)
        self.assertNotIn('dns', without_rest)

    def test_missing_empty_or_invalid_panel_dns_not_fabricated(self):
        p = self.profile('192.0.2.81', 20081)
        self.configure([p], [('all',)])
        for dns in (None, {}, '8.8.8.8', ['8.8.8.8']):
            with self.subTest(dns=dns):
                q = copy.deepcopy(p)
                if dns is None:
                    q.pop('dns')
                else:
                    q['dns'] = dns
                config = s.build_auto(json.dumps([q]))
                self.assertIsNotNone(config)
                self.assertNotIn('dns', config)

    def test_global_panel_rules_priority_and_outbounds(self):
        rules = [
            {'type': 'field', 'protocol': ['bittorrent'], 'outboundTag': 'internet-block'},
            {'type': 'field', 'domain': ['domain:private.example'], 'outboundTag': 'internet-direct'},
            {'type': 'field', 'port': '25,587', 'outboundTag': 'internet-block'},
            {'type': 'field', 'ip': ['geoip:private'], 'outboundTag': 'internet-direct'},
            {'type': 'field', 'domain': ['domain:ignored.example'], 'outboundTag': 'original-proxy'},
            {'type': 'field', 'inboundTag': ['nonexistent-local-inbound'],
             'domain': ['domain:ignored2.example'], 'outboundTag': 'internet-block'},
            {'type': 'field', 'domain': ['domain:after.example'], 'balancerTag': 'old_lb'},
        ]
        p = self.profile('192.0.2.20', 20020, 'Global', rules=rules)
        self.configure([p], [('all','gemini','other')])
        conf = s.build_auto(json.dumps([p]))
        self.assertIsNotNone(conf)
        selected = [r for r in conf['routing']['rules'] if 'outboundTag' in r][:4]
        self.assertEqual([r['outboundTag'] for r in selected], [
            'auto_panel_block', 'auto_panel_direct', 'auto_panel_block', 'auto_panel_direct'])
        self.assertEqual([r['protocol'] for r in selected if 'protocol' in r], [['bittorrent']])
        self.assertEqual(selected[1]['domain'], ['domain:private.example'])
        self.assertEqual(selected[2]['port'], '25,587')
        self.assertEqual(selected[3]['ip'], ['geoip:private'])
        self.assertEqual([r['inboundTag'] for r in selected], [['mixed', 'auto_fallback_all']]*4)
        self.assertLess(conf['routing']['rules'].index(selected[-1]),
                        next(i for i, r in enumerate(conf['routing']['rules']) if r.get('balancerTag') == 'auto_lb_gemini'))
        self.assertEqual({x['tag']: x['protocol'] for x in conf['outbounds']
                          if x['tag'].startswith('auto_panel_')},
                         {'auto_panel_block': 'blackhole', 'auto_panel_direct': 'freedom'})
        self.assertEqual(conf['routing']['domainStrategy'], 'IPIfNonMatch')

    def test_panel_direct_domain_and_private_ip_win_before_every_balancer(self):
        # The two real panel rules from the incident. The client has a
        # reserve entitlement, and both loopback stages are present.
        domain_rule = {
            'type': 'field', 'inboundTag': ['mixed', 'http'],
            'domain': ['geosite:yandex', 'geosite:ozon', 'geosite:vk',
                       'geosite:category-gov-ru', 'vcdn.service-ya.fun',
                       'cdn.service-ya.fun'],
            'outboundTag': 'internet-direct',
        }
        ip_rule = {
            'type': 'field', 'inboundTag': ['mixed', 'http'],
            'ip': ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
                   '169.254.0.0/16', '224.0.0.0/4', '255.255.255.255'],
            'outboundTag': 'internet-direct',
        }
        main = [self.profile('192.0.2.90', 20090, 'Gemini', [domain_rule, ip_rule]),
                self.profile('192.0.2.91', 20091, 'Other')]
        # Even though only the SOCKS inbound is in the template, a real HTTP
        # inbound can be present in configurations issued by the panel.
        main[0]['inbounds'].append({'tag': 'http', 'protocol': 'http', 'port': 10809})
        rest = [self.profile('198.51.100.92', 30092, 'Antiblock')]
        before = copy.deepcopy([main, rest])
        self.configure(main, [('gemini',), ('other',), ('reserve',)], rest)
        conf = s.build_auto(json.dumps(main), json.dumps(rest), True)
        rout = conf['routing']['rules']
        self.assertEqual(set(b['tag'] for b in conf['routing']['balancers']),
                         {'auto_lb_gemini', 'auto_lb_other', 'auto_lb_all', 'auto_lb_reserve'})
        self.assertEqual([r.get('outboundTag') for r in rout[:2]],
                         ['auto_panel_direct', 'auto_panel_direct'])
        self.assertEqual(rout[0]['domain'], domain_rule['domain'])
        self.assertEqual(rout[1]['ip'], ip_rule['ip'])
        self.assertEqual(rout[0]['inboundTag'],
                         ['mixed', 'http', 'auto_fallback_all', 'auto_fallback_reserve'])
        self.assertEqual(rout[1]['inboundTag'], rout[0]['inboundTag'])
        self.assertTrue(all(r.get('balancerTag') is None for r in rout[:2]))
        # Both panel rules precede every route selecting a balancer.
        balancer_indexes = [i for i, r in enumerate(rout) if 'balancerTag' in r]
        self.assertTrue(balancer_indexes and min(balancer_indexes) >= 2)
        self.assertLess(1, next(i for i, r in enumerate(rout)
                                if r.get('balancerTag') == 'auto_lb_reserve'))
        self.assertEqual(len([o for o in conf['outbounds']
                              if o['tag'] == 'auto_panel_direct']), 1)
        self.assertEqual([main, rest], before)

    def test_panel_block_is_first_and_applies_after_both_fallbacks(self):
        panel_rules = [
            {'type': 'field', 'inboundTag': ['mixed'], 'domain': ['domain:blocked.test'],
             'outboundTag': 'internet-block'},
            {'type': 'field', 'domain': ['domain:allowed.test'],
             'outboundTag': 'internet-direct'},
        ]
        main = [self.profile('192.0.2.100', 20100, 'Main', panel_rules)]
        rest = [self.profile('198.51.100.100', 30100, 'Reserve')]
        self.configure(main, [('other',), ('reserve',)], rest)
        conf = s.build_auto(json.dumps(main), json.dumps(rest), True)
        rules = conf['routing']['rules']
        self.assertEqual([r['outboundTag'] for r in rules[:2]],
                         ['auto_panel_block', 'auto_panel_direct'])
        self.assertEqual(rules[0]['inboundTag'],
                         ['mixed', 'auto_fallback_all', 'auto_fallback_reserve'])
        self.assertEqual(rules[1]['inboundTag'], rules[0]['inboundTag'])
        self.assertEqual(rules[2]['balancerTag'], 'auto_lb_reserve')
        self.assertEqual(rules[3]['balancerTag'], 'auto_lb_all')
        # Without reserve, no dangling synthetic tag or fallback rule.
        without_reserve = s.build_auto(json.dumps(main), json.dumps(rest), False)
        self.assertEqual(without_reserve['routing']['rules'][0]['inboundTag'],
                         ['mixed', 'auto_fallback_all'])
        self.assertNotIn('auto_fallback_reserve', str(without_reserve))

    def test_panel_pre_normalized_tags_work_even_without_explicit_outbound(self):
        p = self.profile('192.0.2.110', 20110, 'Main', special=True)
        p['routing']['rules'] = [
            {'type': 'field', 'inboundTag': ['mixed'], 'domain': ['geosite:yandex'],
             'outboundTag': 'auto_panel_direct'},
            {'type': 'field', 'inboundTag': ['mixed'], 'ip': ['10.0.0.0/8'],
             'outboundTag': 'auto_panel_direct'},
        ]
        self.configure([p], [('other',)])
        conf = s.build_auto(json.dumps([p]))
        self.assertEqual([r['outboundTag'] for r in conf['routing']['rules'][:2]],
                         ['auto_panel_direct', 'auto_panel_direct'])
        self.assertEqual([out['protocol'] for out in conf['outbounds']
                          if out['tag'] == 'auto_panel_direct'], ['freedom'])
        self.assertTrue(all('balancerTag' not in rule for rule in conf['routing']['rules'][:2]))

    def test_rules_optional_and_reserve_access_restricted(self):
        p = self.profile('192.0.2.2', 20002, special=True)
        r = self.profile('198.51.100.4', 30004, label='Rest', special=True)
        self.configure([p], [('all','other'), ('reserve',)], [r])
        conf = s.build_auto(json.dumps([p]), json.dumps([r]), False)
        self.assertNotIn('auto_lb_reserve', {x['tag'] for x in conf['routing']['balancers']})
        self.assertEqual(len([x for x in conf['outbounds'] if x['tag'].startswith('auto_node_')]), 1)
        self.assertFalse(any(x['tag'] == 'auto_panel_block' for x in conf['outbounds']))
        self.assertEqual(conf['routing']['domainStrategy'], 'AsIs')
        self.assertFalse(any('auto_fallback_reserve' in str(rule) for rule in conf['routing']['rules']))

    def test_missing_or_unassigned_nodes_and_multiple_memberships(self):
        ps = [self.profile(f'192.0.2.{i}', 20000+i, f'Node {i}') for i in (1,2,3)]
        self.configure(ps, [('all','gemini'), ('other',), ()])
        conf = s.build_auto(json.dumps(ps[:1]))
        self.assertEqual(len([x for x in conf['outbounds'] if x['tag'].startswith('auto_node_')]), 1)
        self.assertEqual({x['tag'] for x in conf['routing']['balancers']},
                         {'auto_lb_gemini','auto_lb_all'})
        self.assertNotIn('auto_lb_other', str(conf))
        conf2 = s.build_auto(json.dumps(ps))
        self.assertEqual(len([x for x in conf2['outbounds'] if x['tag'].startswith('auto_node_')]), 2)

    def test_confirmed_panel_mapping_survives_name_collision(self):
        ps = [self.profile('192.0.2.12', 21012, 'shared-name'),
              self.profile('192.0.2.13', 21013, 'shared-name')]
        self.configure(ps, [('all',), ('other',)])
        # Name aliases collide; matched unique port+protocol must still win.
        for _ in range(5):
            s.remember(ps)
        conf = s.build_auto(json.dumps(ps))
        lb = {x['tag']: x['selector'] for x in conf['routing']['balancers']}
        self.assertEqual(len(lb['auto_lb_all']), 2)
        self.assertEqual(len(lb['auto_lb_other']), 1)
        self.assertNotEqual(lb['auto_lb_all'], lb['auto_lb_other'])
        with s.db() as con:
            for i, p in enumerate(ps, 1):
                ep = s.node_key(p['outbounds'][0])
                self.assertEqual(con.execute('SELECT node_id FROM smart_endpoint_map WHERE endpoint_id=?', (ep,)).fetchone()[0], s.panel_key(i))
        # Rename and user credentials change: current endpoint still maps.
        updated = copy.deepcopy(ps)
        updated[0]['remarks'] = 'Renamed'
        updated[0]['outbounds'][0]['settings']['id'] = 'new-user-uuid'
        conf2 = s.build_auto(json.dumps(updated))
        self.assertEqual(lb['auto_lb_all'], {x['tag']: x['selector'] for x in conf2['routing']['balancers']}['auto_lb_all'])

    def test_renaming_auto_changes_only_generated_profile(self):
        p1 = self.profile('192.0.2.201', 20201, 'Настоящий инбаунд панели')
        p2 = self.profile('192.0.2.202', 20202, 'Другой инбаунд панели')
        originals = copy.deepcopy([p1, p2])
        self.configure([p1, p2], [('all',), ('other',)])
        self.assertEqual(s.auto_name(), s.AUTO_DEFAULT_NAME)
        result = s.set_auto_name('🚀 Мой AUTO')
        self.assertEqual(result, '🚀 Мой AUTO')
        self.assertEqual(s.auto_name(), result)
        config = s.build_auto(json.dumps([p1, p2]))
        self.assertEqual(config['remarks'], '🚀 Мой AUTO')
        self.assertEqual(config['meta']['serverDescription'], '🚀 Мой AUTO')
        self.assertEqual([p1, p2], originals)
        response = s.append_auto(json.dumps([p1, p2]).encode(), json.dumps([p1, p2]).encode())
        parsed = json.loads(response)
        self.assertEqual([x['remarks'] for x in parsed],
                         ['🚀 Мой AUTO', 'Настоящий инбаунд панели', 'Другой инбаунд панели'])
        self.assertEqual(parsed[1:], originals)
        # Fresh SQLite handle must retain the new name.
        self.assertEqual(s.option('auto_name'), '🚀 Мой AUTO')

    def test_old_auto_name_is_replaced_not_duplicated(self):
        p = self.profile('192.0.2.210', 20210, 'Panel')
        self.configure([p], [('all', 'other')])
        old = s.build_auto(json.dumps([p]))
        self.assertEqual(old['remarks'], s.AUTO_DEFAULT_NAME)
        s.set_auto_name('AUTO v2')
        # Earlier AUTO remains in a previously produced response.
        old_response = json.dumps([old, p], ensure_ascii=False).encode()
        renewed = s.append_auto(old_response, json.dumps([p]).encode())
        arr = json.loads(renewed)
        self.assertEqual(len(arr), 2)
        self.assertEqual([x['remarks'] for x in arr], ['AUTO v2', 'Panel'])
        self.assertEqual(arr[1], p)
        # Repeated refresh with the newest name also remains idempotent.
        again = s.append_auto(renewed, json.dumps([p]).encode())
        self.assertEqual([x['remarks'] for x in json.loads(again)], ['AUTO v2', 'Panel'])

    def test_real_panel_inbound_with_default_auto_name_is_not_removed(self):
        panel = self.profile('192.0.2.240', 20240, s.AUTO_DEFAULT_NAME)
        other = self.profile('192.0.2.241', 20241, 'Other panel inbound')
        self.configure([panel, other], [('all',), ('other',)])
        originals = copy.deepcopy([panel, other])
        response = s.append_auto(json.dumps(originals).encode(), json.dumps(originals).encode())
        arr = json.loads(response)
        self.assertEqual(len(arr), 3)
        self.assertEqual(arr[1:], originals)

    def test_auto_name_validation_and_reset(self):
        p = self.profile('192.0.2.230', 20230)
        self.configure([p], [('all',)])
        for invalid in ('', ' ', 'a' * 101, 'Name\nInjected', 'Name\rInjected'):
            with self.subTest(invalid=repr(invalid)), self.assertRaises(ValueError):
                s.set_auto_name(invalid)
        self.assertEqual(s.auto_name(), s.AUTO_DEFAULT_NAME)
        s.set_auto_name('  Название   ')
        self.assertEqual(s.auto_name(), 'Название')
        s.set_auto_name(None)
        self.assertEqual(s.auto_name(), s.AUTO_DEFAULT_NAME)
        self.assertIsNone(s.option('auto_name'))
        config = s.build_auto(json.dumps([p]))
        self.assertEqual(config['meta']['serverDescription'], s.AUTO_DEFAULT_DESCRIPTION)

    def test_ru_websites_use_direct_and_there_is_no_ru_balancer(self):
        gemini = self.profile('192.0.2.21', 21021, 'Gemini')
        internet = self.profile('192.0.2.22', 21022, 'Internet')
        ru_only = self.profile('192.0.2.23', 21023, 'RU only')
        reserve = self.profile('198.51.100.24', 31024, 'Antiblock')
        original = copy.deepcopy([gemini, internet, ru_only])
        self.configure([gemini, internet, ru_only], [('gemini',), ('other',), ('all',), ('reserve',)], [reserve])
        s.set_option('rules_ru', ['domain:ozon.ru', 'regexp:(^|\\.)ru$'])
        result = s.build_auto(json.dumps(original), json.dumps([reserve]), has_reserve=True)
        balancers = {bal['tag']: bal for bal in result['routing']['balancers']}
        self.assertEqual(set(balancers), {'auto_lb_gemini', 'auto_lb_other', 'auto_lb_all', 'auto_lb_reserve'})
        self.assertEqual(len(balancers['auto_lb_all']['selector']), 3)
        self.assertNotIn('auto_lb_ru', str(result))
        ru_rule = next(rule for rule in result['routing']['rules']
                       if rule.get('domain') == ['domain:ozon.ru', 'regexp:(^|\\.)ru$'])
        self.assertEqual(ru_rule['outboundTag'], 'auto_panel_direct')
        self.assertNotIn('balancerTag', ru_rule)
        self.assertEqual([out['protocol'] for out in result['outbounds']
                          if out['tag'] == 'auto_panel_direct'], ['freedom'])
        self.assertLess(result['routing']['rules'].index(ru_rule),
                        next(i for i, r in enumerate(result['routing']['rules'])
                             if r.get('balancerTag') == 'auto_lb_gemini' and r.get('domain')))
        self.assertEqual(original, [gemini, internet, ru_only])

    def test_panel_global_direct_block_are_not_overwritten_by_ru(self):
        p = self.profile('192.0.2.26', 20026, rules=[
            {'type': 'field', 'domain': ['domain:blocked.ru'], 'outboundTag': 'internet-block'},
            {'type': 'field', 'domain': ['domain:special.ru'], 'outboundTag': 'internet-direct'}])
        self.configure([p], [('all',)])
        result = s.build_auto(json.dumps([p]))
        rout = result['routing']['rules']
        panel_rules = [r for r in rout if r.get('domain') in
                       (['domain:blocked.ru'], ['domain:special.ru'])]
        self.assertEqual([r['outboundTag'] for r in panel_rules],
                         ['auto_panel_block', 'auto_panel_direct'])
        self.assertEqual(len([o for o in result['outbounds'] if o['tag'] == 'auto_panel_direct']), 1)
        self.assertLess(rout.index(panel_rules[-1]), next(i for i, r in enumerate(rout)
                                                           if r.get('domain') == s.DEFAULT_RULES['ru']))

    def test_old_ru_memberships_migrate_to_all_and_stay_after_refresh(self):
        profile = self.profile('192.0.2.27', 20027, 'Old RU')
        inbounds = self.configure([profile], [()])
        with s.db() as con:
            con.execute('UPDATE smart_nodes SET pools=? WHERE id=?',
                        (json.dumps(['ru', 'gemini']), s.panel_key(1)))
            con.execute('DELETE FROM smart_options WHERE key=?', ('migration_ru_to_all_v1',))
        conf = s.build_auto(json.dumps([profile]))
        self.assertEqual({b['tag'] for b in conf['routing']['balancers']},
                         {'auto_lb_gemini', 'auto_lb_all'})
        self.assertEqual(s.nodes()[0]['pools'], ['gemini', 'all'])
        for i in range(8):
            s.sync_panel_inbounds(inbounds)
            s.remember([profile])
        self.assertEqual(s.nodes()[0]['pools'], ['gemini', 'all'])
        self.assertEqual(len([out for out in conf['outbounds'] if out['tag'].startswith('auto_node_')]), 1)

    def test_both_gemini_other_populate_all_without_manual_all_assignment(self):
        ps = [self.profile('192.0.2.30', 20330), self.profile('192.0.2.31', 20331)]
        self.configure(ps, [('gemini', 'other'), ('gemini',)])
        conf = s.build_auto(json.dumps(ps))
        bal = {b['tag']: b['selector'] for b in conf['routing']['balancers']}
        self.assertEqual(len(bal['auto_lb_gemini']), 2)
        self.assertEqual(len(bal['auto_lb_other']), 1)
        self.assertEqual(len(bal['auto_lb_all']), 2)
        self.assertEqual(len([out for out in conf['outbounds'] if out['tag'].startswith('auto_node_')]), 2)

    def test_cleared_ru_domains_create_no_new_direct_when_panel_has_none(self):
        p = self.profile('192.0.2.39', 20039, special=True)
        self.configure([p], [('all',)])
        s.set_option('rules_ru', [])
        conf = s.build_auto(json.dumps([p]))
        self.assertFalse(any(out['tag'] == 'auto_panel_direct' for out in conf['outbounds']))
        self.assertFalse(any('domain' in r for r in conf['routing']['rules']))

    def test_disabling_all_only_affects_all_balancer_not_ru_direct(self):
        p = self.profile('192.0.2.40', 20040, 'Internet')
        self.configure([p], [('gemini', 'other')])
        s.set_option('enabled_all', False)
        conf = s.build_auto(json.dumps([p]))
        self.assertEqual({b['tag'] for b in conf['routing']['balancers']},
                         {'auto_lb_gemini', 'auto_lb_other'})
        self.assertIn('auto_panel_direct', [out['tag'] for out in conf['outbounds']])
        self.assertNotIn('auto_to_all', [out['tag'] for out in conf['outbounds']])

    def test_fallbacks_and_routing_tags_are_consistent_across_topologies(self):
        main = [self.profile('192.0.2.51', 21051, 'Gemini'),
                self.profile('192.0.2.52', 21052, 'Web'),
                self.profile('192.0.2.53', 21053, 'RU')]
        rest = [self.profile('198.51.100.54', 31054, 'Reserve')]
        self.configure(main, [('gemini',), ('other',), ('all',), ('reserve',)], rest)
        for with_rest in (True, False):
            for blocked in ((), ('gemini',), ('other',), ('all',), ('gemini', 'all')):
                for p in s.POOL_NAMES:
                    s.set_option('enabled_' + p, p not in blocked)
                conf = s.build_auto(json.dumps(main), json.dumps(rest), with_rest)
                if conf is None:
                    continue
                outtags = {out['tag'] for out in conf['outbounds']}
                bal = {b['tag']: b for b in conf['routing']['balancers']}
                self.assertNotIn('auto_lb_ru', bal)
                for b in bal.values():
                    self.assertTrue(set(b['selector']) <= outtags)
                    self.assertIn(b['fallbackTag'], outtags)
                for r in conf['routing']['rules']:
                    if 'outboundTag' in r:
                        self.assertIn(r['outboundTag'], outtags)
                    if 'balancerTag' in r:
                        self.assertIn(r['balancerTag'], bal)
                self.assertEqual(len(outtags), len(conf['outbounds']))

    def test_empty_and_no_json_fail_safe(self):
        self.assertIsNone(s.build_auto('vless://xyz'))
        self.assertIsNone(s.build_auto('{}'))
        self.assertIsNone(s.build_auto('[]'))
        self.assertEqual(s.append_auto(b'vless://example', b'vless://example'), b'vless://example')


if __name__ == '__main__':
    unittest.main()

class DuplicateEndpointRegressionTests(unittest.TestCase):
    setUp = AutoRoutingTests.setUp
    outbound = staticmethod(AutoRoutingTests.outbound)
    profile = AutoRoutingTests.profile
    """A shared external endpoint must not erase distinct panel inbound IDs."""

    def test_two_main_and_two_reserve_profiles_sharing_endpoints(self):
        main = [self.profile('192.0.2.25', 443, 'Main alpha'),
                self.profile('192.0.2.25', 443, 'Main beta')]
        reserve = [self.profile('198.51.100.25', 443, 'Reserve alpha'),
                   self.profile('198.51.100.25', 443, 'Reserve beta')]
        panel = [{'id': i, 'remark': profile['remarks'], 'protocol': 'vless', 'port': 443}
                 for i, profile in enumerate(main + reserve, 1)]
        s.sync_panel_inbounds(panel)
        s.link_profiles_to_inbounds(main, panel)
        s.link_profiles_to_inbounds(reserve, panel, reserve=True)
        for i in (1, 2):
            s.toggle_node(s.panel_key(i), 'other')
        for i in (3, 4):
            s.toggle_node(s.panel_key(i), 'reserve')
        # Repeated refresh should not collapse the associations.
        for _ in range(3):
            s.remember(main)
            s.remember(reserve, reserve=True)
        conf = s.build_auto(json.dumps(main), json.dumps(reserve), True)
        lbs = {b['tag']: b['selector'] for b in conf['routing']['balancers']}
        self.assertEqual(len(lbs['auto_lb_other']), 2)
        self.assertEqual(len(lbs['auto_lb_all']), 2)
        self.assertEqual(len(lbs['auto_lb_reserve']), 2)
        self.assertEqual(len({o['tag'] for o in conf['outbounds']}), len(conf['outbounds']))
