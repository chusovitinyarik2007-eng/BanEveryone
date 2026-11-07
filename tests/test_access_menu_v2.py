"""Behavior checks without depending on a running Telegram bot or aiogram."""
import ast
import asyncio
from html import escape
import hashlib
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest

import bot_access
import classes
import db_driver

ROOT = Path(__file__).resolve().parents[1]


class FakeButton:
    def __init__(self, text, callback_data):
        self.text = text
        self.callback_data = callback_data


class FakeMarkup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard


class FakeBuilder:
    def __init__(self):
        self.items = []
        self.widths = []

    def add(self, item):
        self.items.append(item)

    def adjust(self, *widths):
        self.widths = widths

    def as_markup(self):
        rows, offset = [], 0
        for n in self.widths:
            rows.append(self.items[offset:offset+n])
            offset += n
        if offset < len(self.items):
            rows.append(self.items[offset:])
        return FakeMarkup(rows)


def load_pure_functions(path, names, env):
    """Load isolated pure renderers; no bot tokens, network or aiogram required."""
    source = ast.parse(path.read_text())
    nodes = [node for node in source.body if isinstance(node, ast.FunctionDef)
             and node.name in names]
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), env)
    return env


class AccessMenuV2Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        previous = classes.DB_PATH
        self.addCleanup(setattr, classes, 'DB_PATH', previous)
        classes.DB_PATH = os.path.join(temporary.name, 'subserver.db')
        db_driver.init_db()
        bot_access.init()

    def test_device_numbers_actions_share_one_row(self):
        env = load_pure_functions(ROOT / 'userBot/button.py',
            {'device_token', '_markup', '_nav', 'get_devices_keyboard'},
            {'hashlib':hashlib, 'InlineKeyboardButton':FakeButton,
             'InlineKeyboardMarkup':FakeMarkup})
        owner = classes.user(uuid='sub-id', current_device=[
            {'hwid':'hwid-A', 'device_model':'Mac'},
            {'hwid':'hwid-B', 'device_model':'Mac'},
        ])
        buttons = env['get_devices_keyboard'](owner).inline_keyboard
        self.assertEqual(len(buttons[0]), 2)
        self.assertEqual(len(buttons[1]), 2)
        self.assertEqual(buttons[0][0].text, '❌ Отключиться 1. Mac')
        self.assertEqual(buttons[0][1].text, '🚫 Заблокировать 1. Mac')
        self.assertEqual(buttons[1][0].text, '❌ Отключиться 2. Mac')
        self.assertNotEqual(buttons[0][0].callback_data, buttons[1][0].callback_data)
        self.assertEqual(buttons[1][1].text, '🚫 Заблокировать 2. Mac')

    def test_menu_lists_blocked_and_last_requests_with_username(self):
        for i in range(1001, 1014):
            bot_access.request_access(i, f'user{i}')
        bot_access.set_status(1001, 'blocked')
        bot_access.set_status(1002, 'approved')
        env = load_pure_functions(ROOT / 'tgbot.py',
            {'_botaccess_markup','_botaccess_home_keyboard','_botaccess_username',
             '_botaccess_list_data','_botaccess_user_data'},
            {'InlineKeyboardBuilder':FakeBuilder,
             'InlineKeyboardButton':FakeButton, 'html_escape':escape,
             'bot_access':bot_access,
             '_BOTACCESS_PAGE':8,
             '_BOTACCESS_STATUSES':{'approved':'✅ Разрешён','pending':'⏳ Заявка',
               'revoked':'🔒 Доступ отозван', 'blocked':'🚫 Заблокирован','new':'⚪ Новый'}})
        text, keyboard = env['_botaccess_list_data']('all', 0)
        self.assertIn('ID:', text)
        self.assertIn('@user', text)
        self.assertEqual(sum(1 for row in keyboard.inline_keyboard for button in row
                             if button.callback_data.startswith('botadm:user:')), 8)
        self.assertIn('botadm:list:all:1', [b.callback_data for r in keyboard.inline_keyboard for b in r])
        blocked, keyboard = env['_botaccess_list_data']('blocked', 0)
        self.assertIn('@user1001', blocked)
        self.assertNotIn('@user1002', blocked)
        profile, keyboard = env['_botaccess_user_data'](1001)
        self.assertIn('@user1001', profile)
        self.assertIn('botadm:act:unblock:1001',
                      [b.callback_data for r in keyboard.inline_keyboard for b in r])

    def test_last_requests_show_username_and_no_duplicate_pending(self):
        self.assertEqual(bot_access.count_requests(), 0)
        self.assertEqual(bot_access.request_access(3001, 'applicant'), 'created')
        self.assertEqual(bot_access.request_access(3001, 'applicant'), 'pending')
        self.assertEqual(bot_access.count_requests(), 1)
        item = bot_access.list_requests()[0]
        self.assertEqual(item['username'], 'applicant')
        self.assertEqual(item['status'], 'pending')
        self.assertTrue(bot_access.resolve(3001, 'pending', 'revoked'))
        self.assertEqual(bot_access.list_requests()[0]['status'], 'revoked')
        self.assertEqual(bot_access.request_access(3001, 'applicant'), 'created')
        self.assertEqual(bot_access.count_requests(), 2)
        bot_access.set_status(3001, 'blocked')
        self.assertEqual(bot_access.list_requests()[0]['status'], 'blocked')
        self.assertEqual(bot_access.list_requests()[1]['status'], 'revoked')

    def test_legacy_username_from_request_is_read_only(self):
        with sqlite3.connect(classes.DB_PATH) as con:
            con.execute('INSERT INTO subscription_requests(request_type,tg_id,username,months) VALUES(?,?,?,?)',
                        ('new',333,'legacy_name',1))
        bot_access.set_status(333,'approved')
        self.assertEqual(bot_access.get_user(333)['username'],'legacy_name')
        self.assertEqual(bot_access.list_users('approved')[0]['username'],'legacy_name')

    def test_full_ban_user_notification_is_exact(self):
        source = ast.parse((ROOT / 'tgbot.py').read_text())
        func = next(n for n in source.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == '_apply_bot_access_action')
        env = {'bot_access':bot_access}
        disabled=[]
        async def set_enabled(tg_id, enabled):
            disabled.append((tg_id,enabled))
            return []
        env['_set_panel_clients_enabled'] = set_enabled
        exec(compile(ast.Module(body=[func], type_ignores=[]), 'tgbot.py', 'exec'), env)
        result, notification = asyncio.run(env['_apply_bot_access_action'](999,'block'))
        self.assertEqual(notification, 'Доступ к боту был заблокирован')
        self.assertEqual(bot_access.status(999),'blocked')
        self.assertEqual(disabled,[(999,False)])


if __name__ == '__main__':
    unittest.main()
