"""Builds the text the LLM answers from, out of the courses and knowledge entries in MongoDB."""

from django.conf import settings

from courses.models import Course, KnowledgeEntry

from .models import ChatMessage

COURSE_TYPE_FA = {"online": "آنلاین (زنده)", "offline": "آفلاین (ضبط‌شده)", "other": "سایر"}


def format_course(course):
    lines = [f"### {course.title}"]
    fields = [
        ("نوع دوره", COURSE_TYPE_FA.get(course.course_type, course.course_type)),
        ("دسته‌بندی", "، ".join(course.categories or [])),
        ("مدرس", course.instructor),
        ("درباره مدرس", course.instructor_bio),
        ("مدت", course.duration),
        ("قیمت", course.price_toman_display),
        ("تاریخ شروع", course.start_date),
        ("بازه برگزاری", course.schedule),
        ("روز برگزاری", course.class_days),
        ("وضعیت ثبت‌نام", "در حال ثبت‌نام" if course.registration_open else "ثبت‌نام بسته است / اعلام نشده"),
        ("خلاصه", course.summary),
        ("توضیحات", course.description),
        ("نکات تکمیلی", course.extra_notes),
        ("لینک", course.url),
    ]
    lines += [f"- {label}: {value}" for label, value in fields if value]
    return "\n".join(lines)


def build_knowledge_text():
    """Deterministic (stable ordering) so the prompt prefix stays cacheable between requests."""
    courses = Course.objects.filter(is_active=True).order_by("-registration_open", "title", "source_id")
    entries = KnowledgeEntry.objects.filter(is_active=True).order_by("source", "title")

    parts = ["## دوره‌ها (Courses)"]
    parts += [format_course(c) for c in courses] or ["(هیچ دوره فعالی ثبت نشده است)"]
    parts.append("## اطلاعات عمومی مرکز و پرسش‌های متداول (Center info & FAQ)")
    parts += [f"### {e.title}\n{e.content}" for e in entries]
    return "\n\n".join(parts)


def build_catalog_text():
    """One line per course: the always-present overview that RAG passages are layered on top of."""
    courses = Course.objects.filter(is_active=True).order_by("-registration_open", "title", "source_id")
    lines = []
    open_count = 0
    for c in courses:
        open_count += bool(c.registration_open)
        status = "ثبت‌نام باز" if c.registration_open else "ثبت‌نام بسته"
        kind = COURSE_TYPE_FA.get(c.course_type, c.course_type)
        parts = [c.title, kind, status, c.price_toman_display, f"شروع {c.start_date}" if c.start_date else "", c.url]
        lines.append("- " + " | ".join(p for p in parts if p))
    header = f"## فهرست دوره‌ها (Course catalog) - مجموع {len(lines)} دوره، {open_count} دوره در حال ثبت‌نام"
    return header + "\n" + ("\n".join(lines) or "(هیچ دوره فعالی ثبت نشده است)")


def build_llm_context(messages):
    """(static knowledge text, retrieved passages or None) for the conversation `messages`.

    With RAG the static part is just the course catalog (cacheable) and the passages most relevant
    to the question are retrieved separately; without RAG it is the entire knowledge base.
    """
    if not settings.RAG_ENABLED:
        return build_knowledge_text(), None
    from .rag import retrieve_context  # imported lazily: LlamaIndex is only needed when RAG is on

    return build_catalog_text(), retrieve_context(messages)


def recent_history(chat_id, turns):
    """Last `turns` messages of a chat, oldest first, as Claude message dicts with alternating roles."""
    # MongoDB stores times with millisecond precision; the ObjectId breaks ties in insertion order.
    rows = list(
        ChatMessage.objects.filter(channel=ChatMessage.Channel.TELEGRAM, chat_id=chat_id)
        .order_by("-created_at", "-id")[:turns]
    )
    messages = []
    for row in reversed(rows):
        if messages and messages[-1]["role"] == row.role:
            messages[-1]["content"] += "\n\n" + row.text
        else:
            messages.append({"role": row.role, "content": row.text})
    # The conversation sent to Claude must start with a user turn.
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages
