"""Static integration invariants for a project requiring an external aiogram install."""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class AutoNameIntegrationTests(unittest.TestCase):
    def test_main_imports_xui_connection_globally(self):
        code = ast.parse((ROOT / "main.py").read_text())
        imports = {alias.name for node in code.body if isinstance(node, ast.Import)
                   for alias in node.names}
        self.assertIn("xui_connection", imports)
        self.assertTrue(any(isinstance(x, ast.AsyncFunctionDef) and x.name == "fetch_real_sub"
                            for x in code.body))

    def test_bot_does_not_modify_panel_inbounds_when_renaming_auto(self):
        code = ast.parse((ROOT / "tgbot.py").read_text())
        save = next(x for x in code.body if isinstance(x, ast.AsyncFunctionDef)
                    and x.name == "auto_save_auto_name")
        calls = [n for n in ast.walk(save) if isinstance(n, ast.Call)]
        self.assertTrue(any(isinstance(c.func, ast.Attribute) and c.func.attr == "set_auto_name"
                            for c in calls))
        self.assertFalse(any(isinstance(c.func, ast.Attribute) and c.func.attr in
                             ("rename_inbound", "update_inbound", "_request") for c in calls))
        self.assertFalse(hasattr(save, "auto_inbound_id"))
        bot_source = (ROOT / "tgbot.py").read_text()
        self.assertIn('("✏️ Название AUTO", "auto:rename")', bot_source)
        self.assertNotIn('Переименование инбаунда в панели 3x-ui', bot_source)
        self.assertNotIn('rename_inbound(', bot_source)
        self.assertNotIn('rename_inbound(', (ROOT / 'xui_connection.py').read_text())

    def test_bot_has_four_server_pool_menus_but_ru_rules_are_direct(self):
        code = (ROOT / "tgbot.py").read_text()
        smart = (ROOT / "smart_routing.py").read_text()
        self.assertIn('"auto:pool:{p}"', code)
        self.assertIn('"auto:rules:ru"', code)
        self.assertIn('"auto:rules:gemini"', code)
        self.assertIn('data[2] in smart_routing.POOL_NAMES', code)
        self.assertIn('"outboundTag": "auto_panel_direct"', smart)
        self.assertNotIn('"auto_lb_ru"', smart)
        self.assertIn('"all": "🔄 Все серверы"', smart)
        self.assertIn('"other": "🌍 Все сайты"', smart)
        self.assertIn('"reserve": "🛟 Антиглушилка"', smart)



if __name__ == '__main__':
    unittest.main()
