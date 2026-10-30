"""Confirmed checkout intents, durable idempotency and fresh payment checks."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from tortoise.transactions import in_transaction

from modules.database import PaymentOperation


def _creation_state(result):
    if result.get("ok"):
        return "succeeded"
    if result.get("http_status") in (409, 502) or result.get("error") in {
        "operation_requires_review", "idempotency_conflict", "checkout_uncertain",
        "request_timeout", "request_failed", "invalid_response",
    }:
        return "uncertain"
    return "failed"


def _order_id(result):
    return str(result.get("order_id") or result.get("transaction_id") or "").strip()


def payment_status_text(result, order_id):
    """Only a fresh payment/check response can confirm a paid invoice."""
    if not result or not result.get("ok"):
        return f"Не удалось проверить текущий статус счёта {order_id}. Оплата пока не подтверждена этой проверкой. Попробуйте проверить ещё раз позже."
    transaction = result.get("transaction") or {}
    status = transaction.get("status")
    normalized = str(status).lower() if status is not None else ""
    if status is True or status == 1 or normalized in {"true", "paid", "completed", "succeeded", "success", "confirmed"}:
        label = "Оплата подтверждена платёжной системой."
    elif status is False or status == 0 or normalized in {"false", "pending", "waiting", "created", "processing"}:
        label = "Оплата пока не подтверждена."
    elif normalized in {"cancelled", "canceled", "expired", "failed"}:
        label = "Счёт отменён, истёк или платёж не завершён."
    else:
        label = "Платёжная система не вернула однозначный статус оплаты."
    return f"Счёт {order_id}: {label} Это результат текущей проверки."


class PaymentFlowMixin:
    @staticmethod
    async def _answer_payment_query(query, *args, **kwargs):
        # Telegram callbacks may have already been acknowledged by the router.
        try:
            await query.answer(*args, **kwargs)
        except Exception:
            pass

    async def _get_payment_context(self, chat_id, user_id):
        operations = await PaymentOperation.filter(chat_id=chat_id, user_id=user_id,
            idempotency_key__isnull=False).order_by("-id").limit(5)
        if not operations:
            return ""
        lines = ["ПОСЛЕДНИЕ СЧЕТА КЛИЕНТА (сохранённые результаты создания, НЕ актуальный статус оплаты):"]
        for operation in operations:
            result = operation.result or {}
            lines.append(f"Операция {operation.id}: {operation.amount} {operation.currency}, метод {operation.method}; создание: {operation.state}.")
            order = _order_id(result)
            if order:
                lines.append(f"Для payment/check используй transaction_id={order}.")
            if result.get("pay_link"):
                lines.append(f"Ссылка счёта: {result['pay_link']}")
        lines.append("Для актуального статуса вызови check_payment. Новый счёт не отменяет старые. Повтор использует исходную операцию; новый счёт создаётся только по отдельному подтверждению клиента.")
        return "\n".join(lines)

    async def _prepare_payment_confirmation(self, update, chat, action):
        params = action.params
        if params.get("request_mode") == "retry":
            query = PaymentOperation.filter(chat_id=chat.id, user_id=chat.user_id, idempotency_key__isnull=False)
            if params.get("operation_id"):
                query = query.filter(id=params["operation_id"])
            else:
                if params.get("method"):
                    query = query.filter(method=params["method"])
                if params.get("amount") is not None:
                    query = query.filter(amount=params["amount"])
            matches = await query.order_by("-id").limit(10)
            if not matches:
                await update.message.reply_text("Не найден прежний запрос с такой суммой и способом оплаты. Для нового счёта отдельно попросите создать новый счёт и подтвердите его.")
                return
            if len(matches) > 1:
                await update.message.reply_text("Найдено несколько счетов. Выберите, какой прежний запрос повторить:", reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"№{row.id}: {row.amount} {row.currency} · {_order_id(row.result or {}) or row.state}"[:64], callback_data=f"payment_retry_{row.id}")]
                    for row in matches
                ]))
                return
            operation = matches[0]
            if (params.get("amount") is not None and params["amount"] != operation.amount) or (params.get("method") and params["method"] != operation.method):
                await update.message.reply_text("Параметры повтора отличаются от исходного счёта. Повтор должен использовать ту же сумму и способ оплаты; для другого счёта нужен отдельный запрос.")
                return
            text = f"Повторить прежний запрос на {operation.amount} {operation.currency} через {operation.method_title}? Будут использованы прежний ключ и те же параметры."
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Повторить прежний запрос", callback_data=f"payment_retry_{operation.id}")]])
        else:
            # A proposal is not a payment request. UUID allocation happens only on
            # the explicit 'new invoice' confirmation, never on an error/failover.
            operation = await PaymentOperation.create(
                chat_id=chat.id, user_id=chat.user_id, method=params["method"],
                method_title=params.get("method_title") or params["method"],
                amount=params["amount"], currency=params.get("currency") or "RUB",
            )
            text = (f"Создать новый счёт на {operation.amount} {operation.currency} через {operation.method_title}?\n"
                    "Ранее созданные счета останутся действующими. Если оплатить несколько счетов, каждый подтверждённый платёж будет учтён отдельно.")
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Создать новый счёт", callback_data=f"payment_confirm_{operation.id}"),
                InlineKeyboardButton("Отмена", callback_data=f"payment_cancel_{operation.id}"),
            ]])
        await update.message.reply_text(text, reply_markup=keyboard)
        await self._record_ai_tool_event(chat, {"phase": "confirmation_sent", "tool": "create_payment",
            "params": {"operation_id": operation.id, "method": operation.method, "amount": operation.amount},
            "result": {"message": text}})
        await self._save_message_to_db(chat.id, chat.user_id, {"kind": "text", "text": text}, "ai")

    @staticmethod
    def _payment_markup(operation):
        result = operation.result or {}
        rows = []
        link = str(result.get("pay_link") or "").strip()
        if result.get("ok") and link.startswith(("https://", "http://")):
            rows.append([InlineKeyboardButton("Перейти к оплате", url=link)])
        if _order_id(result):
            rows.append([InlineKeyboardButton("Проверить оплату", callback_data=f"payment_check_{operation.id}")])
        rows.append([InlineKeyboardButton("Повторить прежний запрос", callback_data=f"payment_retry_{operation.id}")])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def _payment_result_text(operation):
        result = operation.result or {}
        if result.get("ok"):
            order_id = _order_id(result)
            text = f"Счёт на {operation.amount} {operation.currency}."
            if order_id:
                text += f"\nНомер: {order_id}"
            link = str(result.get("pay_link") or "").strip()
            text += f"\n\nСсылка для оплаты:\n{link}" if link else "\nСервис не вернул ссылку для оплаты. Повторите прежний запрос или обратитесь в поддержку."
            return text + "\n\nСоздание счёта и переход по ссылке не подтверждают оплату. Нажмите «Проверить оплату» для актуального статуса."
        error = result.get("error") or "request_failed"
        labels = {
            "operation_requires_review": "Этот запрос ещё выполняется или требует проверки. Можно повторить прежний запрос с тем же ключом.",
            "idempotency_conflict": "Ключ этого запроса уже использован с другими параметрами или другим Support-токеном. Нужна проверка поддержки.",
            "checkout_uncertain": "Платёжный сервис не смог однозначно подтвердить создание счёта. Счёт мог быть создан.",
            "request_timeout": "Время ожидания ответа истекло. Счёт мог быть создан.",
            "request_failed": "Не удалось получить ответ сервиса. Счёт мог быть создан.",
            "invalid_response": "Сервис вернул некорректный ответ. Результат создания счёта неизвестен.",
            "invalid_token": "Платёжная интеграция временно недоступна: токен Support API отклонён.",
            "user_not_found": "Пользователь не найден в платёжной системе.",
            "user_blocked": "Пополнение недоступно: пользователь заблокирован.",
            "captcha_required": "Сначала пройдите капчу в основном боте.",
            "unsupported_method": "Выбранный способ оплаты не поддерживается.",
            "method_unavailable": "Выбранный способ оплаты сейчас недоступен.",
            "invalid_amount": "Сумма пополнения не соответствует ограничениям платёжного сервиса.",
            "previous_operation_requires_review": "Сервис вернул устаревшее ограничение предыдущей операции. Обратитесь в поддержку для проверки версии API.",
        }
        text = labels.get(error, "Не удалось создать ссылку на оплату. Повторите прежний запрос или обратитесь в поддержку.")
        if operation.state == "uncertain":
            text += "\nНовый счёт автоматически не создавался. Для отдельного нового счёта явно попросите его создать и подтвердите действие."
        return text + f"\nКлюч операции: {operation.idempotency_key}"

    async def _handle_payment_callback(self, query, user_id, action, operation_id):
        async with self._client_chat_lock(user_id):
            await self._run_payment_callback(query, user_id, action, operation_id)

    async def _run_payment_callback(self, query, user_id, action, operation_id):
        operation = await PaymentOperation.get_or_none(id=operation_id, user_id=user_id)
        if not operation:
            await self._answer_payment_query(query, "Операция не найдена или принадлежит другому пользователю", show_alert=True)
            return
        chat = await self.db.get_chat_by_id(operation.chat_id)
        if not chat or chat.user_id != user_id:
            await self._answer_payment_query(query, "Чат операции недоступен", show_alert=True)
            return
        if action == "cancel":
            cancelled = await PaymentOperation.filter(id=operation.id, idempotency_key=None, state="proposed").update(state="cancelled")
            await self._answer_payment_query(query, )
            if cancelled:
                await query.edit_message_text("Создание нового счёта отменено. Ранее созданные счета не изменены.")
            return
        if action == "check":
            await self._answer_payment_query(query, "Проверяю текущий статус…")
            order_id = _order_id(operation.result or {})
            if not order_id:
                await query.message.reply_text("Сервис пока не вернул номер счёта. Повторите прежний запрос или обратитесь в поддержку.")
                return
            try:
                result = await self.user_info.check_payment(order_id, user_id)
            except Exception:
                result = {"ok": False, "error": "request_failed"}
            text = payment_status_text(result, order_id)
            await query.message.reply_text(text)
            await self._record_ai_tool_event(chat, {"phase": "result", "tool": "check_payment", "result": result})
            await self._save_message_to_db(chat.id, user_id, {"kind": "text", "text": text}, "ai")
            return
        if action not in {"confirm", "retry"}:
            return
        await self._answer_payment_query(query, "Обрабатываю запрос…")
        # Persist/claim before the HTTP request. Concurrent callbacks cannot create
        # two UUIDs. A crashed request can later be retried with its persisted key.
        now = datetime.now(timezone.utc)
        async with in_transaction() as connection:
            operation = await PaymentOperation.filter(id=operation.id, user_id=user_id).using_db(connection).select_for_update().first()
            if operation is None:
                return
            if operation.state == "cancelled":
                await query.message.reply_text("Это подтверждение отменено. Для нового счёта отправьте отдельный запрос.")
                return
            if operation.idempotency_key is None:
                if action != "confirm" or now - operation.created_at > timedelta(minutes=5):
                    await query.message.reply_text("Подтверждение устарело. Отдельно запросите новый счёт.")
                    return
                operation.idempotency_key = str(uuid4())
            if operation.state == "running" and operation.started_at and now - operation.started_at < timedelta(seconds=90):
                await query.message.reply_text("Этот запрос уже выполняется. Повторите проверку немного позже.")
                return
            operation.state = "running"
            operation.started_at = now
            await operation.save(using_db=connection, update_fields=["idempotency_key", "state", "started_at", "updated_at"])
        params = {"method": operation.method, "amount": operation.amount, "currency": operation.currency, "idempotency_key": operation.idempotency_key}
        await self._record_ai_tool_event(chat, {"phase": "confirmed", "tool": "create_payment", "params": params})
        try:
            result = await self.user_info.create_payment_link(
                owner_telegram_id=user_id, method=operation.method, amount=operation.amount,
                idempotency_key=operation.idempotency_key,
            )
        except Exception:
            result = {"ok": False, "error": "request_failed"}
        if not isinstance(result, dict):
            result = {"ok": False, "error": "request_failed"}
        # A failed replay cannot erase a previously confirmed checkout link.
        replay_failed = bool((operation.result or {}).get("ok") and not result.get("ok"))
        if replay_failed:
            operation.state = "succeeded"
        else:
            operation.state = _creation_state(result)
            operation.result = result
        await operation.save(update_fields=["state", "result", "updated_at"])
        text = self._payment_result_text(operation)
        if replay_failed:
            text = "Не удалось получить ответ на повторный запрос. Ниже сохранённый ранее счёт; актуальную оплату проверяйте отдельно.\n\n" + text
        markup = self._payment_markup(operation)
        delivered = False
        try:
            await query.edit_message_text(text, reply_markup=markup)
            delivered = True
        except Exception:
            try:
                await query.message.reply_text(text, reply_markup=markup)
                delivered = True
            except Exception:
                pass
        await self._record_ai_tool_event(chat, {"phase": "action_result", "tool": "create_payment",
            "result": {"backend": result, "client_message": text}})
        await self._record_ai_tool_event(chat, {"phase": "client_message" if delivered else "delivery_failed", "tool": "create_payment", "message": text})
        await self._save_message_to_db(chat.id, user_id, {"kind": "text", "text": text}, "ai")
