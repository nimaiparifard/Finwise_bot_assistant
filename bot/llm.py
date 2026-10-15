"""
Answers user questions with an LLM, grounded only in the course knowledge base.

The provider is chosen with LLM_PROVIDER:
  * "openai"    - OpenAI or any OpenAI-compatible server (set OPENAI_BASE_URL), via Chat Completions
  * "anthropic" - Claude via the Anthropic API
"""

import json
import logging
import re
from dataclasses import dataclass

import anthropic
import openai
from django.conf import settings

from .prompts import SYSTEM_PROMPT, with_retrieved_context

logger = logging.getLogger(__name__)

INSTRUCTIONS = SYSTEM_PROMPT

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "can_answer": {"type": "boolean"},
        "answer": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["can_answer", "answer", "reason"],
    "additionalProperties": False,
}

FALLBACK_HANDOFF = (
    "سؤال شما برای کارشناسان فین‌وایز ارسال شد و به‌زودی پاسخ را همین‌جا دریافت می‌کنید. 🙏\n"
    "Your question has been forwarded to the FinWise team; you'll get a reply here soon."
)


@dataclass
class LLMResult:
    can_answer: bool
    answer: str
    reason: str = ""


def handoff(reason):
    return LLMResult(can_answer=False, answer=FALLBACK_HANDOFF, reason=reason)


_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def strip_markdown(text):
    """Replies go out as plain text (Telegram, web bubbles), so drop any Markdown the model slips in."""
    text = _MD_LINK.sub(
        lambda m: m.group(2) if m.group(1).strip().startswith("http") else f"{m.group(1).strip()}: {m.group(2)}", text
    )
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)
    return text.replace("`", "")


def parse_result(text):
    """Turn the model's JSON text into an LLMResult; anything unusable becomes a hand-off."""
    cleaned = text.strip()
    if cleaned.startswith("```"):  # some OpenAI-compatible models wrap JSON in a code fence
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(cleaned)
        result = LLMResult(
            bool(data["can_answer"]), strip_markdown(str(data["answer"])).strip(), str(data.get("reason", ""))
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        logger.exception("Could not parse LLM output: %r", text[:500])
        return handoff("پاسخ مدل زبانی قابل‌خواندن نبود")
    if not result.answer:
        if result.can_answer:
            return handoff("مدل زبانی پاسخ خالی برگرداند")
        result.answer = FALLBACK_HANDOFF
    return result


async def answer_question(knowledge_text, messages, retrieved=None):
    """`messages` is the recent conversation (alternating roles, ending with the user's question).

    `knowledge_text` is the static part of the prompt (cacheable); `retrieved` holds the passages
    found by hybrid search for this question and is attached to the latest user message.
    """
    messages = with_retrieved_context(messages, retrieved)
    if settings.LLM_PROVIDER == "anthropic":
        return await answer_with_anthropic(knowledge_text, messages)
    return await answer_with_openai(knowledge_text, messages)


# --- OpenAI / OpenAI-compatible ----------------------------------------------------

_openai_client = None


def get_openai_client():
    global _openai_client
    if _openai_client is None:
        _openai_client = openai.AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY or None,  # None -> the SDK reads OPENAI_API_KEY itself
            base_url=settings.OPENAI_BASE_URL or None,  # None -> https://api.openai.com/v1
            timeout=60,
        )
    return _openai_client


