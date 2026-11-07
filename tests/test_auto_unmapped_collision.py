"""Regression tests for AUTO crash when one shared endpoint has no inbound mapping."""
import json
import unittest

import smart_routing as s
from test_smart_routing import AutoRoutingTests


class AutoUnmappedCollisionTests(unittest.TestCase):
    setUp = AutoRoutingTests.setUp
    profile = AutoRoutingTests.profile
    outbound = staticmethod(AutoRoutingTests.outbound)

    @staticmethod
    def _get_balancers(config):
        return {b['tag']: b['selector'] for b in config['routing']['balancers']}

    def test_shared_main_endpoint_unmapped_profile_is_skipped_without_exception(self):
        matched = self.profile('192.0.2.17', 443, 'Main mapped')
        unmatched = self.profile('192.0.2.17', 443, 'Old disabled name')
        panel = [{'id': 17, 'protocol': 'vless', 'port': 443, 'remark': 'Main mapped'}]
        s.sync_panel_inbounds(panel)
        s.link_profiles_to_inbounds([matched, unmatched], panel)
        s.toggle_node(s.panel_key(17), 'all')

        config = s.build_auto(json.dumps([matched, unmatched]))
        self.assertIsNotNone(config)
        selectors = self._get_balancers(config)
        self.assertEqual(len(selectors['auto_lb_all']), 1)
        self.assertEqual(selectors['auto_lb_all'], [
            'auto_node_main_' + s.node_key(matched['outbounds'][0])
        ])
        encoded = s.append_auto(json.dumps([matched, unmatched]).encode(),
                                json.dumps([matched, unmatched]).encode())
        parsed = json.loads(encoded)
        self.assertEqual(len(parsed), 3)  # AUTO plus two original profiles
        self.assertEqual(len(self._get_balancers(parsed[0])['auto_lb_all']), 1)

    def test_two_mapped_main_profiles_and_one_unmapped_keep_distinct_tags(self):
        main = [self.profile('192.0.2.20', 443, 'Main A'),
                self.profile('192.0.2.20', 443, 'Main B'),
                self.profile('192.0.2.20', 443, 'Unmapped')]
        panel = [{'id': 11, 'protocol': 'vless', 'port': 443, 'remark': 'Main A'},
                 {'id': 12, 'protocol': 'vless', 'port': 443, 'remark': 'Main B'}]
        s.sync_panel_inbounds(panel)
        s.link_profiles_to_inbounds(main, panel)
        s.toggle_node('inbound:11', 'other')
        s.toggle_node('inbound:12', 'all')
        config = s.build_auto(json.dumps(main))
        self.assertIsNotNone(config)
        balancers = self._get_balancers(config)
        self.assertEqual(len(balancers['auto_lb_other']), 1)
        self.assertEqual(len(balancers['auto_lb_all']), 1)
        self.assertNotEqual(balancers['auto_lb_other'][0], balancers['auto_lb_all'][0])
        self.assertEqual(len([o for o in config['outbounds'] if o['tag'].startswith('auto_node_')]), 2)

    def test_no_main_mapping_does_not_throw_or_generate_auto(self):
        main = [self.profile('192.0.2.21', 443, 'Unknown A'),
                self.profile('192.0.2.21', 443, 'Unknown B')]
        s.sync_panel_inbounds([{'id': 51, 'port': 443, 'protocol': 'vless', 'remark': 'Different'}])
        s.toggle_node('inbound:51', 'all')
        s.link_profiles_to_inbounds(main, [{'id': 51, 'port': 443, 'protocol': 'vless', 'remark': 'Different'}])
        self.assertIsNone(s.build_auto(json.dumps(main)))
        payload = json.dumps(main).encode()
        self.assertEqual(s.append_auto(payload, payload), payload)

    def test_reserve_retains_duplicates_when_main_unmapped_collision_exists(self):
        main = [self.profile('192.0.2.23', 443, 'Main mapped'),
                self.profile('192.0.2.23', 443, 'Main unmapped')]
        reserve = [self.profile('198.51.100.2', 443, 'Rest One'),
                   self.profile('198.51.100.2', 443, 'Rest Two')]
        panel = [{'id': 23, 'protocol': 'vless', 'port': 443, 'remark': 'Main mapped'}]
        s.sync_panel_inbounds(panel)
        s.link_profiles_to_inbounds(main, panel)
        s.toggle_node('inbound:23', 'all')
        config = s.build_auto(json.dumps(main), json.dumps(reserve), has_reserve=True)
        self.assertIsNotNone(config)
        selectors = self._get_balancers(config)
        self.assertEqual(len(selectors['auto_lb_all']), 1)
        self.assertEqual(len(selectors['auto_lb_reserve']), 2)
        self.assertEqual(len(set(selectors['auto_lb_reserve'])), 2)
        self.assertTrue(all(tag.startswith('auto_node_reserve_')
                            for tag in selectors['auto_lb_reserve']))


if __name__ == '__main__':
    unittest.main()
