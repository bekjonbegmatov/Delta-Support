import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from tortoise import Tortoise

from modules.bot import SupportBot
from modules.config import Config
from modules.database import Chat, Database, PaymentOperation
from modules.user_info import PendingAction


class PaymentFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["modules.database"]})
        await Tortoise.generate_schemas()
        self.bot = SupportBot(Config(_env_file=None), Database(Config(_env_file=None)))
        self.bot._record_ai_tool_event = AsyncMock()
        self.bot._save_message_to_db = AsyncMock()
        self.bot.user_info.create_payment_link = AsyncMock(return_value={
            "ok": True, "pay_link": "https://pay.invalid/order", "order_id": "order-1", "status": "pending",
        })
        self.bot.user_info.check_payment = AsyncMock()
        self.chat = await Chat.create(user_id=123456789)
        self.update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
        self.query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock(), message=SimpleNamespace(reply_text=AsyncMock()))

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def proposal(self, mode="new", **params):
        action = PendingAction(action="create_payment", params={
            "method": "platega_sbp", "method_title": "Platega СБП", "amount": 2000, "currency": "RUB", "request_mode": mode, **params,
        }, confirm_text="Подтвердите")
        await self.bot._send_pending_action_confirmation(self.update, self.chat, action)
        return await PaymentOperation.filter(chat_id=self.chat.id).order_by("-id").first()

    async def invoke(self, action, operation, user_id=None):
        await self.bot._handle_payment_callback(self.query, user_id or self.chat.user_id, action, operation.id)
        await operation.refresh_from_db()

    async def test_new_key_only_after_explicit_confirmation_and_telegram_owner(self):
        operation = await self.proposal()
        self.assertIsNone(operation.idempotency_key)
        self.bot.user_info.create_payment_link.assert_not_awaited()
        await self.invoke("confirm", operation)
        self.assertEqual(str(UUID(operation.idempotency_key)), operation.idempotency_key)
        kwargs = self.bot.user_info.create_payment_link.await_args.kwargs
        self.assertEqual(kwargs["owner_telegram_id"], self.chat.user_id)
        self.assertNotEqual(kwargs["owner_telegram_id"], self.chat.id)
        self.assertEqual(kwargs["idempotency_key"], operation.idempotency_key)
        self.assertNotIn("metadata", kwargs)

    async def test_successful_replay_same_uuid_and_body_creates_only_one_invoice(self):
        invoices = {}
        calls = []
        async def backend(**params):
            calls.append(params)
            key = params["idempotency_key"]
            invoices.setdefault(key, {"ok": True, "pay_link": f"https://pay.invalid/{key}", "transaction_id": "tx-1"})
            return invoices[key]
        self.bot.user_info.create_payment_link.side_effect = backend
        operation = await self.proposal()
        await self.invoke("confirm", operation)
        await self.invoke("retry", operation)
        self.assertEqual(len(invoices), 1)
        self.assertEqual(calls[0], calls[1])
        self.assertIn("payment_check_", str(self.query.edit_message_text.await_args.kwargs["reply_markup"]))

    async def test_new_invoice_allowed_while_other_operations_running_or_uncertain(self):
        for state in ("running", "uncertain"):
            previous = await PaymentOperation.create(chat_id=self.chat.id, user_id=self.chat.user_id,
                method="platega_sbp", method_title="СБП", amount=2000, idempotency_key=str(uuid4()), state=state)
            current = await self.proposal()
            await self.invoke("confirm", current)
            await previous.refresh_from_db()
            self.assertEqual(previous.state, state)
            self.assertNotEqual(previous.idempotency_key, current.idempotency_key)
        self.assertEqual(self.bot.user_info.create_payment_link.await_count, 2)
        self.assertEqual(await PaymentOperation.all().count(), 4)

    async def test_errors_never_rotate_key_and_explicit_retries_reuse_body(self):
        errors = [
            {"ok": False, "error": "request_timeout"},
            {"ok": False, "error": "checkout_uncertain", "http_status": 502},
            {"ok": False, "error": "operation_requires_review", "http_status": 409},
            {"ok": False, "error": "idempotency_conflict", "http_status": 409},
        ]
        for error in errors:
            self.bot.user_info.create_payment_link.reset_mock()
            self.bot.user_info.create_payment_link.return_value = error
            operation = await self.proposal()
            await self.invoke("confirm", operation)
            key = operation.idempotency_key
            self.assertEqual(self.bot.user_info.create_payment_link.await_count, 1)
            await self.invoke("retry", operation)
            self.assertEqual(operation.idempotency_key, key)
            calls = self.bot.user_info.create_payment_link.await_args_list
            self.assertEqual(calls[0].kwargs, calls[1].kwargs)
            self.assertEqual(operation.state, "uncertain")
            self.assertIn("Новый счёт автоматически не создавался", self.query.edit_message_text.await_args.args[0])

    async def test_new_proposal_after_error_does_not_allocate_key(self):
        self.bot.user_info.create_payment_link.return_value = {"ok": False, "error": "checkout_uncertain", "http_status": 502}
        first = await self.proposal()
        await self.invoke("confirm", first)
        second = await self.proposal()
        self.assertIsNone(second.idempotency_key)
        self.assertEqual(self.bot.user_info.create_payment_link.await_count, 1)
        await self.invoke("confirm", second)
        self.assertNotEqual(first.idempotency_key, second.idempotency_key)

    async def test_payment_check_uses_fresh_status_not_creation_snapshot(self):
        self.bot.user_info.create_payment_link.return_value = {"ok": True, "pay_link": "https://pay.invalid/1", "order_id": "order1", "status": True}
        operation = await self.proposal()
        await self.invoke("confirm", operation)
        self.assertNotIn("Оплата подтверждена", self.query.edit_message_text.await_args.args[0])
        self.bot.user_info.check_payment.side_effect = [
            {"ok": True, "transaction": {"status": False}},
            {"ok": True, "transaction": {"status": True}},
        ]
        await self.invoke("check", operation)
        self.assertIn("пока не подтверждена", self.query.message.reply_text.await_args.args[0])
        await self.invoke("check", operation)
        self.assertIn("Оплата подтверждена", self.query.message.reply_text.await_args.args[0])
        self.bot.user_info.check_payment.assert_awaited_with("order1", self.chat.user_id)
        self.assertEqual(self.bot.user_info.check_payment.await_count, 2)

    async def test_retry_by_exact_operation_and_ambiguous_choices(self):
        first = await self.proposal()
        await self.invoke("confirm", first)
        second = await self.proposal()
        await self.invoke("confirm", second)
        await self.proposal("retry")
        markup = self.update.message.reply_text.await_args.kwargs["reply_markup"]
        callbacks = [row[0].callback_data for row in markup.inline_keyboard]
        self.assertEqual(set(callbacks), {f"payment_retry_{first.id}", f"payment_retry_{second.id}"})
        await self.proposal("retry", operation_id=first.id)
        markup = self.update.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, f"payment_retry_{first.id}")
        self.assertEqual(await PaymentOperation.all().count(), 2)

    async def test_retry_failure_preserves_previously_known_invoice(self):
        operation = await self.proposal()
        await self.invoke("confirm", operation)
        result = operation.result
        self.bot.user_info.create_payment_link.return_value = {"ok": False, "error": "request_timeout"}
        await self.invoke("retry", operation)
        self.assertEqual(operation.result, result)
        self.assertEqual(operation.state, "succeeded")
        self.assertIn("сохранённый ранее счёт", self.query.edit_message_text.await_args.args[0])

    async def test_foreign_callbacks_cancel_and_expired_proposals_cannot_create(self):
        operation = await self.proposal()
        await self.invoke("confirm", operation, user_id=555)
        self.bot.user_info.create_payment_link.assert_not_awaited()
        await self.invoke("cancel", operation)
        await self.invoke("confirm", operation)
        self.assertIsNone(operation.idempotency_key)
        self.bot.user_info.create_payment_link.assert_not_awaited()
        expired = await self.proposal()
        await PaymentOperation.filter(id=expired.id).update(created_at=datetime.now(timezone.utc) - timedelta(minutes=6))
        await self.invoke("confirm", expired)
        self.assertIsNone(expired.idempotency_key)
        self.bot.user_info.create_payment_link.assert_not_awaited()

    async def test_key_survives_restart_and_missing_original_redis(self):
        operation = await self.proposal()
        await self.invoke("confirm", operation)
        original_key = operation.idempotency_key
        # New bot instance uses only the persisted payment operation.
        service = self.bot.user_info
        self.bot = SupportBot(Config(_env_file=None), self.bot.db)
        self.bot.user_info = service
        self.bot._record_ai_tool_event = AsyncMock()
        self.bot._save_message_to_db = AsyncMock()
        await self.invoke("retry", operation)
        self.assertEqual(operation.idempotency_key, original_key)
        self.assertEqual(service.create_payment_link.await_args.kwargs["idempotency_key"], original_key)

    async def test_pre_upgrade_confirmation_keeps_issued_key(self):
        key = str(uuid4())
        self.bot._store_pending_action("old", {"chat_id": self.chat.id, "user_id": self.chat.user_id,
            "action": "create_payment", "params": {"method": "platega_sbp", "amount": 2000, "idempotency_key": key}})
        await self.bot._confirm_pending_action(self.query, self.chat.user_id, "old")
        await self.bot._confirm_pending_action(self.query, self.chat.user_id, "old")
        self.assertEqual(await PaymentOperation.filter(idempotency_key=key).count(), 1)
        self.assertTrue(all(call.kwargs["idempotency_key"] == key for call in self.bot.user_info.create_payment_link.await_args_list))

    async def test_audit_does_not_call_failed_backend_action_successful(self):
        text = self.bot._format_ai_tool_event({"phase": "action_result", "tool": "create_payment",
            "result": {"backend": {"ok": False, "error": "operation_requires_review"}}})
        self.assertIn("Действие завершилось ошибкой", text)
        self.assertNotIn("Действие выполнено", text)

    async def test_selected_subscription_id_is_checked_again_before_action(self):
        self.bot.user_info.find_own_subscription = AsyncMock(return_value={"id": 42, "username": "46235_41nr3", "display_name": "46235_41nr3", "tarif": "Премиум"})
        self.bot.user_info.reset_devices = AsyncMock(return_value={"ok": True})
        self.bot._store_pending_action("reset", {"chat_id": self.chat.id, "user_id": self.chat.user_id,
            "action": "reset_devices", "params": {"subscription_username": "46235_41nr3", "subscription_id": 42}})
        await self.bot._confirm_pending_action(self.query, self.chat.user_id, "reset")
        self.bot.user_info.find_own_subscription.assert_awaited_once_with(self.chat.user_id, username="46235_41nr3", subscription_id=42)
        self.bot.user_info.reset_devices.assert_awaited_once_with(None, username="46235_41nr3", subscription_id=42)

    async def test_device_shortcut_respects_explicit_subscription_code(self):
        self.bot.user_info.get_own_subscriptions = AsyncMock(return_value=[
            {"id": 41, "username": "46235_first", "display_name": "46235_first", "tarif": "Премиум"},
            {"id": 42, "username": "46235_second", "display_name": "46235_second", "tarif": "Премиум"},
        ])
        self.bot.user_info.get_subscription_devices = AsyncMock(return_value={"ok": True, "devices": [{"hwid": "iphone", "deviceName": "iPhone"}]})
        self.bot._send_pending_action_confirmation = AsyncMock()
        self.assertTrue(await self.bot._maybe_send_device_delete_confirmation(self.update, self.chat, self.chat.user_id, "Удали iPhone у подписки 46235_first"))
        self.bot.user_info.get_subscription_devices.assert_awaited_once_with("46235_first")
        action = self.bot._send_pending_action_confirmation.await_args.args[2]
        self.assertEqual(action.params["subscription_id"], 41)
        self.assertIn("46235_first", action.confirm_text)
        self.assertNotIn("Премиум", action.confirm_text)

    async def test_concurrent_workers_claim_same_operation_before_http(self):
        operation = await self.proposal()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []
        async def backend(**params):
            calls.append(params)
            started.set()
            await release.wait()
            return {"ok": True, "pay_link": "https://pay.invalid/one", "order_id": "one"}
        self.bot.user_info.create_payment_link.side_effect = backend
        second_bot = SupportBot(Config(_env_file=None), self.bot.db)
        second_bot.user_info = self.bot.user_info
        second_bot._record_ai_tool_event = AsyncMock()
        second_bot._save_message_to_db = AsyncMock()
        first = asyncio.create_task(self.invoke("confirm", operation))
        await asyncio.wait_for(started.wait(), timeout=2)
        try:
            await second_bot._handle_payment_callback(self.query, self.chat.user_id, "confirm", operation.id)
            self.assertEqual(len(calls), 1)
            self.assertIn("уже выполняется", self.query.message.reply_text.await_args.args[0])
        finally:
            release.set()
            await first
        self.assertEqual(await PaymentOperation.all().count(), 1)

    async def test_stale_callback_ack_does_not_rotate_or_lose_request(self):
        self.query.answer.side_effect = Exception("query already answered")
        operation = await self.proposal()
        await self.invoke("confirm", operation)
        key = operation.idempotency_key
        await self.invoke("retry", operation)
        self.assertEqual(operation.idempotency_key, key)
        self.assertEqual(self.bot.user_info.create_payment_link.await_count, 2)

    async def test_unknown_named_subscription_never_uses_another_device(self):
        self.bot.user_info.get_own_subscriptions = AsyncMock(return_value=[
            {"id": 41, "username": "46235_first", "display_name": "46235_first", "tarif": "Премиум"},
        ])
        self.bot.user_info.get_subscription_devices = AsyncMock(return_value={"ok": True, "devices": [{"hwid": "iphone", "deviceName": "iPhone"}]})
        self.bot._send_pending_action_confirmation = AsyncMock()
        for question in ("Удали iPhone у подписки 46235_typo", "Удали iPhone у подписки Премиум"):
            self.assertFalse(await self.bot._maybe_send_device_delete_confirmation(self.update, self.chat, self.chat.user_id, question))
        self.bot.user_info.get_subscription_devices.assert_not_awaited()
        self.bot._send_pending_action_confirmation.assert_not_awaited()
