import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from modules.openai_api import (
    OpenAIAPIError, apply_chat_compatibility, model_options, request_responses, responses_payload,
)
from web.routers import api_ai


TOOL = {"type": "function", "function": {"name": "create_payment", "parameters": {
    "type": "object", "properties": {"amount": {"type": "integer"}, "method": {"type": "string"}}, "required": ["amount"],
}}}


def final_response(text="Готово"):
    return {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]}


class SSE(httpx.AsyncByteStream):
    def __init__(self, events):
        self.events = events
        self.exhausted = False

    async def __aiter__(self):
        for event in self.events:
            data = ("event: " + event["type"] + "\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()
            for start in range(0, len(data), 5):
                yield data[start:start + 5]
        self.exhausted = True


class OpenAITransportTests(unittest.IsolatedAsyncioTestCase):
    def test_legacy_openai_limits_and_optional_strict_arguments(self):
        source = {"model": "gpt-5.1", "max_tokens": 4096, "temperature": 0.7}
        converted = apply_chat_compatibility(source, "openai")
        self.assertEqual(converted["max_completion_tokens"], 4096)
        self.assertNotIn("temperature", converted)
        self.assertIn("max_tokens", source)
        self.assertEqual(apply_chat_compatibility(source, "deepseek"), source)
        generic = {"model": "deepseek-chat", "max_tokens": 4096, "temperature": 0.7}
        self.assertEqual(apply_chat_compatibility(generic, "openai"), generic)
        self.assertIn("max_completion_tokens", apply_chat_compatibility({**source, "model": "chatgpt-4o-latest"}, "openai"))
        payload = responses_payload({"model": "gpt-5.1", "messages": [], "tools": [TOOL]})
        schema = payload["tools"][0]["parameters"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["required"], ["amount", "method"])
        self.assertEqual(schema["properties"]["method"]["type"], ["string", "null"])
        self.assertEqual(TOOL["function"]["parameters"]["properties"]["method"]["type"], "string")

    async def test_multimodal_tool_roundtrip_preserves_reasoning_without_storage(self):
        requests = []
        reasoning = {"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": "opaque-reasoning"}
        call = {"id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "create_payment", "arguments": '{"amount":200,"method":null}'}

        def handler(request):
            self.assertEqual(str(request.url), "https://test.invalid/v1/responses")
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "completed", "output": [reasoning, call]} if len(requests) == 1 else final_response())

        payload = {"model": "gpt-5.1", "max_tokens": 4096, "tools": [TOOL], "messages": [
            {"role": "system", "content": "Support instructions"},
            {"role": "user", "content": [{"type": "text", "text": "Вот чек"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}]},
        ]}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await request_responses(client, "https://test.invalid/v1/chat/completions", {}, payload)
            message = result["choices"][0]["message"]
            self.assertEqual(message["tool_calls"][0]["id"], "call_1")
            payload["messages"].extend([message, {"role": "tool", "tool_call_id": "call_1", "content": '{"ok":true}'}])
            await request_responses(client, "https://test.invalid/v1", {}, payload)
        self.assertEqual(requests[0]["input"][0]["content"][1]["type"], "input_image")
        self.assertFalse(requests[0]["store"])
        self.assertEqual(requests[1]["input"][1:3], [reasoning, call])
        self.assertEqual(requests[1]["input"][3]["type"], "function_call_output")
        self.assertNotIn("max_tokens", requests[0])

    async def test_stream_is_incremental_and_uses_terminal_response(self):
        answer = "Подключение восстановлено, попробуйте открыть страницу. " * 4
        stream = SSE([
            {"type": "response.output_text.delta", "delta": answer[:100]},
            {"type": "response.output_text.delta", "delta": answer[100:]},
            {"type": "response.completed", "response": final_response(answer)},
        ])
        previews = []

        async def receive(text):
            if text:
                self.assertFalse(stream.exhausted)
                previews.append(text)

        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream,
        ))) as client:
            result = await request_responses(client, "https://test.invalid", {}, {"model": "gpt-5.1", "messages": []}, receive)
        self.assertGreaterEqual(len(previews), 2)
        self.assertEqual(result["choices"][0]["message"]["content"], answer)

    async def test_tool_arguments_reasoning_and_failed_streams_are_never_final_answers(self):
        call = {"type": "function_call", "call_id": "call_1", "name": "create_payment", "arguments": '{"amount":200,"method":null}'}
        events = [
            {"type": "response.reasoning_text.delta", "delta": "private reasoning"},
            {"type": "response.output_item.added", "item": {**call, "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "delta": '{"amount":200'},
            {"type": "response.completed", "response": {"status": "completed", "output": [call]}},
        ]
        callback = AsyncMock()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=SSE(events),
        ))) as client:
            result = await request_responses(client, "https://test.invalid", {}, {"model": "gpt-5.1", "messages": []}, callback)
        self.assertEqual([call.args[0] for call in callback.await_args_list], [""])
        self.assertEqual(result["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"], call["arguments"])
        for terminal in ([], [{"type": "response.incomplete", "response": {"status": "incomplete"}}], [{"type": "response.failed"}]):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=SSE(terminal),
            ))) as client:
                with self.assertRaises(OpenAIAPIError):
                    await request_responses(client, "https://test.invalid", {}, {"model": "gpt-5.1", "messages": []}, callback)

    async def test_explicit_stream_rejection_falls_back_but_incomplete_never_does(self):
        requests = []

        def handler(request):
            body = json.loads(request.content)
            requests.append(body)
            if body.get("stream"):
                return httpx.Response(400, json={"error": {"message": "stream is not supported"}})
            return httpx.Response(200, json=final_response())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await request_responses(client, "https://test.invalid", {}, {"model": "gpt-5.1", "messages": []}, AsyncMock())
        self.assertEqual(len(requests), 2)
        self.assertNotIn("stream", requests[-1])
        self.assertEqual(result["choices"][0]["message"]["content"], "Готово")
        requests.clear()

        def incomplete(request):
            requests.append(request)
            return httpx.Response(200, json={"status": "incomplete", "output": [], "incomplete_details": {"reason": "max_output_tokens"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(incomplete)) as client:
            with self.assertRaises(OpenAIAPIError) as error:
                await request_responses(client, "https://test.invalid", {}, {"model": "gpt-5.1", "messages": []}, AsyncMock())
        self.assertEqual(error.exception.code, "incomplete_response")
        self.assertEqual(len(requests), 1)

    async def test_http_errors_do_not_expose_provider_bodies(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            401, json={"error": {"message": "secret-key-123"}},
        ))) as client:
            with self.assertRaises(OpenAIAPIError) as error:
                await request_responses(client, "https://test.invalid", {}, {"model": "gpt-5.1", "messages": []})
        self.assertNotIn("secret-key", str(error.exception))
        self.assertEqual(error.exception.status_code, 401)

    def test_model_discovery_does_not_recommend_specialized_models(self):
        result = model_options({"data": [{"id": name} for name in ["gpt-image-1", "text-embedding-3-small", "gpt-audio", "gpt-5.1", "o3"]]}, "openai_v2")
        self.assertEqual({item["id"] for item in result if item["likely_chat"]}, {"gpt-5.1", "o3"})


