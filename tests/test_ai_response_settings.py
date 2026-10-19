import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from modules.ai_support import AISupport
from modules.config import Config
from web.routers import api_settings


class ResponseSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_mode_roundtrip_runtime_refresh_and_reset(self):
        values = {}

        async def update_or_create(key, defaults):
            values[key] = defaults["value"]

        def query(**filters):
            async def all_rows():
                return [SimpleNamespace(key=key, value=value) for key, value in values.items()
                    if key in filters.get("key__in", [filters.get("key")])]

            async def delete():
                values.pop(filters["key"], None)

            return SimpleNamespace(all=all_rows, delete=delete)

        config = Config(_env_file=None)
        db = SimpleNamespace(filter=query, update_or_create=update_or_create)
        providers = Mock()
        providers.filter.return_value.order_by.return_value.all = AsyncMock(return_value=[])
        admin = SimpleNamespace(role="admin")
        with patch.object(api_settings, "SystemConfig", db), patch.object(api_settings, "Config", return_value=config), \
             patch("modules.ai_support.SystemConfig", db), patch("modules.ai_support.AIProvider", providers):
            result = await api_settings.get_bot_settings(admin)
            self.assertEqual(result["effective"]["ai_response_mode"], "standard")
            ai = AISupport(config)
            for mode, expected in (("streaming", "streaming"), ("standard", "standard"), (None, "standard")):
                request = SimpleNamespace(json=AsyncMock(return_value={"ai_response_mode": mode}))
                self.assertEqual(await api_settings.put_bot_settings(request, admin), {"ok": True})
                result = await api_settings.get_bot_settings(admin)
                self.assertEqual(result["effective"]["ai_response_mode"], expected)
                await ai._refresh_runtime_settings(force=True)
                self.assertEqual(ai.response_mode, expected)

    async def test_invalid_mode_rejected_before_any_write(self):
        request = SimpleNamespace(json=AsyncMock(return_value={
            "project_name": "Must not be written", "ai_response_mode": "invalid",
        }))
        with patch.object(api_settings.SystemConfig, "update_or_create", new_callable=AsyncMock) as write:
            with self.assertRaises(HTTPException) as error:
                await api_settings.put_bot_settings(request, SimpleNamespace(role="admin"))
            self.assertEqual(error.exception.status_code, 400)
            write.assert_not_awaited()

    async def test_manager_cannot_change_response_mode(self):
        request = SimpleNamespace(json=AsyncMock(return_value={"ai_response_mode": "streaming"}))
        with self.assertRaises(HTTPException) as error:
            await api_settings.put_bot_settings(request, SimpleNamespace(role="manager"))
        self.assertEqual(error.exception.status_code, 403)
        request.json.assert_not_awaited()
