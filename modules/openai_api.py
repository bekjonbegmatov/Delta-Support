"""OpenAI Responses adapter, shared parameter compatibility and model discovery.

The public adapter accepts our chat-style history. Preserve the returned assistant
message (including _responses_output) when continuing a function-calling turn so
reasoning items and call IDs survive without server-side conversation storage.
"""

from copy import deepcopy
import json
import re

import httpx

from modules.ai_streaming import StreamingUnsupported, emit_preview, safe_preview


class OpenAIAPIError(ValueError):
    """A safe error: never includes provider bodies, credentials or request URLs."""

    def __init__(self, status_code=502, code="provider_error"):
        self.status_code = status_code
        self.code = code
        super().__init__(f"AI API HTTP {status_code} ({code})")


def endpoint_url(base_url, endpoint):
    base = (base_url or "https://api.openai.com/v1").rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/models"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    return f"{base}/{endpoint}"


def reasoning_model(model):
    return bool(re.search(r"(?:^|/|:)o[1-9](?:-|$)|(?:^|/|:)gpt-(?:[5-9]|\d{2,})(?:[.\-]|$)", model or "", re.I))


def apply_chat_compatibility(payload, api_type="openai"):
    result = deepcopy(payload)
    if api_type in ("openai", "openai_v2") and re.search(r"(?:^|/|:)(?:gpt-|chatgpt-)|(?:^|/|:)o\d", result.get("model", ""), re.I):
        if "max_tokens" in result:
            result["max_completion_tokens"] = result.pop("max_tokens")
        if reasoning_model(result.get("model")):
            result.pop("temperature", None)
            result.pop("top_p", None)
    return result


def strict_schema(schema):
    """Responses strict functions require every property; optional = nullable."""
    result = deepcopy(schema)
    if result.get("type") == "object" or "properties" in result:
        required = set(result.get("required") or [])
        properties = {}
        for key, value in (result.get("properties") or {}).items():
            value = strict_schema(value)
            if key not in required:
                if isinstance(value.get("type"), str):
                    value["type"] = [value["type"], "null"]
                    if "enum" in value and None not in value["enum"]:
                        value["enum"].append(None)
                else:
                    value = {"anyOf": [value, {"type": "null"}]}
            properties[key] = value
        result.update(type="object", properties=properties, required=list(properties), additionalProperties=False)
    if "items" in result:
        result["items"] = strict_schema(result["items"])
    for union in ("anyOf", "oneOf", "allOf"):
        if union in result:
            result[union] = [strict_schema(item) for item in result[union]]
    return result


def responses_payload(payload):
    items = []
    instructions = []
    for message in payload.get("messages") or []:
        role = message.get("role", "user")
        content = message.get("content")
        if role in ("system", "developer"):
            if content:
                instructions.append(str(content))
            continue
        if message.get("_responses_output") is not None:
            items.extend(deepcopy(message["_responses_output"]))
            continue
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": content or ""})
            continue
        if isinstance(content, list):
            parts = []
            for part in content:
                if part.get("type") in ("text", "input_text"):
                    parts.append({"type": "input_text", "text": part.get("text", "")})
                elif part.get("type") == "image_url":
                    image = part["image_url"]
                    parts.append({"type": "input_image", "image_url": image["url"] if isinstance(image, dict) else image,
                                  "detail": image.get("detail", "auto") if isinstance(image, dict) else "auto"})
                elif part.get("type") == "input_image":
                    parts.append(deepcopy(part))
            content = parts
        if content:
            items.append({"role": role, "content": content})
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            items.append({"type": "function_call", "call_id": call["id"], "name": function["name"],
                          "arguments": function.get("arguments") or "{}"})
    result = {
        "model": payload["model"], "input": items, "store": False,
        "include": ["reasoning.encrypted_content"],
        "max_output_tokens": payload.get("max_output_tokens", payload.get("max_completion_tokens", payload.get("max_tokens", 4096))),
    }
    if instructions:
        result["instructions"] = "\n\n".join(instructions)
    if payload.get("tools"):
        result["tools"] = []
        for tool in payload["tools"]:
            function = tool.get("function", tool)
            result["tools"].append({
                "type": "function", "name": function["name"], "description": function.get("description", ""),
                "parameters": strict_schema(function.get("parameters") or {"type": "object", "properties": {}}),
                "strict": True,
            })
        result["tool_choice"] = payload.get("tool_choice", "auto")
        result["parallel_tool_calls"] = False
    # Omit optional sampling controls: several reasoning families reject them.
    return result


