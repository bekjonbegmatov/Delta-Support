import json
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

from modules.config import Config
from modules.user_info import UserInfoService, subscription_display_name


HTTP_CLIENT = httpx.AsyncClient
OWNER = 123456789
SUBSCRIPTIONS = [
    {"id": 41, "username": "46235_41nr3", "display_name": "46235_41nr3", "tarif": "Премиум", "is_currently_active": True},
    {"id": 42, "username": "46235_other", "display_name": "46235_other", "tarif": "Премиум", "is_currently_active": True},
    {"id": 43, "username": "46235_legacy", "tarif": "Премиум", "is_currently_active": False},
    {"id": 44, "tarif": "Премиум", "is_currently_active": False},
]


class SupportAPIContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = UserInfoService(Config(_env_file=None))
        self.service.active_channel = "support_api"
        self.service.support_api_url = "https://support.invalid"
        self.service.support_api_token = "server-only-test-token"
        self.service._refresh_settings = AsyncMock()

    def transport(self, handler):
        return patch("modules.user_info.httpx.AsyncClient", side_effect=lambda: HTTP_CLIENT(transport=httpx.MockTransport(handler)))

    def account(self):
        return {"ok": True, "user": {"telegram_id": OWNER, "username": "telegram_owner"},
                "connections": {"remna_subscriptions": deepcopy(SUBSCRIPTIONS)}}

    async def test_get_account_uses_telegram_id_and_preserves_unique_subscription_codes(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/api/support/user_info")
            self.assertEqual(request.url.params["user_id"], str(OWNER))
            self.assertEqual(request.content, b"")
            self.assertEqual(request.headers["authorization"], "Bearer server-only-test-token")
            return httpx.Response(200, json=self.account())
        with self.transport(handler):
            result = await self.service.get_user_info(OWNER)
        subs = result["connections"]["remna_subscriptions"]
        self.assertEqual([sub["id"] for sub in subs], [41, 42, 43, 44])
        self.assertEqual(subs[2]["display_name"], "46235_legacy")
        self.assertIsNone(subs[3]["display_name"])
        for formatter in (self.service.format_for_ai, self.service.format_for_manager):
            text = formatter(result)
            for code in ("46235_41nr3", "46235_other", "46235_legacy"):
                self.assertIn(code, text)
            self.assertIn("Подписка без кода", text)
        self.assertNotEqual(subscription_display_name(subs[3]), "Премиум")
        self.assertEqual(len(requests), 1)

    async def test_selection_does_not_confuse_two_identical_tariffs_or_owner_username(self):
        self.service.get_user_info = AsyncMock(return_value=self.account())
        selected = await self.service.find_own_subscription(OWNER, username="46235_other", subscription_id=42)
        self.assertEqual(selected["id"], 42)
        self.assertIsNone(await self.service.find_own_subscription(OWNER, username="Премиум"))
        self.assertIsNone(await self.service.find_own_subscription(OWNER, username="telegram_owner"))
        self.assertIsNone(await self.service.find_own_subscription(OWNER, username="46235_other", subscription_id=41))
        self.assertEqual((await self.service.find_own_subscription(OWNER, subscription_id=44))["id"], 44)
        self.assertIsNone(await self.service.find_own_subscription(OWNER))

    async def test_reads_are_get_and_mutations_use_selected_username_or_id(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"ok": True, "subscription": {"id": 42, "username": "46235_other"}, "devices": []})
        with self.transport(handler):
            info = await self.service.get_subscription_info(username="46235_other", subscription_id=42)
            await self.service.get_subscription_devices(subscription_id=42)
            await self.service.delete_device(username="46235_other", subscription_id=42, hwid="device-42")
            await self.service.reset_devices(username="46235_other", subscription_id=42)
            await self.service.revoke_subscription(subscription_id=42)
        self.assertEqual(info["subscription"]["display_name"], "46235_other")
        self.assertEqual([(r.method, r.url.path) for r in requests], [
            ("GET", "/api/support/subscription_info"),
            ("GET", "/api/support/subscription/devices"),
            ("POST", "/api/support/subscription/device/delete"),
            ("POST", "/api/support/subscription/devices/reset"),
            ("POST", "/api/support/subscription/revoke"),
        ])
        self.assertEqual(dict(requests[0].url.params), {"username": "46235_other"})
        self.assertEqual(dict(requests[1].url.params), {"subscription_id": "42"})
        self.assertEqual(json.loads(requests[2].content), {"username": "46235_other", "hwid": "device-42"})
        self.assertEqual(json.loads(requests[3].content), {"username": "46235_other"})
        self.assertEqual(json.loads(requests[4].content), {"subscription_id": 42})

    async def test_missing_identifier_never_falls_back_to_short_id_or_tariff(self):
        with self.transport(lambda request: self.fail("No HTTP request expected")):
            results = [
                await self.service.get_subscription_info(short_id="old-short-id"),
                await self.service.get_subscription_devices(),
                await self.service.reset_devices("old-short-id"),
                await self.service.delete_device(hwid="device"),
                await self.service.revoke_subscription(),
            ]
        self.assertTrue(all(result["error"] == "subscription_identifier_required" for result in results))

    async def test_new_intent_after_running_or_uncertain_and_same_key_replay(self):
        old_key = str(uuid4())
        new_key = str(uuid4())
        created = {}
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(request.url.path, "/api/support/payment/create")
            self.assertEqual(request.method, "POST")
            self.assertEqual(json.loads(request.content), {"user_id": OWNER, "method": "platega_sbp", "amount": 2000})
            key = request.headers["idempotency-key"]
            if key == old_key:
                return httpx.Response(502, json={"ok": False, "error": "checkout_uncertain"})
            if key not in created:
                created[key] = {"ok": True, "pay_link": "https://pay.invalid/order2", "order_id": "order2", "status": False}
            return httpx.Response(200, json=created[key])
        with self.transport(handler):
            uncertain = await self.service.create_payment_link(OWNER, "platega_sbp", 2000, old_key)
            result = await self.service.create_payment_link(OWNER, "platega_sbp", 2000, new_key)
            replay = await self.service.create_payment_link(OWNER, "platega_sbp", 2000, new_key)
        self.assertEqual(uncertain["http_status"], 502)
        self.assertEqual(result, replay)
        self.assertEqual(len(created), 1)
        self.assertEqual([r.headers["idempotency-key"] for r in requests], [old_key, new_key, new_key])
        self.assertNotIn("metadata", json.loads(requests[0].content))
        self.assertNotIn("email", json.loads(requests[0].content))

    async def test_error_never_retries_or_changes_key(self):
        key = str(uuid4())
        for status, code in ((409, "operation_requires_review"), (409, "idempotency_conflict"), (502, "checkout_uncertain"), (None, "request_timeout")):
            requests = []
            def handler(request):
                requests.append(request)
                if status is None:
                    raise httpx.ReadTimeout("timeout", request=request)
                return httpx.Response(status, json={"ok": False, "error": code})
            with self.transport(handler):
                result = await self.service.create_payment_link(OWNER, "platega_sbp", 2000, key)
            self.assertEqual(result["error"], code)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0].headers["idempotency-key"], key)
            if status:
                self.assertEqual(result["http_status"], status)

    async def test_current_payment_check_ignores_creation_snapshot(self):
        requests = []
        check_count = 0
        def handler(request):
            nonlocal check_count
            requests.append(request)
            if request.method == "POST":
                return httpx.Response(200, json={"ok": True, "pay_link": "https://pay.invalid/order-1", "order_id": "order-1", "status": False})
            self.assertEqual(request.url.path, "/api/support/payment/check")
            self.assertEqual(request.url.params["transaction_id"], "order-1")
            check_count += 1
            return httpx.Response(200, json={"ok": True, "user": {"telegram_id": OWNER}, "transaction": {"status": check_count > 1}})
        with self.transport(handler):
            created = await self.service.create_payment_link(OWNER, "platega_sbp", 2000, str(uuid4()))
            pending = await self.service.check_payment(created["order_id"], OWNER)
            paid = await self.service.check_payment(created["order_id"], OWNER)
        self.assertFalse(created["status"])
        self.assertFalse(pending["transaction"]["status"])
        self.assertTrue(paid["transaction"]["status"])
        self.assertEqual(check_count, 2)

    async def test_payment_check_requires_matching_owner(self):
        for user in ({}, {"telegram_id": OWNER + 1}):
            with self.transport(lambda request: httpx.Response(200, json={"ok": True, "user": user, "transaction": {"secret": "not-for-this-client"}})):
                result = await self.service.check_payment("order", OWNER)
            self.assertEqual(result, {"ok": False, "error": "payment_owner_unverified"})

    async def test_invalid_payment_parameters_make_no_request(self):
        with self.transport(lambda request: self.fail("No HTTP request expected")):
            for amount in (2000.5, True, 0):
                self.assertEqual((await self.service.create_payment_link(OWNER, "platega_sbp", amount, str(uuid4())))["error"], "invalid_amount")
            self.assertEqual((await self.service.create_payment_link(OWNER, "platega_sbp", 2000, ""))["error"], "invalid_idempotency_key")

    async def test_direct_remnawave_reset_keeps_legacy_short_id_and_new_username(self):
        self.service.active_channel = "remnawave"
        self.service.remnawave_url = "https://remna.invalid"
        self.service.remnawave_api_key = "test"
        api = AsyncMock()
        api.__aenter__.return_value = api
        api.get_user_by_short_uuid.return_value = {"uuid": "uuid1"}
        api.get_user_by_username.return_value = {"uuid": "uuid2"}
        with patch.object(self.service, "_remnawave_api", return_value=api):
            await self.service.reset_devices("short-id")
            await self.service.reset_devices(username="code")
        api.get_user_by_short_uuid.assert_awaited_once_with("short-id")
        api.get_user_by_username.assert_awaited_once_with("code")
        self.assertEqual([call.args[0] for call in api.reset_user_devices.await_args_list], ["uuid1", "uuid2"])

    def test_context_does_not_treat_pending_string_as_paid(self):
        data = self.account()
        data["transactions"] = [{"status": "pending", "order_id": "order-1", "summa": 2000}]
        text = self.service.format_for_ai(data)
        self.assertIn("ожидает оплаты", text)
        self.assertIn("order-1", text)
        self.assertIn("check_payment", text)
