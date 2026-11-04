"""Regression for AUTO NoneType.replace with duplicate endpoint identities."""
import json
import os
import tempfile
import unittest

import classes
import smart_routing as smart


class UnmappedCollisionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        previous = classes.DB_PATH
        self.addCleanup(setattr, classes, 'DB_PATH', previous)
        classes.DB_PATH = os.path.join(tmp.name, 'test.sqlite3')

    @staticmethod
    def profile(name):
        # Both JSON profiles share exactly the same endpoint identity.
        return {
            'remarks': name,
            'inbounds': [{'tag': 'socks', 'protocol': 'socks', 'port': 10808}],
            'outbounds': [{
                'tag': 'proxy', 'protocol': 'vless',
                'settings': {'vnext': [{'address': 'example.org', 'port': 443,
                                       'users': [{'id': '00000000-0000-0000-0000-000000000001',
                                                  'encryption': 'none'}]}]},
                'streamSettings': {'network': 'tcp', 'security': 'tls'},
            }],
        }

    def test_unmapped_duplicates_preserve_subscription_without_crash(self):
        profiles = [self.profile('A'), self.profile('B')]
        # No inbounds are linked: this is the default/unconfigured case.
        self.assertIsNone(smart.build_auto(json.dumps(profiles)))
        payload = json.dumps(profiles).encode()
        self.assertEqual(smart.append_auto(payload, payload), payload)

    def test_one_mapped_duplicate_skips_unmapped_peer(self):
        profiles = [self.profile('A'), self.profile('B')]
        panel = [{'id': 23, 'remark': 'A', 'protocol': 'vless', 'port': 443}]
        smart.sync_panel_inbounds(panel)
        smart.toggle_node('inbound:23', 'all')
        smart.link_profiles_to_inbounds(profiles, panel)
        with smart.db() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM smart_endpoint_map').fetchone()[0], 1)
        config = smart.build_auto(json.dumps(profiles))
        self.assertIsNotNone(config)
        nodes = [o for o in config['outbounds'] if o['tag'].startswith('auto_node_')]
        self.assertEqual(len(nodes), 1)
        self.assertIn(nodes[0]['tag'], [tag for bal in config['routing']['balancers']
                                         for tag in bal['selector']])

    def test_two_verified_duplicates_have_distinct_tags(self):
        profiles = [self.profile('A'), self.profile('B')]
        panel = [{'id': 23, 'remark': 'A', 'protocol': 'vless', 'port': 443},
                 {'id': 24, 'remark': 'B', 'protocol': 'vless', 'port': 443}]
        smart.sync_panel_inbounds(panel)
        smart.toggle_node('inbound:23', 'all')
        smart.toggle_node('inbound:24', 'all')
        smart.link_profiles_to_inbounds(profiles, panel)
        config = smart.build_auto(json.dumps(profiles))
        self.assertIsNotNone(config)
        tags = [o['tag'] for o in config['outbounds'] if o['tag'].startswith('auto_node_')]
        self.assertEqual(len(tags), 2)
        self.assertEqual(len(set(tags)), 2)


if __name__ == '__main__':
    unittest.main()
