import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from modules.ai_support import AISupport
from modules.user_info import PendingAction, UserInfoService


class SupportToolContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ai = object.__new__(AISupport)
        self.service = object.__new__(UserInfoService)
        self.service.tool_enabled = Mock(return_value=True)
        self.subscriptions = [
            {"id": 41, "username": "46235_first", "display_name": "46235_first", "tarif": "Премиум", "short_id": "first_short"},
            {"id": 42, "username": "46235_second", "display_name": "46235_second", "tarif": "Премиум", "short_id": "second_short"},
        ]
        self.service.get_own_subscriptions = AsyncMock(return_value=self.subscriptions)
        self.service.get_subscription_info = AsyncMock(return_value={"ok": True, "subscription": self.subscriptions[1]})
        self.service.get_subscription_devices = AsyncMock(return_value={"ok": True, "devices": [{"hwid": "hwid_2"}]})
        self.service.get_payment_methods = AsyncMock(return_value={"ok": True, "methods": [
            {"code": "platega_sbp", "title": "Platega СБП", "currency": "RUB", "min_amount": 50, "max_amount": 9999},
        ]})

    async def call(self, name, args):
        return await self.ai._execute_tool_call(name, args, 123456789, self.service)

    async def test_same_tariff_actions_keep_selected_username_and_id(self):
        for tool in ("reset_devices", "revoke_subscription", "delete_device"):
            with self.subTest(tool=tool):
                kind, action = await self.call(tool, {"subscription_username": "46235_second", "hwid": "hwid_2"})
                self.assertEqual(kind, "pending_action")
                self.assertIsInstance(action, PendingAction)
                self.assertEqual(action.params["subscription_username"], "46235_second")
                self.assertEqual(action.params["subscription_id"], 42)
                self.assertIn("46235_second", action.confirm_text)
                self.assertNotIn("46235_first", action.confirm_text)
                self.assertNotIn("Премиум", action.confirm_text)
                if tool == "revoke_subscription":
                    self.assertIn("Срок подписки не продлевается", action.confirm_text)

    async def test_select_by_id_and_followup_passes_canonical_identity(self):
        for tool in ("get_subscription_info", "get_subscription_devices"):
            kind, result = await self.call(tool, {"subscription_id": 42})
            self.assertEqual(kind, "result")
            self.assertTrue(result["ok"])
            getattr(self.service, tool).assert_awaited_once_with(username="46235_second", subscription_id=42)

    async def test_mismatched_ids_tariff_and_owner_login_do_not_select_subscription(self):
        for args in (
            {"subscription_username": "46235_first", "subscription_id": 42},
            {"subscription_username": "Премиум"},
            {"subscription_username": "telegram_owner"},
            {"subscription_id": 99},
        ):
            with self.subTest(args=args):
                kind, result = await self.call("reset_devices", args)
                self.assertEqual(kind, "denied")
                self.assertNotIsInstance(result, PendingAction)

    async def test_old_response_without_display_name_uses_subscription_username(self):
        self.subscriptions[1].pop("display_name")
        kind, action = await self.call("delete_device", {"subscription_id": 42, "hwid": "hwid_2"})
        self.assertEqual(kind, "pending_action")
        self.assertIn("46235_second", action.confirm_text)
        self.assertNotIn("Премиум", action.confirm_text)

    async def test_missing_code_does_not_turn_tariff_into_identifier(self):
        self.subscriptions[1].pop("display_name")
        self.subscriptions[1].pop("username")
        kind, action = await self.call("reset_devices", {"subscription_id": 42})
        self.assertEqual(kind, "pending_action")
        self.assertIsNone(action.params["subscription_username"])
        self.assertEqual(action.params["subscription_id"], 42)
        self.assertIn("ID 42", action.confirm_text)
        self.assertNotIn("Премиум", action.confirm_text)
        kind, _ = await self.call("reset_devices", {"subscription_username": "Премиум"})
        self.assertEqual(kind, "denied")

    async def test_invalid_or_missing_identifiers_never_guess_first_subscription(self):
        for args in ({}, {"subscription_id": True}, {"subscription_id": -1}, {"subscription_id": "42"}, {"subscription_username": ["46235_second"]}):
            with self.subTest(args=args):
                kind, result = await self.call("reset_devices", args)
                self.assertEqual(kind, "result")
                self.assertFalse(result["ok"])
        self.service.get_own_subscriptions.assert_not_awaited()

    async def test_new_and_retry_proposals_never_allocate_uuid_or_call_checkout(self):
        self.service.create_payment_link = AsyncMock()
        with patch("modules.ai_support.uuid4") as allocate_uuid:
            for mode in ("new", "retry"):
                kind, action = await self.call("create_payment", {"amount": 2000, "method": "platega_sbp", "request_mode": mode})
                self.assertEqual(kind, "pending_action")
                self.assertEqual(action.params["request_mode"], mode)
                self.assertEqual(action.params["amount"], 2000)
                self.assertNotIn("idempotency_key", action.params)
                self.assertNotIn("metadata", action.params)
                self.assertNotIn("email", action.params)
            allocate_uuid.assert_not_called()
        self.service.create_payment_link.assert_not_awaited()

    async def test_malformed_payment_intent_does_not_silently_change_amount(self):
        for args in ({"amount": True}, {"amount": 2000.5}, {"amount": 2000, "request_mode": "automatically_new"}):
            kind, result = await self.call("create_payment", args)
            self.assertEqual(kind, "result")
            self.assertFalse(result["ok"])
        self.service.get_payment_methods.assert_not_awaited()

    async def test_retry_keeps_exact_operation_and_does_not_query_current_methods(self):
        self.service.get_payment_methods = AsyncMock(return_value={"ok": False, "error": "method_unavailable"})
        kind, action = await self.call("create_payment", {"request_mode": "retry", "operation_id": 71})
        self.assertEqual(kind, "pending_action")
        self.assertEqual(action.params, {"request_mode": "retry", "operation_id": 71})
        self.service.get_payment_methods.assert_not_awaited()

    async def test_retry_preserves_optional_original_params_without_new_limits(self):
        kind, action = await self.call("create_payment", {
            "request_mode": "retry", "operation_id": 71, "amount": 20000, "method": "previous_method",
        })
        self.assertEqual(kind, "pending_action")
        self.assertEqual(action.params, {"request_mode": "retry", "operation_id": 71, "amount": 20000, "method": "previous_method"})
        self.service.get_payment_methods.assert_not_awaited()

    async def test_invalid_retry_identifiers_and_missing_new_amount_are_rejected(self):
        for args in (
            {"request_mode": "retry", "operation_id": True},
            {"request_mode": "retry", "operation_id": -1},
            {"request_mode": "retry", "operation_id": "71"},
            {"request_mode": "retry", "operation_id": 71, "amount": 2000.5},
            {"request_mode": "retry", "operation_id": 71, "method": []},
            {"request_mode": "new"},
        ):
            with self.subTest(args=args):
                kind, result = await self.call("create_payment", args)
                self.assertEqual(kind, "result")
                self.assertFalse(result["ok"])
        self.service.get_payment_methods.assert_not_awaited()

    async def test_check_payment_returns_fresh_backend_status(self):
        self.service.check_payment = AsyncMock(side_effect=[
            {"ok": True, "payment": {"status": "pending", "order_id": "order_2"}},
            {"ok": True, "payment": {"status": "paid", "order_id": "order_2"}},
        ])
        _, first = await self.call("check_payment", {"transaction_id": "order_2"})
        _, second = await self.call("check_payment", {"transaction_id": "order_2"})
        self.assertEqual(first["payment"]["status"], "pending")
        self.assertEqual(second["payment"]["status"], "paid")
        self.assertEqual(self.service.check_payment.await_count, 2)
        self.service.check_payment.assert_awaited_with("order_2", 123456789)

    async def test_saved_invoice_context_reaches_default_and_custom_prompt(self):
        self.ai._project_name = "Delta"
        self.ai._build_service_context = AsyncMock(return_value="")
        self.ai._build_user_context = Mock(return_value="")
        self.ai._request_chat_completion = AsyncMock(return_value={"choices": [{"message": {"content": "Проверю текущий статус счёта."}}]})
        provider = SimpleNamespace(name="test", model_name="test", base_url="https://test.invalid", api_type="openai", api_key="test", api_keys=None)
        for template in ("", "Короткий пользовательский шаблон", "Счета: {payment_context}"):
            self.ai._system_prompt = template
            await self.ai._try_provider_request(provider, "Этот счёт оплачен?", context={
                "user_id": 123456789, "payment_context": "Счёт order_2 создан. Снимок не является статусом оплаты.",
            })
            payload = self.ai._request_chat_completion.await_args.args[3]
            prompt = payload["messages"][0]["content"]
            self.assertEqual(prompt.count("Счёт order_2 создан."), 1)
            self.assertIn("Актуальную оплату проверяй только через check_payment", prompt)
