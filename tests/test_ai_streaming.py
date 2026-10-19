import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from telegram.error import BadRequest, RetryAfter

from modules.ai_streaming import read_chat_stream, safe_preview
from modules.ai_support import AISupport
from modules.telegram_streaming import TelegramDraft
from modules.user_info import PendingAction


def event(delta=None, finish=None):
    return {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}


class SSEBytes(httpx.AsyncByteStream):
    def __init__(self, events, done=True):
        self.events = events
        self.done = done
        self.exhausted = False

    async def __aiter__(self):
        for item in self.events:
            encoded = ("data: " + json.dumps(item, ensure_ascii=False) + "\n\n").encode()
            # Split bytes too, including multibyte Cyrillic and SSE field lines.
            for start in range(0, len(encoded), 7):
                yield encoded[start:start + 7]
        if self.done:
            yield b"data: [DONE]\n\n"
        self.exhausted = True


def sse_response(events, done=True):
    return httpx.Response(
        200, headers={"content-type": "text/event-stream"}, stream=SSEBytes(events, done),
    )


class StreamTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_incremental_content_and_assembled_final(self):
        text = "Ответ поддержки появляется постепенно. " * 5
        stream = SSEBytes([event({"content": text[:90]}), event({"content": text[90:]}, "stop")])
        previews = []

        async def receive(value):
            if value:
                self.assertFalse(stream.exhausted)
                previews.append(value)

        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream,
        ))) as client:
            result = await read_chat_stream(client, "https://test.invalid", {}, {}, receive)
        self.assertEqual(result["choices"][0]["message"]["content"], text)
        self.assertGreaterEqual(len(previews), 2)
        self.assertTrue(previews[1].startswith(previews[0]))

    async def test_tool_arguments_and_reasoning_never_reach_callback(self):
        events = [
            event({"reasoning_content": "secret internal reasoning"}),
            event({"tool_calls": [{"index": 0, "id": "call1", "function": {"name": "create_payment", "arguments": '{"amount":'}}]}),
            event({"tool_calls": [{"index": 0, "function": {"arguments": '200,"method":"platega_card"}'}}]}, "tool_calls"),
        ]
        callback = AsyncMock()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: sse_response(events))) as client:
            result = await read_chat_stream(client, "https://test.invalid", {}, {}, callback)
        self.assertEqual([call.args[0] for call in callback.await_args_list], [""])
        call = result["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "create_payment")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"amount": 200, "method": "platega_card"})

    async def test_truncated_stream_is_not_a_completed_answer(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: sse_response(
            [event({"content": "Обрыв незавершённого ответа"})], done=False,
        ))) as client:
            with self.assertRaisesRegex(ValueError, "before completion"):
                await read_chat_stream(client, "https://test.invalid", {}, {}, AsyncMock())

    async def test_unsupported_stream_retries_normal_once_and_caches(self):
        requests = []

        def handle(request):
            body = json.loads(request.content)
            requests.append(body)
            if body.get("stream"):
                return httpx.Response(400, json={"error": "stream is not supported"})
            return httpx.Response(200, json={"choices": [{"message": {"content": "Готовый ответ"}}]})

        ai = object.__new__(AISupport)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            for _ in range(2):
                result = await ai._request_chat_completion(client, "https://test.invalid", {}, {"model": "test"}, AsyncMock())
                self.assertEqual(result["choices"][0]["message"]["content"], "Готовый ответ")
        self.assertEqual([item.get("stream", False) for item in requests], [True, False, False])

    async def test_nonstreaming_json_and_auth_error(self):
        ai = object.__new__(AISupport)
        for status in (200, 401):
            calls = []

            def handle(request):
                calls.append(request)
                return httpx.Response(status, json={"choices": [{"message": {"content": "Ответ"}}]})

            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                if status == 200:
                    result = await ai._request_chat_completion(client, "https://test.invalid", {}, {}, AsyncMock())
                    self.assertEqual(result["choices"][0]["message"]["content"], "Ответ")
                else:
                    with self.assertRaisesRegex(ValueError, "401"):
                        await ai._request_chat_completion(client, "https://test.invalid", {}, {}, AsyncMock())
            self.assertEqual(len(calls), 1)


