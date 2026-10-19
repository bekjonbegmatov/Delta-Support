"""Ephemeral Telegram previews; the caller always sends the final message normally."""

import logging
import secrets
import time

from telegram.error import RetryAfter, TelegramError

logger = logging.getLogger(__name__)


class TelegramDraft:
    def __init__(self, bot, chat_id, message_thread_id=None):
        self.bot = bot
        self.chat_id = chat_id
        self.message_thread_id = message_thread_id
        self.draft_id = secrets.randbelow(2**31 - 1) + 1
        self.disabled = False
        self.next_update = 0.0
        self.retry_until = 0.0
        self.last_text = ""

    async def update(self, text):
        # Telegram counts UTF-16 code units; leave room for astral emoji.
        text = text.encode("utf-16-le")[:8000].decode("utf-16-le", errors="ignore")
        if self.disabled or text == self.last_text:
            return
        now = time.monotonic()
        if now < self.retry_until:
            return
        if text and now < self.next_update:
            return
        try:
            data = {"chat_id": self.chat_id, "draft_id": self.draft_id, "text": text}
            if self.message_thread_id is not None:
                data["message_thread_id"] = self.message_thread_id
            # PTB 22.5 predates this method. Reuse its authenticated transport
            # (including custom Bot API URLs); prefer the public method on upgrade.
            method = getattr(self.bot, "send_message_draft", None)
            if method is not None:
                await method(**data, read_timeout=3.0, write_timeout=3.0)
            else:
                await self.bot._post("sendMessageDraft", data, read_timeout=3.0, write_timeout=3.0)
            self.last_text = text
            self.next_update = time.monotonic() + 1.0
        except RetryAfter as exc:
            delay = exc.retry_after
            delay = delay.total_seconds() if hasattr(delay, "total_seconds") else float(delay)
            self.retry_until = time.monotonic() + delay
        except TelegramError:
            # Older Bot API servers, unsupported chats and network errors fall
            # back to one final message. Do not retry a failing draft per token.
            self.disabled = True
            logger.info("Telegram drafts unavailable; using a completed reply")
