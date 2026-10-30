import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from telegram.error import BadRequest, TimedOut
from tortoise import Tortoise

from modules.bot import SupportBot
from modules.config import Config
from modules.database import Chat, Database, ManagerNotification, Message, SystemConfig
from web.routers import api_settings, api_chats


class ChatLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["modules.database"]})
        await Tortoise.generate_schemas()
        self.config = Config(_env_file=None)
        self.db = Database(self.config)
        self.bot = SupportBot(self.config, self.db)
        self.bot.application = SimpleNamespace(bot=AsyncMock())
        self.bot.refresh_runtime_settings = AsyncMock()
        self.user = SimpleNamespace(id=12345, username="client", first_name="Client", last_name=None)

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def old_chat(self, **kwargs):
        past = datetime.now(timezone.utc) - timedelta(days=91)
        return await Chat.create(user_id=self.user.id, last_message_at=past, **kwargs)

    def update(self, info=None):
        message = SimpleNamespace(
            text="Как подключить VPN?", caption=None, message_id=22, message_thread_id=None,
            sticker=None, animation=None, photo=None, video=None, audio=None, voice=None,
            document=None, video_note=None, reply_text=AsyncMock(), reply_to_message=None,
        )
        if info:
            for key, value in info.items():
                setattr(message, key, value)
        return SimpleNamespace(effective_user=self.user, message=message,
            effective_message=message, effective_chat=SimpleNamespace(type="private", id=self.user.id))

    async def test_closed_chat_reopens_in_place_with_history_and_topic(self):
        chat = await Chat.create(user_id=self.user.id, status="closed", topic_id=77,
            manager_id=99, assigned_admin_id=4, ai_disabled=True)
        await self.db.add_message(chat.id, self.user.id, "Раньше уже подключали", "user")
        reopened, _ = await self.bot._save_client_message(self.user, {"kind": "text", "text": "Снова ошибка"})
        self.assertEqual(reopened.id, chat.id)
        self.assertEqual(reopened.topic_id, 77)
        self.assertEqual(reopened.status, "active")
        self.assertFalse(reopened.ai_disabled)
        self.assertIsNone(reopened.manager_id)
        self.assertIsNone(reopened.assigned_admin_id)
        self.assertEqual(await Chat.all().count(), 1)
        self.assertEqual(await Message.filter(chat_id=chat.id).count(), 2)

    async def test_open_chat_wins_over_more_recent_closed_duplicate(self):
        active = await Chat.create(user_id=self.user.id, status="waiting_manager")
        await Chat.create(user_id=self.user.id, status="closed")
        found = await self.db.get_chat_by_user_id(self.user.id)
        self.assertEqual(found.id, active.id)

    async def test_history_returns_latest_messages_chronologically(self):
        chat = await Chat.create(user_id=self.user.id)
        for number in range(25):
            await self.db.add_message(chat.id, self.user.id, str(number), "user")
        messages = await self.db.get_chat_messages(chat.id, limit=3)
        self.assertEqual([m.content for m in messages], ["22", "23", "24"])

    async def test_system_events_do_not_extend_retention(self):
        chat = await self.old_chat()
        await self.db.add_message(chat.id, self.user.id, "Служебная запись", "system")
        refreshed = await Chat.get(id=chat.id)
        self.assertEqual(refreshed.last_message_at, chat.last_message_at)

    async def test_handoff_clears_old_reminder_and_never_checks_waiting_client(self):
        old = datetime.now(timezone.utc) - timedelta(days=2)
        chat = await Chat.create(user_id=self.user.id, last_message_at=old, reminder_sent_at=old)
        await self.db.update_chat_status(chat.id, "waiting_manager")
        refreshed = await Chat.get(id=chat.id)
        self.assertIsNone(refreshed.reminder_sent_at)
        self.assertIsNotNone(refreshed.waiting_since)
        # Even reminders left by older releases must not auto-close waiting chats.
        await Chat.filter(id=chat.id).update(reminder_sent_at=old)
        await self.bot._run_auto_close()
        self.bot.application.bot.send_message.assert_not_awaited()
        self.assertEqual((await Chat.get(id=chat.id)).status, "waiting_manager")

    async def test_autoclose_reminds_active_ai_chat_but_not_disabled_or_waiting(self):
        past = datetime.now(timezone.utc) - timedelta(days=2)
        active = await Chat.create(user_id=self.user.id)
        for chat in (active, await Chat.create(user_id=2, status="waiting_manager"), await Chat.create(user_id=3, ai_disabled=True)):
            await self.db.add_message(chat.id, chat.user_id, "Попробуйте ещё раз", "ai")
            await Chat.filter(id=chat.id).update(last_message_at=past)
        await self.bot._run_auto_close()
        self.bot.application.bot.send_message.assert_awaited_once()
        self.assertEqual(self.bot.application.bot.send_message.call_args.kwargs["chat_id"], self.user.id)

    async def test_waiting_client_message_is_forwarded_without_ai(self):
        await Chat.create(user_id=self.user.id, status="waiting_manager")
        self.bot._forward_to_manager = AsyncMock()
        self.bot.ai.get_ai_answer = AsyncMock()
        await self.bot.handle_any_message(self.update(), None)
        self.bot._forward_to_manager.assert_awaited_once()
        self.bot.ai.get_ai_answer.assert_not_awaited()

    async def test_repeated_handoff_does_not_notify_again(self):
        chat = await Chat.create(user_id=self.user.id, status="waiting_manager")
        query = SimpleNamespace(edit_message_text=AsyncMock())
        self.bot._build_chat_summary = AsyncMock()
        await self.bot.request_manager(query, chat.id)
        self.bot._build_chat_summary.assert_not_awaited()
        self.bot.application.bot.send_message.assert_not_awaited()

    async def test_persisted_topic_survives_empty_redis(self):
        chat = await Chat.create(user_id=self.user.id, topic_id=77)
        self.bot._group_id = -100123
        self.bot.redis = Mock()
        self.bot.redis.get.return_value = None
        self.assertEqual(await self.bot._ensure_group_topic(chat), 77)
        self.bot.application.bot.create_forum_topic.assert_not_awaited()
        self.bot.redis.set.assert_any_call("group_topic:thread:-100123:77", str(chat.id))
        self.assertEqual((await Chat.get(id=chat.id)).topic_group_id, -100123)

    async def test_topic_commands_and_replies_work_without_redis(self):
        chat = await Chat.create(user_id=self.user.id, topic_id=77, topic_group_id=-100123)
        self.bot._group_id = -100123
        update = self.update({"message_thread_id": 77})
        update.effective_chat.id = -100123
        found = await self.bot._chat_from_topic(update)
        self.assertEqual(found.id, chat.id)

    async def test_group_lookup_rejects_other_groups_and_stale_redis(self):
        wrong = await Chat.create(user_id=1, topic_id=77, topic_group_id=-100999)
        right = await Chat.create(user_id=2, topic_id=77, topic_group_id=-100123)
        self.bot._group_id = -100123
        self.bot.redis = Mock()
        self.bot.redis.get.return_value = str(wrong.id)
        update = self.update({"message_thread_id": 77})
        update.effective_chat.id = -100123
        self.assertEqual((await self.bot._chat_from_topic(update)).id, right.id)
        await right.delete()
        self.assertIsNone(await self.bot._chat_from_topic(update))
        await self.bot._edit_group_topic_status(wrong)
        self.bot.application.bot.edit_forum_topic.assert_not_awaited()

    async def test_group_manager_reply_survives_redis_outage(self):
        chat = await Chat.create(user_id=2, topic_id=77, topic_group_id=-100123, status="waiting_manager")
        self.bot._group_id = -100123
        self.bot.redis = Mock()
        self.bot.redis.get.side_effect = ConnectionError("Redis unavailable")
        self.bot.redis.set.side_effect = ConnectionError("Redis unavailable")
        self.bot.redis.setex.side_effect = ConnectionError("Redis unavailable")
        self.bot.config = SimpleNamespace(get_all_staff_ids=lambda: [self.user.id])
        self.bot._send_to_client = AsyncMock()
        update = self.update({"message_thread_id": 77})
        update.effective_chat.id = -100123
        await self.bot.handle_any_message(update, None)
        self.bot._send_to_client.assert_awaited_once()
        self.assertEqual(await Message.filter(chat_id=chat.id, message_type="manager").count(), 1)

    async def test_web_delete_keeps_history_on_telegram_failure(self):
        chat = await self.old_chat(topic_id=77, topic_group_id=-100123)
        await Message.create(chat_id=chat.id, user_id=self.user.id, content="Keep this")
        self.bot.application.bot.delete_forum_topic.side_effect = TimedOut()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot=self.bot, ws_manager=AsyncMock())))
        with self.assertRaises(HTTPException) as error:
            await api_chats.delete_chat(chat.id, request, SimpleNamespace(role="admin"))
        self.assertEqual(error.exception.status_code, 502)
        self.assertTrue(await Chat.filter(id=chat.id).exists())
        self.assertTrue(await Message.filter(chat_id=chat.id).exists())
        self.bot.application.bot.delete_forum_topic.side_effect = None
        await api_chats.delete_chat(chat.id, request, SimpleNamespace(role="admin"))
        self.assertFalse(await Chat.filter(id=chat.id).exists())
        self.bot.application.bot.delete_forum_topic.assert_awaited_with(chat_id=-100123, message_thread_id=77)

    async def test_retention_deletes_topic_history_and_notifications(self):
        chat = await self.old_chat(topic_id=77, topic_group_id=-100123)
        await Message.create(chat_id=chat.id, user_id=self.user.id, content="old")
        await ManagerNotification.create(chat_id=chat.id, manager_id=99)
        await self.bot._run_retention_cleanup()
        self.bot.application.bot.delete_forum_topic.assert_awaited_once_with(chat_id=-100123, message_thread_id=77)
        self.assertFalse(await Chat.filter(id=chat.id).exists())
        self.assertEqual(await Message.all().count(), 0)
        self.assertEqual(await ManagerNotification.all().count(), 0)

    async def test_retention_preserves_chat_when_telegram_temporarily_fails(self):
        chat = await self.old_chat(topic_id=77, topic_group_id=-100123)
        self.bot.application.bot.delete_forum_topic.side_effect = TimedOut()
        await self.bot._run_retention_cleanup()
        self.assertTrue(await Chat.filter(id=chat.id).exists())
        self.assertEqual((await Chat.get(id=chat.id)).topic_id, 77)
        self.bot.application.bot.delete_forum_topic.side_effect = None
        await self.bot._run_retention_cleanup()
        self.assertFalse(await Chat.filter(id=chat.id).exists())

    async def test_retention_treats_already_deleted_topic_as_success(self):
        chat = await self.old_chat(topic_id=77, topic_group_id=-100123)
        self.bot.application.bot.delete_forum_topic.side_effect = BadRequest("Message thread not found")
        await self.bot._run_retention_cleanup()
        self.assertFalse(await Chat.filter(id=chat.id).exists())

    async def test_retention_preserves_chat_when_topic_permissions_missing(self):
        chat = await self.old_chat(topic_id=77, topic_group_id=-100123)
        self.bot.application.bot.delete_forum_topic.side_effect = BadRequest("Not enough rights to manage topics")
        await self.bot._run_retention_cleanup()
        self.assertTrue(await Chat.filter(id=chat.id).exists())

    async def test_retention_preserves_recent_or_disabled(self):
        recent = await Chat.create(user_id=1, last_message_at=datetime.now(timezone.utc))
        old = await self.old_chat()
        self.bot._chat_retention_days = 0
        await self.bot._run_retention_cleanup()
        self.assertEqual(await Chat.all().count(), 2)
        self.bot._chat_retention_days = 90
        await self.bot._run_retention_cleanup()
        self.assertTrue(await Chat.filter(id=recent.id).exists())
        self.assertFalse(await Chat.filter(id=old.id).exists())

    async def test_retention_preserves_new_activity_during_telegram_call(self):
        chat = await self.old_chat(topic_id=77, topic_group_id=-100123)
        async def concurrent_manager_reply(**kwargs):
            await self.db.add_message(chat.id, 99, "Ответ менеджера", "manager")
        self.bot.application.bot.delete_forum_topic.side_effect = concurrent_manager_reply
        await self.bot._run_retention_cleanup()
        preserved = await Chat.get(id=chat.id)
        self.assertIsNone(preserved.topic_id)
        self.assertTrue(await Message.filter(chat_id=chat.id).exists())

    async def test_image_document_uses_verified_mime(self):
        info = {"kind": "document", "file_id": "png", "file_name": "screenshot.png", "mime_type": "application/octet-stream"}
        self.assertTrue(self.bot._is_image_info(info))
        self.bot._download_tg_file = AsyncMock(return_value=b"\x89PNG\r\n\x1a\nimage")
        self.assertTrue((await self.bot._photo_to_data_url(info)).startswith("data:image/png;base64,"))
        self.bot._download_tg_file.return_value = b"%PDF-1.0 wrong-content"
        self.assertIsNone(await self.bot._photo_to_data_url(info))

    async def test_download_rechecks_size_after_receiving_bytes(self):
        file = SimpleNamespace(file_size=None, download_as_bytearray=AsyncMock(return_value=bytearray(b"12345")))
        self.bot.application.bot.get_file.return_value = file
        self.assertIsNone(await self.bot._download_tg_file("id", max_size=4))

    async def test_image_reaches_ai_with_tools_and_history_without_current_message(self):
        chat = await Chat.create(user_id=self.user.id)
        await self.db.add_message(chat.id, self.user.id, "Предыдущий вопрос", "user")
        await self.db.add_message(chat.id, self.user.id, "Внутренняя запись", "system")
        self.bot.ai.get_ai_answer = AsyncMock(return_value="Нажмите подключить в приложении.")
        self.bot.user_info.get_ai_context = AsyncMock(return_value=None)
        self.bot._photo_to_data_url = AsyncMock(return_value="data:image/png;base64,aW1hZ2U=")
        self.bot._start_typing = Mock(return_value=(None, None))
        self.bot._ai_stream_callback = Mock(return_value=None)
        self.bot._maybe_send_device_delete_confirmation = AsyncMock(return_value=False)
        update = self.update({"text": None, "document": SimpleNamespace(file_id="png", mime_type="image/png", file_name="screen.png")})
        await self.bot.handle_any_message(update, None)
        call = self.bot.ai.get_ai_answer.call_args
        self.assertEqual(call.kwargs["image_data_url"], "data:image/png;base64,aW1hZ2U=")
        self.assertTrue(call.kwargs["execute_tools"])
        self.assertEqual(call.args[1]["chat_id"], chat.id)
        self.assertEqual(call.args[2], [{"role": "user", "message": "Предыдущий вопрос"}])
        # The word "подключить" alone must never show the manager button.
        self.assertNotIn("reply_markup", update.message.reply_text.call_args.kwargs)

    async def test_automation_retention_zero_and_invalid_validation(self):
        admin = SimpleNamespace(role="admin")
        request = SimpleNamespace(json=AsyncMock(return_value={"chat_retention_days": "0"}))
        with patch.object(api_settings, "_invalidate_runtime_caches"):
            await api_settings.put_automation_settings(request, admin)
        result = await api_settings.get_automation_settings(admin)
        self.assertEqual(result["effective"]["chat_retention_days"], 0)
        request.json.return_value = {"auto_close_enabled": True, "chat_retention_days": -1}
        with self.assertRaises(HTTPException):
            await api_settings.put_automation_settings(request, admin)
        self.assertFalse(await SystemConfig.filter(key="auto_close_enabled").exists())


if __name__ == "__main__":
    unittest.main()
