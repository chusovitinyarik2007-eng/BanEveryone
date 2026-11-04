"""Regression checks for Vless config frontend DOM IDs and script cache version."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class VlessAdminFrontendTests(unittest.TestCase):
    def test_all_static_vless_js_dom_ids_exist_in_html(self):
        js = (ROOT / 'web_subscription/static/vless_admin.js').read_text(encoding='utf-8')
        html = (ROOT / 'web_subscription/templates/auto_admin.html').read_text(encoding='utf-8')
        ids = set(re.findall(r'\bid\s*=\s*["\']([^"\']+)', html))
        referenced = set(re.findall(r'\$\(["\']([^"\']+)["\']\)', js))
        self.assertTrue(referenced <= ids, f'Unknown DOM IDs: {referenced - ids}')
        self.assertIn("$('vlessStatus').textContent", js)
        self.assertNotIn("$('vStatus')", js)

    def test_vless_script_is_cache_busted(self):
        html = (ROOT / 'web_subscription/templates/auto_admin.html').read_text(encoding='utf-8')
        self.assertRegex(html, r'src="static/vless_admin\.js\?v=[0-9-]+"')

if __name__ == '__main__':
    unittest.main()