def chat_response(response):
    if response.get("error") or response.get("status") in ("failed", "cancelled", "incomplete"):
        raise OpenAIAPIError(code="incomplete_response" if response.get("status") == "incomplete" else "response_failed")
    output = response.get("output")
    if not isinstance(output, list):
        raise OpenAIAPIError(code="invalid_response")
    texts, calls = [], []
    for item in output:
        if item.get("type") == "function_call":
            if not item.get("call_id") or not item.get("name"):
                raise OpenAIAPIError(code="invalid_function_call")
            calls.append({"id": item["call_id"], "type": "function", "function": {
                "name": item["name"], "arguments": item.get("arguments") or "{}",
            }})
        elif item.get("type") == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    texts.append(part.get("text", ""))
                elif part.get("type") == "refusal":
                    texts.append(part.get("refusal", ""))
    if not texts and not calls:
        raise OpenAIAPIError(code="empty_response")
    message = {"role": "assistant", "content": "\n".join(texts) or None, "_responses_output": output}
    if calls:
        message["tool_calls"] = calls
    return {"choices": [{"message": message}], "usage": response.get("usage")}


def raise_api_error(response):
    codes = {400: "invalid_request", 401: "invalid_api_key", 403: "access_denied", 404: "model_or_endpoint_not_found",
             408: "timeout", 429: "rate_limit", 500: "provider_error", 502: "provider_error", 503: "provider_unavailable"}
    code = codes.get(response.status_code, "provider_error")
    if response.status_code == 400:
        try:
            error = response.json().get("error") or {}
            message = str(error.get("message", "")).lower()
            parameter = error.get("param")
            if parameter in ("max_tokens", "max_completion_tokens", "temperature", "tools", "input", "messages") and any(
                word in message for word in ("unsupported", "not supported", "does not support")
            ):
                code = "unsupported_" + parameter
            elif "responses" in message and any(word in message for word in ("only", "required", "must")):
                code = "responses_required"
            elif any(word in message for word in ("image", "vision")) and any(word in message for word in ("unsupported", "not supported", "does not support")):
                code = "images_unsupported"
        except (ValueError, AttributeError, TypeError):
            pass
    raise OpenAIAPIError(response.status_code, code)


async def _responses_stream(client, url, headers, payload, callback):
    await emit_preview(callback, "")
    async with client.stream("POST", url, headers=headers, json={**payload, "stream": True}, timeout=90.0) as response:
        if response.status_code != 200:
            await response.aread()
            error = response.text.lower()
            if response.status_code in (400, 422, 501) and "stream" in error and any(
                word in error for word in ("not support", "unsupported", "not allowed", "unknown", "unrecognized", "not implement")
            ):
                raise StreamingUnsupported()
            raise_api_error(response)
        if "application/json" in response.headers.get("content-type", ""):
            await response.aread()
            return chat_response(response.json())
        content, last_preview = "", ""
        has_calls = False
        data_lines = []
        completed = None

        async def consume(data):
            nonlocal content, last_preview, has_calls, completed
            if data == "[DONE]":
                return
            event = json.loads(data)
            kind = event.get("type", "")
            if kind in ("error", "response.failed", "response.incomplete"):
                raise OpenAIAPIError(code="incomplete_response" if kind == "response.incomplete" else "response_failed")
            if kind == "response.output_item.added" and (event.get("item") or {}).get("type") == "function_call":
                has_calls = True
            elif kind == "response.function_call_arguments.delta":
                has_calls = True
            elif kind == "response.output_text.delta":
                content += event.get("delta") or ""
            elif kind == "response.completed":
                completed = chat_response(event.get("response") or {})
                return
            preview = "" if has_calls else safe_preview(content)
            if preview != last_preview:
                await emit_preview(callback, preview)
                last_preview = preview

        async for line in response.aiter_lines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif not line and data_lines:
                await consume("\n".join(data_lines))
                data_lines = []
                if completed is not None:
                    break
        if data_lines and completed is None:
            await consume("\n".join(data_lines))
        if completed is None:
            raise OpenAIAPIError(code="stream_interrupted")
        return completed


async def request_responses(client, url, headers, payload, callback=None):
    url = endpoint_url(url, "responses")
    body = responses_payload(payload)
    if callback is not None:
        try:
            return await _responses_stream(client, url, headers, body, callback)
        except StreamingUnsupported:
            # Only retry after an explicit rejection, before any generated output.
            pass
    response = await client.post(url, headers=headers, json=body, timeout=90.0)
    if response.status_code != 200:
        raise_api_error(response)
    return chat_response(response.json())


def model_options(data, api_type):
    """The models endpoint exposes IDs, not definitive endpoint capabilities."""
    result = []
    for item in data.get("data") or []:
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        specialized = re.search(r"embedding|moderation|whisper|tts|dall-e|image|audio|realtime|transcrib|sora|search-api", model_id, re.I)
        likely_chat = not specialized and (api_type not in ("openai", "openai_v2") or bool(re.search(r"(?:^|:)(?:gpt-|chatgpt-)|(?:^|:)o\d", model_id)))
        result.append({"id": model_id, "owned_by": item.get("owned_by"), "likely_chat": bool(likely_chat)})
    return sorted(result, key=lambda item: (not item["likely_chat"], item["id"]))
