import asyncio
import json
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from courses.models import Course, KnowledgeEntry

from . import llm, telegram_bot
from .knowledge import build_knowledge_text, recent_history
from .models import ChatMessage, Question

USER_CHAT = 111
ADMIN_CHAT = -999


def fake_update(text, chat_id=USER_CHAT, reply_to_message_id=None):
    message = mock.MagicMock()
    message.text = text
    message.reply_text = mock.AsyncMock()
    message.reply_to_message = SimpleNamespace(message_id=reply_to_message_id) if reply_to_message_id else None
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(id=42, username="sara", full_name="Sara A"),
    )


def fake_context():
    bot = mock.MagicMock()
    bot.send_chat_action = mock.AsyncMock()
    bot.send_message = mock.AsyncMock(return_value=SimpleNamespace(message_id=555))
    return SimpleNamespace(bot=bot)


def replies(update):
    return " ".join(call.args[0] for call in update.message.reply_text.call_args_list)


class KnowledgeTests(TestCase):
    def test_knowledge_text_and_history(self):
        Course.objects.create(title="Tax course", price_rial=650_000_000, registration_open=True)
        Course.objects.create(title="Hidden", is_active=False)
        KnowledgeEntry.objects.create(title="Refunds", content="Full refund one week before start.")
        text = build_knowledge_text()
        self.assertIn("Tax course", text)
        self.assertIn("65,000,000 تومان", text)
        self.assertIn("Full refund", text)
        self.assertNotIn("Hidden", text)

        for role, body in [("assistant", "stale"), ("user", "a"), ("user", "b"), ("assistant", "c"), ("user", "d")]:
            ChatMessage.objects.create(chat_id=1, role=role, text=body)
        history = recent_history(1, 10)
        self.assertEqual([m["role"] for m in history], ["user", "assistant", "user"])
        self.assertEqual(history[0]["content"], "a\n\nb")


def claude_response(payload, stop_reason="end_turn"):
    usage = SimpleNamespace(input_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=1)
    content = [SimpleNamespace(type="text", text=json.dumps(payload))]
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=usage)


@override_settings(LLM_PROVIDER="anthropic")
class AnthropicLLMTests(TestCase):
    def run_with(self, response):
        client = mock.MagicMock()
        client.beta.messages.create = mock.AsyncMock(return_value=response)
        with mock.patch.object(llm, "get_anthropic_client", return_value=client):
            result = asyncio.run(llm.answer_question("kb", [{"role": "user", "content": "hi"}]))
        return result, client.beta.messages.create.call_args.kwargs

    def test_answer_and_request_shape(self):
        result, kwargs = self.run_with(claude_response({"can_answer": True, "answer": "Hello", "reason": ""}))
        self.assertTrue(result.can_answer)
        self.assertEqual(result.answer, "Hello")
        self.assertEqual(kwargs["model"], "claude-opus-5-5")
        self.assertEqual(kwargs["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(kwargs["system"][-1]["cache_control"], {"type": "ephemeral"})

    def test_refusal_and_garbage_are_handed_off(self):
        result, _ = self.run_with(claude_response({}, stop_reason="refusal"))
        self.assertFalse(result.can_answer)
        bad = claude_response({})
        bad.content[0].text = "not json"
        result, _ = self.run_with(bad)
        self.assertFalse(result.can_answer)
        self.assertTrue(result.answer)


def openai_response(content, finish_reason="stop"):
    message = SimpleNamespace(content=content, refusal=None)
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, prompt_tokens_details=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)], usage=usage)


@override_settings(LLM_PROVIDER="openai", OPENAI_MODEL="my-model")
class OpenAILLMTests(TestCase):
    def run_with(self, create):
        client = mock.MagicMock()
        client.chat.completions.create = create
        with mock.patch.object(llm, "get_openai_client", return_value=client):
            return asyncio.run(llm.answer_question("KB-TEXT", [{"role": "user", "content": "hi"}]))

    def test_answer_and_request_shape(self):
        payload = json.dumps({"can_answer": True, "answer": "سلام", "reason": ""})
        create = mock.AsyncMock(return_value=openai_response(payload))
        result = self.run_with(create)
        self.assertEqual(result, llm.LLMResult(True, "سلام", ""))
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["model"], "my-model")
        self.assertEqual(kwargs["response_format"]["type"], "json_schema")
        self.assertEqual(kwargs["messages"][0]["role"], "system")
        self.assertIn("KB-TEXT", kwargs["messages"][0]["content"])
        self.assertEqual(kwargs["messages"][-1], {"role": "user", "content": "hi"})

    def test_falls_back_to_json_object_and_strips_code_fence(self):
        import httpx
        import openai

        request = httpx.Request("POST", "https://example.com/v1/chat/completions")
        rejected = openai.BadRequestError(
            "json_schema not supported", response=httpx.Response(400, request=request), body=None
        )
        fenced = '```json\n{"can_answer": false, "answer": "", "reason": "unknown"}\n```'
        create = mock.AsyncMock(side_effect=[rejected, openai_response(fenced)])
        result = self.run_with(create)
        self.assertEqual(create.call_args_list[1].kwargs["response_format"], {"type": "json_object"})
        self.assertFalse(result.can_answer)
        self.assertEqual(result.answer, llm.FALLBACK_HANDOFF)
        self.assertEqual(result.reason, "unknown")

    def test_truncated_answer_is_handed_off(self):
        create = mock.AsyncMock(return_value=openai_response('{"can_answer": tr', finish_reason="length"))
        self.assertFalse(self.run_with(create).can_answer)


