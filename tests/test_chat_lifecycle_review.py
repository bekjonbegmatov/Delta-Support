"""Regression checks for lifecycle races and partial infrastructure outages."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram.error import BadRequest
from tortoise import Tortoise

from modules.bot import SupportBot
from modules.config import Config
from modules.database import Chat, Database
from web.routers import api_chats


class LifecycleReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["modules.database"]})
        await Tortoise.generate_schemas()
        self.db = Database(Config(_env_file=None))
        self.bot = SupportBot(self.db.config, self.db)
        self.bot.application = SimpleNamespace(bot=AsyncMock())
        self.bot.refresh_runtime_settings = AsyncMock()
        self.past = datetime.now(timezone.utc) - timedelta(days=91)

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def test_retention_progresses_past_failed_batch(self):
        for number in range(100):
            await Chat.create(user_id=number + 1, last_message_at=self.past,
                              topic_id=number + 1, topic_group_id=-100123)
        later = await Chat.create(user_id=200, last_message_at=self.past)
        self.bot.application.bot.delete_forum_topic.side_effect = BadRequest("Not enough rights to manage topics")
        with patch("modules.bot.logger"):
            # Each bounded run may process 100 entries, but permanent failures
            # must not starve entries after the first batch on every hourly run.
            await self.bot._run_retention_cleanup()
            await self.bot._run_retention_cleanup()
        self.assertFalse(await Chat.filter(id=later.id).exists())
        self.assertEqual(await Chat.all().count(), 100)

    async def test_cache_cleanup_failure_does_not_hide_successful_deletion(self):
        chat = await Chat.create(user_id=1, last_message_at=self.past, topic_id=77, topic_group_id=-100123)
        self.bot.redis = Mock()
        self.bot.redis.delete.side_effect = ConnectionError("Cache unavailable")
        self.bot.ws_manager = SimpleNamespace(broadcast=AsyncMock())
        with patch("modules.bot.logger"):
            await self.bot._run_retention_cleanup()
        self.assertFalse(await Chat.filter(id=chat.id).exists())
        self.bot.ws_manager.broadcast.assert_awaited_once_with("chat_deleted", {"chat_id": chat.id})

    async def test_fresh_client_message_cancels_stale_reminder(self):
        chat = await Chat.create(user_id=1, last_message_at=self.past)
        await self.db.add_message(chat.id, 1, "Я уже вернулся, вопрос актуален", "user")
        await self.bot._send_inactivity_reminder(chat, datetime.now(timezone.utc))
        self.bot.application.bot.send_message.assert_not_awaited()
        self.assertIsNone((await Chat.get(id=chat.id)).reminder_sent_at)

    async def test_fresh_client_message_cancels_stale_auto_close(self):
        chat = await Chat.create(user_id=1, last_message_at=self.past, reminder_sent_at=self.past)
        await self.db.add_message(chat.id, 1, "Продолжаем", "user")
        self.bot._close_chat = AsyncMock()
        await self.bot._auto_close_chat(chat)
        self.bot._close_chat.assert_not_awaited()
        self.assertEqual((await Chat.get(id=chat.id)).status, "active")

    async def test_web_status_events_do_not_extend_chat_retention(self):
        chat = await Chat.create(user_id=1, last_message_at=self.past)
        user = SimpleNamespace(id=99, role="admin")
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            bot=self.bot, ws_manager=SimpleNamespace(broadcast=AsyncMock()),
        )))
        with patch.object(api_chats, "_notify_client_background"), patch.object(api_chats, "_edit_group_topic_background"):
            for operation in (api_chats.join_chat, api_chats.close_chat, api_chats.back_to_ai):
                await operation(request, chat.id, user)
                self.assertEqual((await Chat.get(id=chat.id)).last_message_at, self.past)


if __name__ == "__main__":
    unittest.main()