async def answer_with_openai(knowledge_text, messages):
    chat = [
        # Static instructions + knowledge first, so servers with automatic prefix caching can reuse them.
        {"role": "system", "content": f"{INSTRUCTIONS}\n<knowledge_base>\n{knowledge_text}\n</knowledge_base>"},
        *messages,
    ]
    strict_format = {
        "type": "json_schema",
        "json_schema": {"name": "course_answer", "strict": True, "schema": OUTPUT_SCHEMA},
    }
    client = get_openai_client()
    try:
        try:
            response = await client.chat.completions.create(
                model=settings.OPENAI_MODEL, messages=chat, response_format=strict_format,
            )
        except openai.BadRequestError as exc:
            # Many OpenAI-compatible servers don't support json_schema; fall back to plain JSON mode.
            logger.warning("json_schema output rejected (%s); retrying with json_object", exc.message)
            response = await client.chat.completions.create(
                model=settings.OPENAI_MODEL, messages=chat, response_format={"type": "json_object"},
            )
    except openai.APIConnectionError as exc:
        logger.error("OpenAI connection error (%s): %s", settings.OPENAI_BASE_URL or "api.openai.com", exc)
        return handoff("مدل زبانی در دسترس نبود (خطای اتصال)")
    except openai.RateLimitError as exc:
        logger.error("OpenAI rate limited: %s", exc)
        return handoff("مدل زبانی در دسترس نبود (محدودیت تعداد درخواست)")
    except openai.APIStatusError as exc:
        logger.error("OpenAI API error %s: %s", exc.status_code, exc.message)
        return handoff(f"خطای مدل زبانی ({exc.status_code})")
    except openai.OpenAIError as exc:
        logger.error("OpenAI error: %s", exc)
        return handoff("خطای مدل زبانی")

    if response.usage:
        details = getattr(response.usage, "prompt_tokens_details", None)
        logger.info(
            "OpenAI usage: prompt=%s cached=%s completion=%s",
            response.usage.prompt_tokens, getattr(details, "cached_tokens", None), response.usage.completion_tokens,
        )
    if not response.choices:
        return handoff("مدل زبانی پاسخی برنگرداند")
    choice = response.choices[0]
    if getattr(choice.message, "refusal", None) or choice.finish_reason == "content_filter":
        return handoff("مدل زبانی از پاسخ دادن خودداری کرد")
    if choice.finish_reason == "length":
        return handoff("پاسخ مدل زبانی ناقص قطع شد")
    return parse_result(choice.message.content or "")


# --- Anthropic (Claude) --------------------------------------------------------------

_anthropic_client = None


def get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic()  # reads ANTHROPIC_API_KEY from the environment
    return _anthropic_client


async def answer_with_anthropic(knowledge_text, messages):
    system = [
        {"type": "text", "text": INSTRUCTIONS},
        {
            "type": "text",
            "text": f"<knowledge_base>\n{knowledge_text}\n</knowledge_base>",
            # Course data rarely changes, so cache the whole system prompt across users.
            "cache_control": {"type": "ephemeral"},
        },
    ]
    try:
        response = await get_anthropic_client().beta.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=8000,
            system=system,
            messages=messages,
            output_config={
                "effort": settings.ANTHROPIC_EFFORT,
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
            # If a safety classifier declines, let the API retry on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIConnectionError as exc:
        logger.error("Claude API connection error: %s", exc)
        return handoff("مدل زبانی در دسترس نبود (خطای اتصال)")
    except anthropic.RateLimitError as exc:
        logger.error("Claude API rate limited: %s", exc)
        return handoff("مدل زبانی در دسترس نبود (محدودیت تعداد درخواست)")
    except anthropic.APIStatusError as exc:
        logger.error("Claude API error %s: %s", exc.status_code, exc.message)
        return handoff(f"خطای مدل زبانی ({exc.status_code})")
    except anthropic.AnthropicError as exc:
        logger.error("Claude API error: %s", exc)
        return handoff("خطای مدل زبانی")

    usage = response.usage
    logger.info(
        "Claude usage: input=%s cache_read=%s cache_write=%s output=%s",
        usage.input_tokens, usage.cache_read_input_tokens, usage.cache_creation_input_tokens, usage.output_tokens,
    )
    if response.stop_reason == "refusal":
        return handoff("مدل زبانی از پاسخ دادن خودداری کرد")
    if response.stop_reason == "max_tokens":
        return handoff("پاسخ مدل زبانی ناقص قطع شد")
    return parse_result(next((block.text for block in response.content if block.type == "text"), ""))
