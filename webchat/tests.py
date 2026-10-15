import json
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from bot import llm
from bot.models import ChatMessage

CHAT_URL = "/api/chat/"


@override_settings(LLM_PROVIDER="openai", OPENAI_API_KEY="test-key", RAG_ENABLED=False)
class WebChatStorageTests(TestCase):
    def post(self, payload):
        return self.client.post(CHAT_URL, json.dumps(payload), content_type="application/json")

    def ask(self, conversation_id, text, result):
        payload = {"conversation_id": conversation_id, "messages": [{"role": "user", "content": text}]}
        with mock.patch.object(llm, "answer_question", mock.AsyncMock(return_value=result)):
            return self.post(payload)

    def test_exchange_is_stored_for_the_admin(self):
        response = self.ask(123456789, "قیمت دوره چقدر است؟", llm.LLMResult(True, "۳ میلیون تومان"))
        self.assertEqual(response.status_code, 200)
        rows = list(ChatMessage.objects.filter(channel=ChatMessage.Channel.WEB, chat_id=123456789).order_by("created_at", "id"))
        self.assertEqual([r.role for r in rows], ["user", "assistant"])
        self.assertEqual(rows[0].text, "قیمت دوره چقدر است؟")
        self.assertFalse(rows[1].escalated)

    def test_escalation_is_flagged(self):
        self.ask(42, "قسطی می‌شود؟", llm.LLMResult(False, "ارجاع شد", "no policy"))
        self.assertTrue(ChatMessage.objects.get(chat_id=42, role="assistant").escalated)

    def test_missing_or_bad_conversation_id_is_rejected(self):
        for bad in (None, "abc", -5, 0, True, 2**60):
            payload = {"conversation_id": bad, "messages": [{"role": "user", "content": "hi"}]}
            self.assertEqual(self.post(payload).status_code, 400, bad)
        self.assertEqual(ChatMessage.objects.count(), 0)

    def test_storage_failure_does_not_break_the_chat(self):
        from pymongo.errors import PyMongoError

        with mock.patch("webchat.views.ChatMessage.objects.create", side_effect=PyMongoError("down")):
            response = self.ask(7, "سلام", llm.LLMResult(True, "سلام!"))
        self.assertEqual(response.json()["answer"], "سلام!")

    def test_web_and_telegram_histories_do_not_mix(self):
        from bot.knowledge import recent_history

        ChatMessage.objects.create(channel="web", chat_id=5, role="user", text="from web")
        ChatMessage.objects.create(channel="telegram", chat_id=5, role="user", text="from telegram")
        self.assertEqual([m["content"] for m in recent_history(5, 10)], ["from telegram"])


class ConversationAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "a@example.com", "pw")
        self.client.force_login(self.admin)
        ChatMessage.objects.create(channel="web", chat_id=99, role="user", text="سلام، قیمت؟")
        ChatMessage.objects.create(channel="web", chat_id=99, role="assistant", text="کدام دوره؟", escalated=True)
        ChatMessage.objects.create(channel="telegram", chat_id=99, role="user", text="telegram only")

    def test_list_links_to_the_conversation(self):
        response = self.client.get(reverse("admin:bot_chatmessage_changelist"))
        self.assertContains(response, "مشاهده گفتگو")
        self.assertContains(response, reverse("admin:bot_chatmessage_conversation", args=["web", 99]))

    def test_thread_shows_only_that_channels_messages(self):
        response = self.client.get(reverse("admin:bot_chatmessage_conversation", args=["web", 99]))
        self.assertContains(response, "سلام، قیمت؟")
        self.assertContains(response, "کدام دوره؟")
        self.assertContains(response, "ارجاع")
        self.assertNotContains(response, "telegram only")

    def test_unknown_conversation_and_negative_ids(self):
        self.assertEqual(self.client.get(reverse("admin:bot_chatmessage_conversation", args=["web", 1])).status_code, 404)
        ChatMessage.objects.create(channel="telegram", chat_id=-100, role="user", text="group")
        self.assertEqual(self.client.get(reverse("admin:bot_chatmessage_conversation", args=["telegram", -100])).status_code, 200)
