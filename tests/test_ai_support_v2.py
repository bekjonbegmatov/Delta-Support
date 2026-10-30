import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

from modules.ai_policy import SupportAnswer, explicitly_requests_manager, should_offer_manager
from modules.ai_support import AISupport
from modules.user_info import PendingAction


def completed(*output):
    return httpx.Response(200, json={"status": "completed", "output": list(output)})


def function(name, arguments, call_id):
    return {"type": "function_call", "name": name, "arguments": json.dumps(arguments), "call_id": call_id, "id": "fc_" + call_id}


def reply(text):
    return {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": text, "annotations": []}]}


class EscalationPolicyTests(unittest.TestCase):
    def test_ordinary_setup_and_manager_mentions_do_not_offer_button(self):
        for question in ("Как подключить VPN?", "Какой менеджер паролей нужен?", "Не нужно приглашать менеджера", "Что значит менеджер?"):
            self.assertFalse(should_offer_manager("Можно подключить приложение, менеджер пока не нужен.", question), question)

    def test_explicit_request_and_structured_signal_offer_button(self):
        for question in ("Позовите менеджера", "Хочу поговорить с оператором", "Можно живого человека?", "Connect me to a human", "Не хочу бота, хочу оператора", "Менеджера!"):
            self.assertTrue(explicitly_requests_manager(question), question)
        self.assertTrue(should_offer_manager(SupportAnswer("Нужна ручная проверка.", offer_manager=True)))
        self.assertFalse(should_offer_manager(SupportAnswer("Уточните устройство.")))


class ResponsesSupportFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ai = object.__new__(AISupport)
        self.ai._project_name = "Delta"
        self.ai._system_prompt = ""
        self.ai._build_service_context = AsyncMock(return_value="Инструкции сервиса")
        self.ai._build_user_context = Mock(return_value="")
        self.provider = SimpleNamespace(name="OpenAI v2", model_name="gpt-5-mini", base_url="https://test.invalid/v1/responses", api_type="openai_v2", api_key="test", api_keys=[])
        self.service = Mock()
        self.service.get_enabled_tools.return_value = ["get_subscription_info", "get_subscription_devices", "reset_devices"]
        self.service.tool_enabled.return_value = True
        self.service.find_own_subscription = AsyncMock(return_value={"username": "own"})
        self.service.get_subscription_info = AsyncMock(return_value={"ok": True})
        self.service.get_subscription_devices = AsyncMock(return_value={"ok": True, "devices": []})

    async def request(self, handler, **kwargs):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("modules.ai_support.httpx.AsyncClient", return_value=client):
            return await self.ai._try_provider_request(self.provider, kwargs.pop("question", "Не подключается"),
                context={"user_id": 123, "chat_id": 7}, execute_tools=True, user_info_service=self.service, **kwargs)

    async def test_image_multiple_tool_rounds_and_reasoning_preserved(self):
        requests = []
        reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "encrypted"}
        def handle(request):
            self.assertEqual(request.url.path, "/v1/responses")
            data = json.loads(request.content)
            requests.append(data)
            self.assertFalse(data["store"])
            self.assertNotIn("max_tokens", data)
            self.assertGreaterEqual(data["max_output_tokens"], 4096)
            if len(requests) == 1:
                self.assertEqual(data["input"][-1]["content"][1]["type"], "input_image")
                self.assertTrue(data["tools"])
                return completed(reasoning, function("get_subscription_info", {"subscription_username": "own"}, "one"))
            if len(requests) == 2:
                self.assertIn(reasoning, data["input"])
                self.assertEqual(data["input"][-1]["call_id"], "one")
                self.assertTrue(data["tools"])
                return completed(function("get_subscription_devices", {"subscription_username": "own"}, "two"))
            self.assertEqual(data["input"][-1]["call_id"], "two")
            return completed(reply("На скриншоте ошибка подключения. Проверьте выбранную сеть."))
        result = await self.request(handle, image_data_url="data:image/png;base64,AAAA")
        self.assertIn("скриншоте", result)
        self.assertEqual(len(requests), 3)
        self.assertEqual(self.service.find_own_subscription.await_count, 2)

    async def test_destructive_tool_only_returns_confirmation(self):
        result = await self.request(lambda request: completed(function("reset_devices", {"subscription_username": "own"}, "one")))
        self.assertIsInstance(result, PendingAction)
        self.assertEqual(result.action, "reset_devices")
        self.service.reset_devices.assert_not_called()

    async def test_unknown_tool_is_not_executed_and_bad_json_can_be_corrected(self):
        requests = []
        def handle(request):
            data = json.loads(request.content)
            requests.append(data)
            if len(requests) == 1:
                bad_call = function("get_subscription_info", {}, "bad")
                bad_call["arguments"] = "{broken"
                return completed(bad_call, function("delete_all_users", {}, "unknown"))
            outputs = [item for item in data["input"] if item.get("type") == "function_call_output"]
            self.assertEqual(json.loads(outputs[0]["output"])["error"], "invalid_arguments")
            self.assertEqual(json.loads(outputs[1]["output"])["error"], "tool_not_available")
            return completed(reply("Уточните название подписки."))
        await self.request(handle)
        self.service.find_own_subscription.assert_not_awaited()

    async def test_local_gap_tool_works_without_account_integration_and_deduplicates(self):
        self.service = None
        requests = []
        arguments = {"topic": "Настройка телевизора", "reason": "Нет инструкции для этой системы", "example_question": "Как настроить VPN на телевизоре?"}
        def handle(request):
            requests.append(json.loads(request.content))
            if len(requests) < 3:
                return completed(function("report_knowledge_gap", arguments, str(len(requests))))
            return completed(reply("Уточните модель телевизора и его операционную систему."))
        with patch("modules.ai_support.record_knowledge_gap", new_callable=AsyncMock, return_value={"ok": True}) as record:
            result = await self.request(handle)
        record.assert_awaited_once_with(**arguments, chat_id=7)
        self.assertFalse(result.offer_manager)

    async def test_diagnostics_escalation_requires_previous_attempts(self):
        for history, expected in (([], False), ([{"role": "assistant", "content": "Шаг 1"}, {"role": "assistant", "content": "Шаг 2"}], True)):
            count = 0
            def handle(request):
                nonlocal count
                count += 1
                if count == 1:
                    return completed(function("offer_manager", {"reason_code": "diagnostics_exhausted", "reason": "После проверки двух сетей и обновления приложения сбой сохраняется"}, "one"))
                return completed(reply("Продолжим проверку."))
            result = await self.request(handle, chat_history=history)
            self.assertEqual(result.offer_manager, expected)

    async def test_loop_is_bounded_and_identical_tool_is_not_reexecuted(self):
        requests = []
        def handle(request):
            data = json.loads(request.content)
            requests.append(data)
            if not data.get("tools"):
                return completed(reply("Проверку завершить не удалось. Уточните ошибку."))
            return completed(function("get_subscription_info", {"subscription_username": "own"}, str(len(requests))))
        result = await self.request(handle)
        self.assertEqual(len(requests), 7)
        self.service.find_own_subscription.assert_awaited_once()
        self.assertIn("Уточните", result)

    async def test_custom_prompt_and_vision_preserve_safety_and_account_context(self):
        self.ai._system_prompt = "Кастомный стиль"
        def handle(request):
            prompt = json.loads(request.content)["instructions"]
            self.assertIn("Кастомный стиль", prompt)
            self.assertIn("Инструкции сервиса", prompt)
            self.assertIn("ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА", prompt)
            return completed(reply("Уточните текст ошибки."))
        await self.request(handle, image_data_url="data:image/png;base64,AAAA")
