import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import subscription_service
import xui_connection


class ActiveInboundsTests(unittest.TestCase):
    def test_enabled_only_when_options_include_status(self):
        options = [
            {'id': 1, 'remark': 'Включён', 'protocol': 'vless', 'enable': True},
            {'id': 2, 'remark': 'Выключен', 'protocol': 'vless', 'enable': False},
            {'id': 3, 'remark': 'Служебный', 'protocol': 'http', 'enable': True},
        ]
        with patch.object(xui_connection, 'get_inbounds_options', new_callable=AsyncMock, return_value=options), \
             patch.object(xui_connection, 'get_inbounds_enabled_map', new_callable=AsyncMock) as enabled:
            result = asyncio.run(subscription_service.list_inbounds())
        self.assertEqual([x['id'] for x in result], [1])
        enabled.assert_not_awaited()

    def test_options_without_status_are_checked_against_panel_list(self):
        options = [
            {'id': 1, 'remark': 'Включён', 'protocol': 'vless'},
            {'id': 2, 'remark': 'Выключен', 'protocol': 'vless'},
            {'id': 3, 'remark': 'Удалён', 'protocol': 'vless'},
        ]
        with patch.object(xui_connection, 'get_inbounds_options', new_callable=AsyncMock, return_value=options), \
             patch.object(xui_connection, 'get_inbounds_enabled_map', new_callable=AsyncMock, return_value={'1': True, '2': False}):
            result = asyncio.run(subscription_service.list_inbounds())
        self.assertEqual([x['id'] for x in result], [1])

    def test_status_lookup_failure_does_not_show_unverified_connections(self):
        with patch.object(xui_connection, 'get_inbounds_options', new_callable=AsyncMock, return_value=[{'id': 1, 'protocol': 'vless'}]), \
             patch.object(xui_connection, 'get_inbounds_enabled_map', new_callable=AsyncMock, side_effect=RuntimeError('3x-ui unavailable')):
            with self.assertRaises(RuntimeError):
                asyncio.run(subscription_service.list_inbounds())

    def test_full_list_keeps_enable_flag(self):
        response = {'obj': [{'id': 1, 'enable': True}, {'id': 2, 'enable': False}]}
        with patch.object(xui_connection, '_request', new_callable=AsyncMock, return_value=response):
            result = asyncio.run(xui_connection.get_inbounds_enabled_map())
        self.assertEqual(result, {'1': True, '2': False})
