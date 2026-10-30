import asyncio
import base64
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import classes
import db_driver
import subscription_service
from web_subscription import sub_page_loader as page


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = classes.DB_PATH
        classes.DB_PATH = str(Path(self.tmp.name) / 'trial.db')
        db_driver.init_db()
        self.old_trial_days = classes.settings.get("trial_days")
        classes.settings["trial_days"] = 3

    def tearDown(self):
        classes.DB_PATH = self.old_db
        if self.old_trial_days is None:
            classes.settings.pop("trial_days", None)
        else:
            classes.settings["trial_days"] = self.old_trial_days
        self.tmp.cleanup()

    def test_only_once_even_after_deleting_subscription(self):
        self.assertTrue(db_driver.register_bot_visitor(9001))
        self.assertTrue(db_driver.reserve_trial(9001))
        self.assertFalse(db_driver.reserve_trial(9001))
        db_driver.finish_trial(9001, 'some-sub')
        self.assertFalse(db_driver.trial_available(9001))
        self.assertFalse(db_driver.register_bot_visitor(9001))

    def test_double_click_is_rejected_and_popup_text_is_clear(self):
        self.assertTrue(db_driver.register_bot_visitor(123456))
        self.assertTrue(db_driver.reserve_trial(123456))
        self.assertFalse(db_driver.reserve_trial(123456))
        db_driver.finish_trial(123456, 'trial-sub')
        self.assertFalse(db_driver.reserve_trial(123456))
        bot_source = (Path(__file__).resolve().parents[1] / 'userBot' / 'bot.py').read_text()
        self.assertIn('Бесплатная подписка уже была активирована.', bot_source)
        self.assertIn('show_alert=True', bot_source)
        self.assertIn('Поддерживаются только Happ и Incy.', bot_source)
        self.assertIn('добавьте её в приложение Happ или Incy', bot_source)
        self.assertIn('f"{CLIENT_HELP}"', bot_source)


        db_driver.update_name_by_uuid('old-id', 'old-user', 712)
        self.assertFalse(db_driver.register_bot_visitor(712))
        self.assertFalse(db_driver.reserve_trial(712))

    def test_concurrent_claims_only_one_wins(self):
        self.assertTrue(db_driver.register_bot_visitor(101))
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(db_driver.reserve_trial, [101] * 8))
        self.assertEqual(outcomes.count(True), 1)

    def test_custom_duration_is_used_and_persisted(self):
        from unittest.mock import AsyncMock
        async def fake_groups():
            return [], {10}, {15}
        async def fake_ensure(email, full):
            self.assertTrue(full)
        classes.settings["trial_days"] = 7
        db_driver.update_settings()
        with patch.object(subscription_service, '_inbound_groups', fake_groups), \
             patch.object(subscription_service.xui_connection, 'create_client', new_callable=AsyncMock), \
             patch.object(subscription_service, 'ensure_subscription_type_by_email', fake_ensure):
            _, _, expiry = asyncio.run(subscription_service.create_trial_subscription(777, 'trial'))
        self.assertAlmostEqual(expiry - int(time.time() * 1000), 7 * 86400000, delta=10000)
        with sqlite3.connect(classes.DB_PATH) as db:
            self.assertEqual(__import__('json').loads(db.execute(
                "SELECT value FROM settings WHERE key='trial_days'").fetchone()[0]), 7)

    def test_three_days_full_rest_and_device_limit(self):
        now = int(time.time() * 1000)
        async def fake_groups():
            return [], {10, 11}, {15, 16}
        async def fake_ensure(email, full):
            self.assertTrue(full)
        with patch.object(subscription_service, '_inbound_groups', fake_groups), \
             patch.object(subscription_service.xui_connection, 'create_client', new_callable=AsyncMock) as create, \
             patch.object(subscription_service, 'ensure_subscription_type_by_email', fake_ensure):
            sub_id, email, expiry = asyncio.run(subscription_service.create_trial_subscription(504, 'trial'))
        self.assertTrue(3*86400000-10000 < expiry-now < 3*86400000+10000)
        self.assertEqual(create.await_args.args[1], [10, 11])
        self.assertEqual(db_driver.get_owned_subscription(sub_id, 504).max_device, 2)


class PageTests(unittest.TestCase):
    def test_no_fetch_if_keys_not_visible(self):
        class User:
            name='test'
            max_device=2
            current_device=[]
        base={'obj': {'client': {'expiryTime':int(time.time()*1000)+300000, 'enable':True, 'totalGB':10**9}, 'usedTraffic':10**8}}
        async def get_info(name):
            return base if name == 'test' else {}
        class FakeTemplate:
            def TemplateResponse(self, **kwargs):
                return kwargs['context']
        with patch.object(page.db_driver, 'get_data_by', return_value=User()), \
             patch.object(page.xui_connection, 'get_user_info', side_effect=get_info), \
             patch.object(page, '_get_keys', new_callable=AsyncMock) as key_fetch, \
             patch.object(page, 'templates', FakeTemplate()):
            result=asyncio.run(page.send_sub_page(object(), 'VPN','myid'))
        self.assertFalse(result['keys_visible'])
        self.assertEqual(result['connection_keys'], [])
        self.assertIsNone(result['rest_traffic'])
        key_fetch.assert_not_awaited()

    def test_key_parser_and_base64(self):
        raw='vless://uuid@server:443?type=xhttp#Server'
        data=base64.b64encode(raw.encode()).decode().rstrip('=')
        self.assertEqual(page.decode(data),raw)
        self.assertEqual(page.parse_vless(raw)[2], 'xhttp')


if __name__ == '__main__':
    unittest.main()