class ProtocolFilterTests(unittest.TestCase):
    def test_every_partial_token_boundary_hides_protocol(self):
        prefix = "Проверяю доступные способы оплаты. " * 4
        protocols = [
            '<｜｜DSML｜｜ invoke name="create_payment"><｜｜DSML｜｜ parameter name="amount">200',
            'To=functions.create_payment {"amount":200,"method":"platega_card"}',
            'functions.create_payment {"amount":200}',
            '{"tool_calls":[{"function":{"name":"create_payment","arguments":"secret"}}]}',
            '{"amount":200,"method":"platega_card"}',
            'create_payment(amount=200, method="platega_card")',
            '<tool_call>{"name":"create_payment"}</tool_call>',
            '<think>secret reasoning which must not be visible</think>',
        ]
        for protocol in protocols:
            text = prefix + protocol + " " * 80
            for end in range(1, len(text) + 1):
                preview = safe_preview(text[:end])
                with self.subTest(protocol=protocol, end=end):
                    self.assertTrue(prefix.startswith(preview), preview)

    def test_final_guard_blocks_internal_names_and_json_arguments(self):
        for text in ('create_payment(200)', '{"amount":200,"method":"platega_card"}', '<｜｜DSML｜｜ calls>'):
            self.assertTrue(AISupport._contains_tool_protocol(text))
        self.assertFalse(AISupport._contains_tool_protocol("Пополнить баланс на 200 рублей через карту МИР?"))


class ProviderFlowTests(unittest.IsolatedAsyncioTestCase):
    def make_ai(self):
        ai = object.__new__(AISupport)
        ai._project_name = "Delta"
        ai._system_prompt = ""
        ai._build_service_context = AsyncMock(return_value="")
        ai._build_user_context = Mock(return_value="")
        ai._refresh_runtime_settings = AsyncMock()
        ai.enabled = True
        ai.response_mode = "streaming"
        self.provider = SimpleNamespace(
            name="test", model_name="test", base_url="https://test.invalid", api_type="openai",
            api_key="test-only", api_keys=None,
        )
        ai._ai_providers = [self.provider]
        return ai

    async def test_standard_mode_does_not_start_streaming(self):
        ai = self.make_ai()
        ai.response_mode = "standard"
        ai._try_provider_request = AsyncMock(return_value="Ответ")
        callback = AsyncMock()
        self.assertEqual(await ai.get_ai_answer("Вопрос", stream_callback=callback), "Ответ")
        self.assertIsNone(ai._try_provider_request.await_args.kwargs["stream_callback"])
        callback.assert_not_awaited()

    async def test_tool_followup_streams_only_answer_and_executes_once(self):
        ai = self.make_ai()
        service = Mock()
        service.get_enabled_tools.return_value = ["get_payment_methods"]
        service.tool_enabled.return_value = True
        service.get_payment_methods = AsyncMock(return_value={"ok": True, "methods": []})
        calls = []
        answer = "Сейчас нет доступных способов оплаты. Обратитесь к менеджеру. " * 2

        def handle(request):
            body = json.loads(request.content)
            calls.append(body)
            if len(calls) == 1:
                return sse_response([event({"tool_calls": [{"index": 0, "id": "call1", "function": {
                    "name": "get_payment_methods", "arguments": "{}",
                }}]}, "tool_calls")])
            return sse_response([event({"content": answer[:80]}), event({"content": answer[80:]}, "stop")])

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        callback = AsyncMock()
        with patch("modules.ai_support.httpx.AsyncClient", return_value=client):
            result = await ai.get_ai_answer("Как оплатить?", {"user_id": 123}, execute_tools=True,
                user_info_service=service, stream_callback=callback)
        self.assertEqual(result, answer.strip())
        service.get_payment_methods.assert_awaited_once()
        self.assertTrue(all(item["stream"] for item in calls))
        self.assertEqual(calls[1]["messages"][-1]["role"], "tool")
        previews = [call.args[0] for call in callback.await_args_list]
        self.assertTrue(any(previews))
        self.assertTrue(all(answer.startswith(text) for text in previews))

    async def test_dsml_stream_returns_confirmation_without_protocol_preview(self):
        ai = self.make_ai()
        service = Mock()
        service.get_enabled_tools.return_value = ["create_payment"]
        service.tool_enabled.return_value = True
        service.get_payment_methods = AsyncMock(return_value={"ok": True, "methods": [{
            "code": "platega_card", "title": "Банковская карта", "min_amount": 50, "max_amount": 9999,
        }]})
        dsml = ('<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="create_payment">'
            '<｜｜DSML｜｜ parameter name="amount" string="false">200</｜｜DSML｜｜ parameter>'
            '<｜｜DSML｜｜ parameter name="method" string="true">platega_card</｜｜DSML｜｜ parameter>'
            '</｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>')
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: sse_response(
            [event({"content": char}) for char in dsml] + [event(finish="stop")],
        )))
        callback = AsyncMock()
        with patch("modules.ai_support.httpx.AsyncClient", return_value=client):
            result = await ai.get_ai_answer("Карта 200 руб", {"user_id": 123}, execute_tools=True,
                user_info_service=service, stream_callback=callback)
        self.assertIsInstance(result, PendingAction)
        self.assertEqual(result.params["method"], "platega_card")
        self.assertTrue(all(not call.args[0] for call in callback.await_args_list))

    async def test_failed_provider_resets_preview_before_failover(self):
        ai = self.make_ai()
        ai._ai_providers = [self.provider, self.provider]
        ai._try_provider_request = AsyncMock(side_effect=[ValueError("broken stream"), "Ответ второго"])
        callback = AsyncMock()
        result = await ai.get_ai_answer("Вопрос", stream_callback=callback)
        self.assertEqual(result, "Ответ второго")
        callback.assert_awaited_once_with("")


class TelegramDraftTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_draft_throttled_and_utf16_bounded(self):
        bot = SimpleNamespace(_post=AsyncMock())
        draft = TelegramDraft(bot, 123, 456)
        with patch("modules.telegram_streaming.time.monotonic", return_value=100):
            await draft.update("Первый текст")
            await draft.update("Следующий текст")
        with patch("modules.telegram_streaming.time.monotonic", return_value=102):
            await draft.update("😀" * 4096)
        self.assertEqual(bot._post.await_count, 2)
        for call in bot._post.await_args_list:
            self.assertEqual(call.args[0], "sendMessageDraft")
            data = call.args[1]
            self.assertEqual(data["draft_id"], draft.draft_id)
            self.assertEqual(data["message_thread_id"], 456)
            self.assertLessEqual(len(data["text"].encode("utf-16-le")), 8192)

    async def test_unsupported_telegram_disables_previews_for_reply(self):
        bot = SimpleNamespace(_post=AsyncMock(side_effect=BadRequest("Method not found")))
        draft = TelegramDraft(bot, 123)
        await draft.update("Первый текст")
        await draft.update("Второй текст")
        bot._post.assert_awaited_once()
        self.assertTrue(draft.disabled)

    async def test_retry_after_does_not_block_or_bypass_cooldown(self):
        bot = SimpleNamespace(_post=AsyncMock(side_effect=[None, RetryAfter(5), None]))
        draft = TelegramDraft(bot, 123)
        with patch("modules.telegram_streaming.time.monotonic", return_value=100):
            await draft.update("Первый текст")
        with patch("modules.telegram_streaming.time.monotonic", return_value=102):
            await draft.update("Второй текст")
            await draft.update("")
            await draft.update("Третий текст")
        self.assertEqual(bot._post.await_count, 2)
        with patch("modules.telegram_streaming.time.monotonic", return_value=108):
            await draft.update("Финальный текст")
        self.assertEqual(bot._post.await_count, 3)
