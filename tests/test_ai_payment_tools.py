import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from modules.ai_support import AISupport
from modules.user_info import PendingAction


METHODS = [
    {
        "code": "platega_sbp",
        "title": "Platega СБП",
        "description": "Пополнения через СБП",
        "currency": "RUB",
        "min_amount": 50,
        "max_amount": 9999,
    },
    {
        "code": "platega_card",
        "title": "Банковская карта",
        "description": "Пополнение баланса через карта МИР",
        "currency": "RUB",
        "min_amount": 50,
        "max_amount": 9999,
    },
    {
        "code": "cryptobot",
        "title": "Крипто Бот",
        "description": "Оплата через криптовалюта",
        "currency": "RUB",
        "min_amount": 50,
        "max_amount": 9999,
    },
]


class FakeUserInfoService:
    def get_enabled_tools(self):
        return ["get_payment_methods", "create_payment"]

    def tool_enabled(self, name):
        return name in {"get_payment_methods", "create_payment"}

    async def get_payment_methods(self):
        return {"ok": True, "currency": "RUB", "methods": METHODS}


class PaymentMethodResolutionTests(unittest.TestCase):
    def test_accepts_display_label_with_mir_qualifier(self):
        method = AISupport._resolve_payment_method("Банковская карта (МИР)", METHODS)
        self.assertEqual(method["code"], "platega_card")

    def test_resolves_short_human_aliases_only_to_available_methods(self):
        self.assertEqual(AISupport._resolve_payment_method("карта", METHODS)["code"], "platega_card")
        self.assertEqual(AISupport._resolve_payment_method("крипта", METHODS)["code"], "cryptobot")
        self.assertEqual(AISupport._resolve_payment_method("СБП", METHODS)["code"], "platega_sbp")

    def test_unknown_or_ambiguous_method_is_not_guessed(self):
        self.assertIsNone(AISupport._resolve_payment_method("наличными", METHODS))
        self.assertIsNone(AISupport._resolve_payment_method("оплата", METHODS))


class TextToolCallTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ai = object.__new__(AISupport)

    def _prepare_provider_flow(self, content):
        self.ai._project_name = "Delta VPN"
        self.ai._system_prompt = ""
        self.ai._build_service_context = AsyncMock(return_value="")
        self.ai._build_user_context = Mock(return_value="")
        self.ai._post_chat_completion = AsyncMock(
            return_value={"choices": [{"message": {"content": content}}]}
        )
        return SimpleNamespace(
            name="DeepSeek Chat v3",
            model_name="deepseek-chat",
            base_url="https://api.deepseek.com",
            api_key="test-key",
            api_keys=None,
            api_type="openai",
        )

    def test_parses_deepseek_dsml_tool_call(self):
        content = """<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="create_payment">
<｜｜DSML｜｜ parameter name="amount" string="false">200</｜｜DSML｜｜ parameter>
<｜｜DSML｜｜ parameter name="method" string="true">platega_card</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>"""
        self.assertEqual(
            self.ai._extract_text_tool_call(content),
            ("create_payment", {"amount": 200, "method": "platega_card"}),
        )
        self.assertTrue(self.ai._contains_tool_protocol(content))

    def test_keeps_existing_functions_json_fallback(self):
        content = 'To=functions.create_payment {"amount": 200, "method": "platega_card"}'
        self.assertEqual(
            self.ai._extract_text_tool_call(content),
            ("create_payment", {"amount": 200, "method": "platega_card"}),
        )

    async def test_payment_label_creates_confirmable_action(self):
        kind, action = await self.ai._execute_tool_call(
            "create_payment",
            {"amount": 200, "method": "Банковская карта (МИР)"},
            123,
            FakeUserInfoService(),
        )
        self.assertEqual(kind, "pending_action")
        self.assertIsInstance(action, PendingAction)
        self.assertEqual(action.params["method"], "platega_card")
        self.assertEqual(action.params["amount"], 200)

    async def test_deepseek_dsml_is_executed_instead_of_returned_to_client(self):
        content = """<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="create_payment">
<｜｜DSML｜｜ parameter name="amount" string="false">200</｜｜DSML｜｜ parameter>
<｜｜DSML｜｜ parameter name="method" string="true">platega_card</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>"""
        provider = self._prepare_provider_flow(content)

        result = await self.ai._try_provider_request(
            provider=provider,
            question="Банковская карта (МИР) 200 руб",
            context={"user_id": 123},
            execute_tools=True,
            user_info_service=FakeUserInfoService(),
        )

        self.assertIsInstance(result, PendingAction)
        self.assertEqual(result.params["method"], "platega_card")
        self.assertNotIn("DSML", result.confirm_text)

    async def test_malformed_tool_protocol_is_never_returned_to_client(self):
        provider = self._prepare_provider_flow(
            '<｜｜DSML｜｜ invoke name="create_payment">broken payload'
        )

        result = await self.ai._try_provider_request(
            provider=provider,
            question="пополнить 200 рублей",
            context={"user_id": 123},
            execute_tools=True,
            user_info_service=FakeUserInfoService(),
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
