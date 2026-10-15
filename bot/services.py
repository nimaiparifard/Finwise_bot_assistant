"""Synchronous helpers shared by the Django admin and the Telegram bot."""

import requests
from django.conf import settings
from django.utils import timezone

from courses.models import KnowledgeEntry

from .models import ChatMessage, Question

TELEGRAM_LIMIT = 4096
ANSWER_HEADER = "📩 پاسخ کارشناس فین‌وایز به سؤال شما:"


class TelegramError(Exception):
    pass


def split_message(text, limit=TELEGRAM_LIMIT):
    """Split long text on line breaks so each chunk fits in one Telegram message."""
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        current += line
    if current.strip():
        chunks.append(current)
    return chunks or [text]


def send_telegram_message(chat_id, text):
    """Send a plain-text message via the Bot HTTP API (used outside the bot's event loop, e.g. the admin)."""
    if not settings.TELEGRAM_BOT_TOKEN:
        raise TelegramError("توکن ربات تلگرام (TELEGRAM_BOT_TOKEN) تنظیم نشده است.")
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    for chunk in split_message(text):
        try:
            response = requests.post(url, json={"chat_id": chat_id, "text": chunk}, timeout=15)
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise TelegramError(f"ارتباط با تلگرام برقرار نشد: {exc}") from exc
        if not data.get("ok"):
            raise TelegramError(data.get("description", "خطای نامشخص تلگرام"))


def format_answer_for_user(question):
    return f"{ANSWER_HEADER}\n\n❓ {question.question}\n\n{question.answer}"


def mark_answered(question, answer, answered_by):
    """Record an admin answer (after it was delivered) and add it to the chat history."""
    question.answer = answer
    question.status = Question.Status.ANSWERED
    question.answered_by = answered_by
    question.answered_at = timezone.now()
    question.save()
    ChatMessage.objects.create(chat_id=question.chat_id, role=ChatMessage.Role.ASSISTANT, text=answer)


def save_question_to_faq(question):
    """Turn an answered question into a knowledge entry so the bot can answer it next time."""
    if question.saved_to_faq or not question.answer:
        return False
    KnowledgeEntry.objects.create(
        title=question.question[:300], content=question.answer, source=KnowledgeEntry.Source.FAQ
    )
    question.saved_to_faq = True
    question.save(update_fields=["saved_to_faq"])
    return True
