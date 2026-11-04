"""Regression tests: real-world URI aliases, nullable xHTTP extra, batch import UX.

No real customer keys or credentials are embedded in these tests.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

import classes
import vless_config as vc

ROOT = Path(__file__).resolve().parents[1]
ID1 = '123e4567-e89b-42d3-a456-426614174001'
ID2 = '123e4567-e89b-42d3-a456-426614174002'


def link(uuid=ID1, transport='xhttp', extra=None, flags='insecure=0'):
    args = f'type={transport}&security=tls&sni=example.net&path=%2Ftest&mode=packet-up&{flags}'
    if extra is not None:
        args += '&extra=' + quote(extra, safe='')
    return f'vless://{uuid}@example.net:443?{args}#Test'


class ParserCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        old_db = classes.DB_PATH
        classes.DB_PATH = os.path.join(temp.name, 'test.sqlite')
        self.addCleanup(setattr, classes, 'DB_PATH', old_db)

    def test_nullable_xhttp_extra_and_full_json_object(self):
        _, out = vc.parse_vless(link(extra='null'))
        settings = out['streamSettings']['xhttpSettings']
        self.assertNotIn('extra', settings)
        self.assertEqual(settings['mode'], 'packet-up')
        self.assertEqual(settings['path'], '/test')
        for extra in ('', 'NULL', '  null  '):
            self.assertNotIn('extra', vc.parse_vless(link(extra=extra))[1]['streamSettings']['xhttpSettings'])
        content = '{"uplinkHTTPMethod":"GET","xmux":{"maxConnections":3},"xPaddingMethod":"tokenish"}'
        self.assertEqual(vc.parse_vless(link(extra=content))[1]['streamSettings']['xhttpSettings']['extra'], json.loads(content))
        self.assertEqual(vc.parse_vless(link(extra='{}'))[1]['streamSettings']['xhttpSettings']['extra'], {})
        for invalid in ('[]', 'false', '123', '{wrong'):
            with self.assertRaisesRegex(ValueError, 'xHTTP extra'):
                vc.parse_vless(link(extra=invalid))

    def test_secure_insecure_aliases_and_conflicts(self):
        for flag in ('insecure=0', 'allowInsecure=0', 'insecure=false',
                     'allowInsecure=no', 'insecure=0&allowInsecure=0'):
            _, out = vc.parse_vless(link(flags=flag))
            self.assertNotIn('allowInsecure', out['streamSettings']['tlsSettings'])
        for flag in ('insecure=1', 'allowInsecure=true', 'insecure=YES'):
            with self.assertRaisesRegex(ValueError, 'insecure=1'):
                vc.parse_vless(link(flags=flag))
        with self.assertRaisesRegex(ValueError, 'противоречат'):
            vc.parse_vless(link(flags='insecure=0&allowInsecure=1'))
        with self.assertRaisesRegex(ValueError, 'insecure: ожидается'):
            vc.parse_vless(link(flags='insecure=maybe'))
        reality = f'vless://{ID1}@example.net:443?security=reality&pbk=FAKE_PUBLIC_KEY&insecure=0'
        _, out = vc.parse_vless(reality)
        self.assertIn('realitySettings', out['streamSettings'])
        self.assertNotIn('tlsSettings', out['streamSettings'])

    def test_bulk_import_known_non_vless_skipped_explicitly(self):
        content = '\n'.join([link(), '', 'ss://example-not-supported',
                             link(uuid=ID2, flags='allowInsecure=0', extra='null')])
        result = vc.add_links(content)
        self.assertEqual(result['added_count'], 2)
        self.assertEqual(result['skipped'], [{'line': 3, 'reason': 'Не VLESS (ss://)'}])
        nodes = vc.state(include_secrets=True)['nodes']
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0]['uri'], link())
        self.assertEqual(nodes[1]['uri'], link(uuid=ID2, flags='allowInsecure=0', extra='null'))
        vc.update_settings(enabled=True)
        profile = json.loads(vc.cached_json())
        tags = [n['tag'] for n in profile['outbounds'] if n['protocol'] == 'vless']
        self.assertEqual(profile['routing']['balancers'][0]['selector'], tags)
        self.assertEqual(profile['observatory']['subjectSelector'], tags)

    def test_bulk_validation_still_transactional_and_line_numbered(self):
        with self.assertRaisesRegex(ValueError, 'Строка 2'):
            vc.add_links(link() + '\n' + link(uuid=ID2, flags='insecure=1'))
        self.assertFalse(vc.state()['nodes'])
        with self.assertRaisesRegex(ValueError, 'ss:// не поддерживается'):
            vc.add_links('ss://example')
        with self.assertRaisesRegex(ValueError, 'Строка 2'):
            vc.add_links(link() + '\ninvalid-link')
        self.assertFalse(vc.state()['nodes'])

    def test_cache_is_not_regenerated_on_sub_request(self):
        from unittest.mock import patch
        vc.add_links(link(extra='null'))
        vc.update_settings(enabled=True)
        compiled = vc.cached_json()
        with patch.object(vc, 'parse_vless', side_effect=AssertionError('unnecessary parsing')):
            self.assertEqual(vc.cached_json(), compiled)
            response = json.loads(vc.append_cached(b'[{"remarks":"BASE AUTO"}]'))
        self.assertEqual(len(response), 2)
        self.assertEqual(response[-1]['remarks'], vc.DEFAULT['name'])


class CopyAllFrontendTests(unittest.TestCase):
    def test_copy_button_exists_and_uses_original_links(self):
        html = (ROOT / 'web_subscription/templates/auto_admin.html').read_text(encoding='utf8')
        js = (ROOT / 'web_subscription/static/vless_admin.js').read_text(encoding='utf8')
        self.assertIn('id="vCopyAll"', html)
        self.assertIn("$('vCopyAll').addEventListener('click'", js)
        self.assertIn("state.nodes.map(node=>node.uri).join('\\n')", js)
        self.assertIn("navigator.clipboard.writeText(content)", js)
        self.assertIn("document.execCommand('copy')", js)
        self.assertIn("$('vCopyAll').disabled=state.nodes.length===0", js)
        self.assertIn('static/vless_admin.js?v=20261105-2', html)


if __name__ == '__main__':
    unittest.main()
