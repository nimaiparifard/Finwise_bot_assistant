"""Turns courses and knowledge entries from MongoDB into self-contained LlamaIndex nodes."""

from django.conf import settings
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode

from courses.models import Course, KnowledgeEntry

COURSE_TYPE_FA = {"online": "آنلاین (زنده)", "offline": "آفلاین (ضبط‌شده)", "other": "سایر"}

FACTS = "facts"
DETAILS = "details"


def get_splitter():
    # Word-based sizes: deterministic, offline (no tiktoken download) and fine for Persian.
    return SentenceSplitter(
        chunk_size=settings.RAG_CHUNK_WORDS,
        chunk_overlap=settings.RAG_CHUNK_OVERLAP,
        tokenizer=str.split,
        secondary_chunking_regex=r"[^,.;؛،?؟!]+[,.;؛،?؟!]?",
    )


def make_node(node_id, text, **metadata):
    node = TextNode(id_=node_id, text=text, metadata=metadata)
    # The text is already self-contained (it starts with the course/entry title); keep metadata
    # out of the embedded and LLM-visible text.
    node.excluded_embed_metadata_keys = list(metadata)
    node.excluded_llm_metadata_keys = list(metadata)
    return node


def course_facts_text(course):
    fields = [
        ("نوع دوره", COURSE_TYPE_FA.get(course.course_type, course.course_type)),
        ("دسته‌بندی", "، ".join(course.categories or [])),
        ("مدرس", course.instructor),
        ("مدت", course.duration),
        ("قیمت", course.price_toman_display),
        ("تاریخ شروع", course.start_date),
        ("بازه برگزاری", course.schedule),
        ("روز برگزاری", course.class_days),
        ("وضعیت ثبت‌نام", "در حال ثبت‌نام" if course.registration_open else "ثبت‌نام بسته است / اعلام نشده"),
        ("لینک", course.url),
    ]
    lines = [f"دوره: {course.title}"] + [f"- {label}: {value}" for label, value in fields if value]
    if course.summary:
        lines.append(f"- خلاصه: {course.summary}")
    return "\n".join(lines)


def course_nodes(course, splitter):
    ref = f"course:{course.pk}"
    meta = {"kind": "course", "ref": ref, "title": course.title, "url": course.url}
    nodes = [make_node(f"{ref}:{FACTS}", course_facts_text(course), part=FACTS, **meta)]
    sections = [
        ("توضیحات", course.description),
        ("درباره مدرس", f"{course.instructor}: {course.instructor_bio}" if course.instructor_bio else ""),
        ("نکات تکمیلی", course.extra_notes),
    ]
    for label, body in sections:
        for i, chunk in enumerate(splitter.split_text(body) if body.strip() else []):
            # Prefix every chunk with the course title so it still makes sense on its own.
            text = f"دوره: {course.title}\n{label}: {chunk}"
            nodes.append(make_node(f"{ref}:{label}:{i}", text, part=DETAILS, **meta))
    return nodes


def entry_nodes(entry, splitter):
    ref = f"entry:{entry.pk}"
    meta = {"kind": "info", "ref": ref, "title": entry.title, "url": "", "part": DETAILS}
    if entry.source == KnowledgeEntry.Source.FAQ:
        head, label = "پرسش متداول", "پاسخ"
    else:
        head, label = "اطلاعات مرکز", "متن"
    chunks = splitter.split_text(entry.content) if entry.content.strip() else [""]
    return [
        make_node(f"{ref}:{i}", f"{head}: {entry.title}\n{label}: {chunk}", **meta) for i, chunk in enumerate(chunks)
    ]


def build_nodes():
    """All nodes for the active courses and knowledge entries, in a stable order."""
    splitter = get_splitter()
    nodes = []
    for course in Course.objects.filter(is_active=True).order_by("title", "source_id"):
        nodes += course_nodes(course, splitter)
    for entry in KnowledgeEntry.objects.filter(is_active=True).order_by("source", "title"):
        nodes += entry_nodes(entry, splitter)
    return nodes
