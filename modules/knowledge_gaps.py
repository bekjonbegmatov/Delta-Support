"""Evidence-backed suggestions for admins; never generates published KB content."""

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional

from tortoise.expressions import F
from tortoise.transactions import in_transaction

from modules.database import Chat, KnowledgeBaseEntry, KnowledgeGap, KnowledgeGapObservation


REPORT_KNOWLEDGE_GAP_TOOL = {
    "type": "function",
    "function": {
        "name": "report_knowledge_gap",
        "description": (
            "Тихо предложить администратору недостающую тему базы знаний. Используй только если "
            "в предоставленной базе знаний действительно нет нужной инструкции или факта о сервисе. "
            "Сначала проверь имеющиеся статьи и доступные инструменты. Не используй для сведений "
            "о конкретном аккаунте, временной ошибки API, общих приветствий и уже описанных тем. "
            "Обобщи тему и вопрос: запрещено включать имена, ID, логины, ссылки подписок, ключи, "
            "номера платежей и другие персональные данные. Это не вызывает менеджера и не публикует статью."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Краткое название недостающей инструкции, до 255 символов"},
                "reason": {"type": "string", "description": "Какого проверенного знания не хватает для решения, до 1000 символов"},
                "example_question": {"type": "string", "description": "Обобщенный вопрос клиента без персональных данных, до 500 символов"},
            },
            "required": ["topic", "reason", "example_question"],
            "additionalProperties": False,
        },
    },
}


def _normalise_topic(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return " ".join(re.findall(r"[^\W_]+", value, flags=re.UNICODE))


def _clean_summary(value: str, limit: int) -> str:
    """Defense in depth; the model must already supply a generalised question."""
    value = re.sub(r"(?:https?://|vless://|vmess://|ss://|tg://)\S+", "[ссылка]", value, flags=re.I)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[\w-]+|@[\w]+", "[контакт]", value)
    value = re.sub(r"\b(?:sk-|sess-)[\w-]+|\b[0-9a-f]{8}-[0-9a-f-]{27,}\b|\b\d{6,}\b", "[скрыто]", value, flags=re.I)
    return " ".join(value.split())[:limit].strip()


async def record_knowledge_gap(
    topic: str, reason: str, example_question: str, chat_id: Optional[int] = None,
) -> dict:
    """Count each chat once per normalised topic, without reopening reviewed items."""
    if chat_id is None:
        return {"ok": True, "status": "ignored", "reason": "not_a_customer_chat"}
    if not all(isinstance(value, str) and value.strip() for value in (topic, reason, example_question)):
        return {"ok": False, "error": "topic_reason_and_example_required"}

    topic = _clean_summary(topic, 255)
    reason = _clean_summary(reason, 1000)
    example_question = _clean_summary(example_question, 500)
    normalised = _normalise_topic(topic)
    if len(normalised) < 4:
        return {"ok": False, "error": "topic_too_short"}
    fingerprint = hashlib.sha256(normalised.encode("utf-8")).hexdigest()

    async with in_transaction() as connection:
        if not await Chat.filter(id=chat_id).using_db(connection).exists():
            return {"ok": True, "status": "ignored", "reason": "chat_not_found"}
        titles = await KnowledgeBaseEntry.filter(is_active=True).using_db(connection).values_list("title", flat=True)
        if any(_normalise_topic(title) == normalised for title in titles):
            return {"ok": True, "status": "already_covered"}
        gap, _ = await KnowledgeGap.get_or_create(
            fingerprint=fingerprint,
            defaults={"topic": topic, "reason": reason, "example_question": example_question, "last_chat_id": chat_id},
            using_db=connection,
        )
        _, new_observation = await KnowledgeGapObservation.get_or_create(
            gap_id=gap.id, chat_id=chat_id, using_db=connection,
        )
        now = datetime.now(timezone.utc)
        update = {"last_seen_at": now, "updated_at": now, "last_chat_id": chat_id}
        if new_observation:
            update["mention_count"] = F("mention_count") + 1
        await KnowledgeGap.filter(id=gap.id).using_db(connection).update(**update)
        return {"ok": True, "status": gap.status, "suggestion_id": gap.id, "new_observation": new_observation}
