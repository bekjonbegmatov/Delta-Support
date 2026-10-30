import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
from tortoise import Tortoise

from modules.database import Chat, KnowledgeBaseEntry, KnowledgeGap, KnowledgeGapObservation
from modules.knowledge_gaps import record_knowledge_gap
from web.routers import api_kb


ADMIN = SimpleNamespace(role="admin")
MANAGER = SimpleNamespace(role="manager")


def request(body):
    return SimpleNamespace(json=AsyncMock(return_value=body))


class KnowledgeGapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["modules.database"]})
        await Tortoise.generate_schemas()
        self.chat = await Chat.create(user_id=101)

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def report(self, topic="Настройка роутера", chat_id=None):
        return await record_knowledge_gap(
            topic=topic, reason="Нет инструкции для роутера.",
            example_question="Как подключить VPN на роутере?",
            chat_id=self.chat.id if chat_id is None else chat_id,
        )

    async def test_deduplicates_case_punctuation_and_repeated_tool_calls(self):
        first = await self.report()
        repeated = await self.report("  НАСТРОЙКА — РОУТЕРА! ")
        self.assertEqual(first["suggestion_id"], repeated["suggestion_id"])
        self.assertFalse(repeated["new_observation"])
        second_chat = await Chat.create(user_id=202)
        await self.report(chat_id=second_chat.id)
        gap = await KnowledgeGap.get(id=first["suggestion_id"])
        self.assertEqual(gap.mention_count, 2)
        self.assertEqual(await KnowledgeGap.all().count(), 1)
        self.assertEqual(await KnowledgeGapObservation.all().count(), 2)

    async def test_skips_admin_tasks_unknown_chats_and_existing_articles(self):
        result = await record_knowledge_gap("Настройка роутера", "Нет инструкции", "Как настроить?")
        self.assertEqual(result["status"], "ignored")
        result = await self.report(chat_id=99999)
        self.assertEqual(result["status"], "ignored")
        await KnowledgeBaseEntry.create(title="Настройка роутера!", content="Проверенная инструкция")
        self.assertEqual((await self.report())["status"], "already_covered")
        self.assertEqual(await KnowledgeGap.all().count(), 0)

    async def test_closed_suggestions_stay_closed_on_new_reports(self):
        report = await self.report()
        gap_id = report["suggestion_id"]
        await api_kb.update_suggestion(gap_id, request({"status": "dismissed"}), ADMIN)
        second_chat = await Chat.create(user_id=202)
        self.assertEqual((await self.report(chat_id=second_chat.id))["status"], "dismissed")
        self.assertEqual(await api_kb.list_suggestions("open", ADMIN), [])
        self.assertEqual(len(await api_kb.list_suggestions("dismissed", ADMIN)), 1)
        await api_kb.update_suggestion(gap_id, request({"status": "open"}), ADMIN)
        self.assertEqual(len(await api_kb.list_suggestions("open", ADMIN)), 1)

    async def test_saving_verified_article_resolves_suggestion_atomically(self):
        report = await self.report()
        result = await api_kb.create_entry(request({
            "title": "Настройка роутера", "content": "Проверенные шаги.",
            "suggestion_id": report["suggestion_id"],
        }), ADMIN)
        gap = await KnowledgeGap.get(id=report["suggestion_id"])
        self.assertEqual(gap.status, "resolved")
        self.assertEqual(gap.entry_id, result["id"])
        with self.assertRaises(HTTPException) as error:
            await api_kb.create_entry(request({
                "title": "Повтор", "content": "Статья", "suggestion_id": gap.id,
            }), ADMIN)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(await KnowledgeBaseEntry.all().count(), 1)

    async def test_disabled_draft_resolves_only_when_activated(self):
        report = await self.report()
        result = await api_kb.create_entry(request({
            "title": "Настройка роутера", "content": "Черновик", "is_active": False,
            "suggestion_id": report["suggestion_id"],
        }), ADMIN)
        self.assertEqual((await KnowledgeGap.get(id=report["suggestion_id"])).status, "open")
        await api_kb.update_entry(result["id"], request({"is_active": True}), ADMIN)
        self.assertEqual((await KnowledgeGap.get(id=report["suggestion_id"])).status, "resolved")

    async def test_invalid_suggestion_does_not_create_orphan_article(self):
        with self.assertRaises(HTTPException) as error:
            await api_kb.create_entry(request({"title": "X", "content": "Y", "suggestion_id": 999}), ADMIN)
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(await KnowledgeBaseEntry.all().count(), 0)

    async def test_suggestion_access_is_admin_only(self):
        for operation in (
            lambda: api_kb.list_suggestions("all", MANAGER),
            lambda: api_kb.update_suggestion(1, request({"status": "dismissed"}), MANAGER),
            lambda: api_kb.create_entry(request({"title": "X", "content": "Y", "suggestion_id": 1}), MANAGER),
        ):
            with self.assertRaises(HTTPException) as error:
                await operation()
            self.assertEqual(error.exception.status_code, 403)
        # Existing manager workflow for a verified article from a solved chat remains available.
        self.assertTrue((await api_kb.create_entry(request({"title": "X", "content": "Y"}), MANAGER))["ok"])

    async def test_invalid_status_is_rejected(self):
        for operation in (
            lambda: api_kb.list_suggestions("unknown", ADMIN),
            lambda: api_kb.update_suggestion(1, request({"status": "unknown"}), ADMIN),
        ):
            with self.assertRaises(HTTPException) as error:
                await operation()
            self.assertEqual(error.exception.status_code, 400)

    async def test_example_is_redacted_and_chat_deletion_cleans_evidence(self):
        result = await record_knowledge_gap(
            "Ошибка подключения", "Нужна инструкция",
            "Не работает https://example.test/sub/secret у @customer ID 123456789 sk-secretkey",
            self.chat.id,
        )
        gap = await KnowledgeGap.get(id=result["suggestion_id"])
        for secret in ("https://", "secret", "@customer", "123456789"):
            self.assertNotIn(secret, gap.example_question)
        await self.chat.delete()
        gap = await KnowledgeGap.get(id=gap.id)
        self.assertIsNone(gap.last_chat_id)
        self.assertEqual(await KnowledgeGapObservation.all().count(), 0)
        self.assertEqual(gap.mention_count, 1)


if __name__ == "__main__":
    unittest.main()
