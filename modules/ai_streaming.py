"""OpenAI-compatible SSE transport and safe previews for Telegram drafts."""

import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

# Shared by preview filtering and the final-answer guard. Internal names have no
# place in a client reply, even if a provider emits them without protocol wrappers.
TOOL_PROTOCOL = re.compile(
    r"DSML|\b(?:To\s*=\s*)?functions\.|\btool_calls?\b|<\|.*?tool|"
    r"<\s*/?\s*(?:invoke|function|parameter)\b|"
    r"[\"'](?:arguments|parameters|amount|method|transaction_id|subscription_username|hwid|idempotency_key)[\"']\s*:|"
    r"\b(?:get_payment_methods|get_available_payment_methods|create_payment(?:_link|_invoice)?|"
    r"check_payment|get_subscription_info|get_subscription_devices|reset_devices|"
    r"revoke_subscription|delete_device|remove_subscription_device|"
    r"delete_subscription_device|remove_device)\b",
    re.IGNORECASE,
)


def clean_response_text(content):
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE)
    return re.sub(r"<think>.*$", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


class StreamingUnsupported(Exception):
    """The endpoint explicitly rejected the streaming parameter."""


async def emit_preview(callback, text):
    if callback is not None:
        try:
            await callback(text)
        except Exception:
            # Delivery of a temporary preview must not lose the completed answer.
            logger.warning("AI preview delivery failed")


def safe_preview(content):
    """Withhold incomplete protocol markers and model reasoning from previews.

    Keep a small tail until subsequent tokens arrive: a protocol marker may be
    split at any byte/token boundary. Final messages use the normal validation.
    """
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE)
    # Stop before any remaining tag (including an unclosed think block), DSML,
    # or the JSON/functions forms handled by the normal tool fallback.
    for pattern in (TOOL_PROTOCOL, re.compile(r"[<＜{]")):
        marker = pattern.search(text)
        if marker:
            text = text[:marker.start()]
    return text[:-48].rstrip() if len(text) > 48 else ""


async def read_chat_stream(client, url, headers, payload, callback):
    """Assemble text/tool deltas into the same shape as a non-streamed response."""
    await emit_preview(callback, "")
    async with client.stream(
        "POST", url, headers=headers, json={**payload, "stream": True}, timeout=40.0,
    ) as response:
        if response.status_code != 200:
            await response.aread()
            error = response.text.lower()
            if response.status_code in (400, 422, 501) and "stream" in error and any(
                word in error for word in ("not support", "unsupported", "not allowed", "unknown", "unrecognized", "not implement")
            ):
                raise StreamingUnsupported()
            # Do not log request headers or provider error bodies with credentials.
            raise ValueError(f"AI streaming HTTP error {response.status_code}")
        if "application/json" in response.headers.get("content-type", ""):
            # Some compatible endpoints ignore stream=true and return normal JSON.
            await response.aread()
            return response.json()

        content = ""
        calls = {}
        finished = False
        last_preview = ""
        data_lines = []

        async def consume_event(data):
            nonlocal content, finished, last_preview
            if data == "[DONE]":
                finished = True
                return
            event = json.loads(data)
            if event.get("error"):
                raise ValueError("AI streaming provider error")
            for choice in event.get("choices") or []:
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta") or {}
                if isinstance(delta.get("content"), str):
                    content += delta["content"]
                # reasoning_content is intentionally ignored.
                for fragment in delta.get("tool_calls") or []:
                    index = fragment["index"]
                    call = calls.setdefault(index, {
                        "id": "", "type": "function", "function": {"name": "", "arguments": ""},
                    })
                    if fragment.get("id"):
                        call["id"] = fragment["id"]
                    for field in ("name", "arguments"):
                        call["function"][field] += (fragment.get("function") or {}).get(field) or ""
                if choice.get("finish_reason") is not None:
                    finished = True
            preview = "" if calls else safe_preview(content)
            if preview != last_preview:
                await emit_preview(callback, preview)
                last_preview = preview

        async for line in response.aiter_lines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif not line and data_lines:
                data = "\n".join(data_lines)
                data_lines = []
                await consume_event(data)
                if data == "[DONE]":
                    break
        if data_lines:
            await consume_event("\n".join(data_lines))
        if not finished:
            raise ValueError("AI stream ended before completion")
        message = {"role": "assistant", "content": content or None}
        if calls:
            message["tool_calls"] = [calls[index] for index in sorted(calls)]
        return {"choices": [{"message": message}]}