@override_settings(TELEGRAM_ADMIN_CHAT_ID=str(ADMIN_CHAT), LLM_HISTORY_TURNS=6)
class BotFlowTests(TestCase):
    def ask(self, text, result):
        update, context = fake_update(text), fake_context()
        with mock.patch.object(llm, "answer_question", mock.AsyncMock(return_value=result)):
            asyncio.run(telegram_bot.handle_user_message(update, context))
        return update, context

    def test_answerable_question_is_answered(self):
        update, context = self.ask("price?", llm.LLMResult(True, "It costs 3M Toman."))
        self.assertIn("3M Toman", replies(update))
        context.bot.send_message.assert_not_called()
        self.assertEqual(Question.objects.count(), 0)
        self.assertEqual(ChatMessage.objects.filter(chat_id=USER_CHAT).count(), 2)

    def test_unanswerable_question_goes_to_admin_and_back(self):
        update, context = self.ask("Can I pay in 5 installments?", llm.LLMResult(False, "Forwarded!", "no policy"))
        self.assertIn("Forwarded!", replies(update))
        question = Question.objects.get()
        self.assertEqual(question.status, Question.Status.PENDING)
        self.assertEqual(question.admin_message_id, 555)
        target, notice = context.bot.send_message.call_args.args
        self.assertEqual(target, ADMIN_CHAT)
        self.assertIn("5 installments", notice)

        # Admin replies in the admin chat with +faq.
        admin_update = fake_update("+faq Yes, up to 3 installments.", chat_id=ADMIN_CHAT, reply_to_message_id=555)
        admin_context = fake_context()
        asyncio.run(telegram_bot.handle_admin_reply(admin_update, admin_context))

        target, text = admin_context.bot.send_message.call_args.args
        self.assertEqual(target, USER_CHAT)
        self.assertIn("up to 3 installments", text)
        question.refresh_from_db()
        self.assertEqual(question.status, Question.Status.ANSWERED)
        self.assertEqual(question.answer, "Yes, up to 3 installments.")
        self.assertTrue(KnowledgeEntry.objects.filter(source=KnowledgeEntry.Source.FAQ).exists())
        self.assertIn("پاسخ برای", replies(admin_update))

    def test_reply_to_unrelated_admin_message_is_ignored(self):
        admin_update = fake_update("chatting", chat_id=ADMIN_CHAT, reply_to_message_id=1)
        admin_context = fake_context()
        asyncio.run(telegram_bot.handle_admin_reply(admin_update, admin_context))
        admin_context.bot.send_message.assert_not_called()


class QuestionAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "a@example.com", "pw")
        self.client.force_login(self.admin)
        self.question = Question.objects.create(chat_id=USER_CHAT, question="Certificate?")
        self.url = reverse("admin:bot_question_change", args=[self.question.pk])

    def post_answer(self, answer):
        return self.client.post(self.url, {"status": "pending", "answer": answer}, follow=True)

    def test_answer_from_admin_panel_is_sent(self):
        with mock.patch("bot.admin.send_telegram_message") as send:
            self.post_answer("Certificates are issued after the final exam.")
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], USER_CHAT)
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, Question.Status.ANSWERED)
        self.assertEqual(self.question.answered_by, "admin")

    def test_failed_delivery_keeps_question_pending(self):
        from .services import TelegramError

        with mock.patch("bot.admin.send_telegram_message", side_effect=TelegramError("blocked")):
            response = self.post_answer("Answer")
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, Question.Status.PENDING)
        self.assertEqual(self.question.answer, "Answer")
        self.assertContains(response, "ارسال نشد")


class CourseAdminTests(TestCase):
    def test_editing_crawled_field_locks_course(self):
        User.objects.create_superuser("admin", "a@example.com", "pw")
        self.client.login(username="admin", password="pw")
        course = Course.objects.create(source_id=7, title="Old title", price_rial=10)
        url = reverse("admin:courses_course_change", args=[course.pk])
        form = {
            "title": "New title", "course_type": "online", "categories": "[]", "price_rial": "10",
            "is_active": "on", "registration_open": "on",
        }
        self.client.post(url, form)
        course.refresh_from_db()
        self.assertEqual(course.title, "New title")
        self.assertTrue(course.locked)


class PlainTextReplyTests(TestCase):
    def test_markdown_links_bold_and_headings_are_flattened(self):
        text = "## عنوان\n**قیمت** ۳ میلیون\n[ثبت‌نام](https://x.test/c?id=1) و [https://x.test/a](https://x.test/a) و `کد`"
        cleaned = llm.strip_markdown(text)
        self.assertEqual(
            cleaned, "عنوان\nقیمت ۳ میلیون\nثبت‌نام: https://x.test/c?id=1 و https://x.test/a و کد"
        )

    def test_parse_result_applies_it(self):
        payload = json.dumps({"can_answer": True, "answer": "[لینک](https://x.test)", "reason": ""})
        self.assertEqual(llm.parse_result(payload).answer, "لینک: https://x.test")


class CatalogCountTests(TestCase):
    def test_header_states_total_and_open_counts(self):
        from .knowledge import build_catalog_text

        Course.objects.create(title="A", registration_open=True)
        Course.objects.create(title="B", registration_open=False)
        Course.objects.create(title="C", registration_open=True)
        self.assertIn("مجموع 3 دوره، 2 دوره در حال ثبت‌نام", build_catalog_text())