class ProviderEndpointsTests(unittest.IsolatedAsyncioTestCase):
    async def test_models_use_saved_key_server_side(self):
        requests = []
        provider = SimpleNamespace(api_type="openai_v2", api_key="saved-secret", base_url=None, model_name="gpt-5.1")

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"data": [{"id": "gpt-5.1"}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        request = SimpleNamespace(json=AsyncMock(return_value={"provider_id": 7}))
        with patch.object(api_ai.AIProvider, "get_or_none", new_callable=AsyncMock, return_value=provider), patch.object(api_ai.httpx, "AsyncClient", return_value=client):
            result = await api_ai.discover_models(request, SimpleNamespace(role="admin"))
        self.assertEqual(requests[0].headers["authorization"], "Bearer saved-secret")
        self.assertEqual(str(requests[0].url), "https://api.openai.com/v1/models")
        self.assertNotIn("saved-secret", json.dumps(result))

    async def test_v2_connection_test_uses_responses_and_legacy_uses_new_token_parameter(self):
        for api_type, endpoint in (("openai_v2", "/v1/responses"), ("openai", "/v1/chat/completions")):
            requests = []

            def handler(request):
                requests.append(request)
                return httpx.Response(200, json=final_response() if api_type == "openai_v2" else {"choices": [{"message": {"content": "OK"}}]})

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            request = SimpleNamespace(json=AsyncMock(return_value={"api_type": api_type, "api_key": "secret", "model_name": "gpt-5.1"}))
            with patch.object(api_ai.httpx, "AsyncClient", return_value=client):
                result = await api_ai.test_provider(request, SimpleNamespace(role="admin"))
            self.assertTrue(result["ok"])
            self.assertEqual(requests[0].url.path, endpoint)
            body = json.loads(requests[0].content)
            self.assertNotIn("max_tokens", body)
            self.assertNotIn("temperature", body)

    async def test_manager_cannot_discover_models_or_test_credentials(self):
        for endpoint in (api_ai.discover_models, api_ai.test_provider):
            request = SimpleNamespace(json=AsyncMock())
            with self.assertRaises(HTTPException) as error:
                await endpoint(request, SimpleNamespace(role="manager"))
            self.assertEqual(error.exception.status_code, 403)
            request.json.assert_not_awaited()
