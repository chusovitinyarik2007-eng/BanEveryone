"""Safety regression: shared ports and deleted associations never inherit pools."""
import json
import os
import sqlite3
import tempfile
import unittest

import classes
import smart_routing as s


class CollisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old = classes.DB_PATH
        self.addCleanup(setattr, classes, 'DB_PATH', old)
        classes.DB_PATH = os.path.join(self.tmp.name, 'db.sqlite')

    def profile(self, address, name, port=443):
        return {'remarks': name, 'inbounds': [{'tag': 'mixed'}],
                'outbounds': [{'tag': 'proxy', 'protocol': 'vless',
                               'settings': {'address': address, 'port': port, 'id': 'user'}}]}

    def test_shared_port_does_not_link_three_profiles_to_same_inbound(self):
        p = [self.profile('a.example', 'A'), self.profile('b.example', 'B'),
             self.profile('c.example', 'C')]
        panel = [{'id': 71, 'remark': 'Wrong', 'protocol': 'vless', 'port': 443}]
        s.sync_panel_inbounds(panel)
        s.link_profiles_to_inbounds(p, panel)
        with s.db() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM smart_endpoint_map').fetchone()[0], 0)
        s.toggle_node('inbound:71', 'all')
        self.assertIsNone(s.build_auto(json.dumps(p)))

    def test_distinct_names_shared_443_match_only_own_pools(self):
        p = [self.profile('a.example', 'A'), self.profile('b.example', 'B'),
             self.profile('c.example', 'C')]
        panel = [{'id': i, 'remark': chr(65+i-1), 'protocol': 'vless', 'port': 443}
                 for i in range(1, 4)]
        s.sync_panel_inbounds(panel)
        s.toggle_node('inbound:1', 'gemini')
        s.toggle_node('inbound:2', 'other')
        s.toggle_node('inbound:3', 'all')
        s.link_profiles_to_inbounds(p, panel)
        config = s.build_auto(json.dumps(p))
        self.assertIsNotNone(config)
        lbs = {b['tag']: b['selector'] for b in config['routing']['balancers']}
        self.assertEqual([len(lbs['auto_lb_'+name]) for name in ('gemini','other','all')], [1,1,1])
        self.assertEqual(len(set(x[0] for x in lbs.values())), 3)

    def test_remember_does_not_resurrect_deleted_mappings(self):
        p = [self.profile('a.example','A')]
        s.sync_panel_inbounds([{'id': 1, 'remark': 'A', 'protocol': 'vless', 'port': 443}])
        s.toggle_node('inbound:1', 'all')
        s.link_profiles_to_inbounds(p, [{'id': 1, 'remark': 'A', 'protocol': 'vless', 'port': 443}])
        with s.db() as con:
            con.execute('DELETE FROM smart_endpoint_map')
        s.remember(p)
        with s.db() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM smart_endpoint_map').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
