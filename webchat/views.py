import json
import logging
import os

from asgiref.sync import async_to_sync
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST
from pymongo.errors import PyMongoError

from bot import llm
from bot.knowledge import build_llm_context
from bot.models import ChatMessage
from courses.models import Course

from .demo_knowledge import DEMO_KNOWLEDGE

logger = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 2000
MAX_CONVERSATION_ID = 2**53  # ids are random numbers made by the browser (safe JS integers)


@ensure_csrf_cookie
def index(request):
    return render(request, "webchat/index.html", {"max_chars": MAX_QUESTION_CHARS})


def load_knowledge(messages):
    """(knowledge text, retrieved passages, is_demo): from MongoDB, or the bundled demo data if it is down or empty."""
    try:
        if Course.objects.filter(is_active=True).exists():
            knowledge, retrieved = build_llm_context(messages)
            return knowledge, retrieved, False
    except PyMongoError as exc:
        logger.warning("MongoDB unavailable, using demo knowledge: %s", exc)
    return DEMO_KNOWLEDGE, None, True


def clean_messages(raw):
    """Keep the last N valid turns, alternating roles, starting with and ending on a user turn."""
    messages = []
    for item in raw if isinstance(raw, list) else []:
        role, content = item.get("role"), str(item.get("content", "")).strip()
        if role not in ("user", "assistant") or not content:
            continue
        content = content[:MAX_QUESTION_CHARS]
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n\n" + content
        else:
            messages.append({"role": role, "content": content})
    messages = messages[-settings.LLM_HISTORY_TURNS - 1:]
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages if messages and messages[-1]["role"] == "user" else []


def parse_conversation_id(value):
    """The browser's random conversation number, or None if it is missing or not a sane integer."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < MAX_CONVERSATION_ID:
        return None
    return value


def store_exchange(conversation_id, question, result):
    """Keep the visitor's question and the reply so admins can read the conversation. Never breaks the chat."""
    try:
        ChatMessage.objects.create(
            channel=ChatMessage.Channel.WEB, chat_id=conversation_id, role=ChatMessage.Role.USER, text=question
        )
        ChatMessage.objects.create(
            channel=ChatMessage.Channel.WEB, chat_id=conversation_id, role=ChatMessage.Role.ASSISTANT,
            text=result.answer, escalated=not result.can_answer,
        )
    except PyMongoError:
        logger.exception("Could not store web chat messages")


@require_POST
def chat(request):
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "درخواست نامعتبر است."}, status=400)
    messages = clean_messages(payload.get("messages"))
    conversation_id = parse_conversation_id(payload.get("conversation_id"))
    if not messages:
        return JsonResponse({"error": "لطفاً یک پیام بنویسید."}, status=400)
    if conversation_id is None:
        return JsonResponse({"error": "لطفاً صفحه را دوباره بارگذاری کنید."}, status=400)

    if settings.LLM_PROVIDER == "openai" and not (settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")):
        return JsonResponse(
            {"error": "کلید OPENAI_API_KEY تنظیم نشده است. آن را (و OPENAI_BASE_URL) در فایل .env بنویسید و سرور را دوباره اجرا کنید."},
            status=503,
        )

    knowledge, retrieved, is_demo = load_knowledge(messages)
    # Django runs this view in a fresh event loop per request, so a cached async client from an
    # earlier request would be bound to a closed loop. Start from a clean client each time.
    llm._openai_client = None
    llm._anthropic_client = None
    result = async_to_sync(llm.answer_question)(knowledge, messages, retrieved)
    store_exchange(conversation_id, messages[-1]["content"], result)
    return JsonResponse({"answer": result.answer, "escalated": not result.can_answer, "demo_data": is_demo})
