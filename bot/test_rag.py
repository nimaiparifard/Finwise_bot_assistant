import hashlib
from unittest import mock

from django.test import TestCase, override_settings
from llama_index.core.embeddings import BaseEmbedding

from courses.models import Course, KnowledgeEntry

from . import rag
from .knowledge import build_catalog_text, build_llm_context
from .prompts import with_retrieved_context
from .rag import retriever
from .rag.text import normalize


class BagOfWordsEmbedding(BaseEmbedding):
    """Deterministic offline embedder: words hashed into a 64-dim count vector."""

    def _vector(self, text):
        vec = [0.0] * 64
        for word in normalize(text).split():
            vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % 64] += 1.0
        return vec

    def _get_query_embedding(self, query):
        return self._vector(query)

    async def _aget_query_embedding(self, query):
        return self._vector(query)

    def _get_text_embedding(self, text):
        return self._vector(text)


def user(text):
    return [{"role": "user", "content": text}]


class NormalizeTests(TestCase):
    def test_persian_variants_digits_and_halfspaces(self):
        self.assertEqual(normalize("كتاب ي دوره‌ها"), "کتاب ی دوره ها")
        self.assertEqual(normalize("قیمت ۶۵,۰۰۰,۰۰۰ ریال ١٢٣"), "قیمت 65000000 ریال 123")


@override_settings(RAG_ENABLED=True, RAG_TOP_K=4, RAG_CANDIDATES=8, EMBEDDING_MODEL="fake")
class HybridRetrievalTests(TestCase):
    def setUp(self):
        rag.reset()
        self.addCleanup(rag.reset)
        patcher = mock.patch.object(retriever, "get_embed_model", return_value=BagOfWordsEmbedding())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tax = Course.objects.create(
            title="حسابداری مالیاتی", price_rial=35_000_000, registration_open=True, url="https://x.test/tax",
            description="آموزش اظهارنامه مالیات بر ارزش افزوده و مالیات بر درآمد شرکت‌ها.",
        )
        self.ifrs = Course.objects.create(
            title="گزارشگری مالی IFRS", price_rial=48_000_000, instructor="دکتر احمدی",
            description="استانداردهای بین‌المللی گزارشگری مالی برای شرکت‌های بزرگ.",
        )
        KnowledgeEntry.objects.create(title="استرداد وجه", content="تا یک هفته قبل از شروع دوره، بازگشت کامل هزینه.")

    def test_keyword_match_returns_course_with_its_facts(self):
        context = retriever.retrieve_context(user("اظهارنامه ارزش افزوده"))
        self.assertIn("حسابداری مالیاتی", context)
        self.assertIn("3,500,000 تومان", context)  # price comes from the facts node, not the matched chunk
        self.assertIn("https://x.test/tax", context)

    def test_policy_entry_is_retrieved(self):
        self.assertIn("بازگشت کامل هزینه", retriever.retrieve_context(user("استرداد وجه ثبت نام")))

    def test_follow_up_uses_previous_question(self):
        messages = [
            {"role": "user", "content": "دوره IFRS را توضیح بده"},
            {"role": "assistant", "content": "..."},
            {"role": "user", "content": "مدرسش کیه؟"},
        ]
        self.assertIn("دکتر احمدی", retriever.retrieve_context(messages))

    def test_index_is_hybrid_and_rebuilds_on_change(self):
        self.assertEqual(retriever.get_snapshot().mode, "hybrid")
        retriever._checked_at = 0  # let the freshness check run again
        KnowledgeEntry.objects.create(title="گواهینامه", content="گواهی پایان دوره پس از آزمون نهایی صادر می‌شود.")
        self.assertIn("آزمون نهایی", retriever.retrieve_context(user("گواهینامه پایان دوره")))

    def test_falls_back_to_bm25_when_embeddings_fail(self):
        broken = mock.MagicMock(side_effect=ConnectionError("embedding API down"))
        with mock.patch.object(retriever, "get_embed_model", broken):
            rag.reset()
            self.assertEqual(retriever.get_snapshot().mode, "bm25")
            self.assertIn("حسابداری مالیاتی", retriever.retrieve_context(user("اظهارنامه مالیات")))

    def test_nothing_relevant_or_empty_query(self):
        self.assertIsNone(retriever.retrieve_context(user("   ")))
        self.assertIsNone(retriever.retrieve_context([]))


@override_settings(RAG_ENABLED=True)
class ContextWiringTests(TestCase):
    def test_catalog_is_one_line_per_course(self):
        Course.objects.create(title="A", price_rial=10_000_000, registration_open=True)
        Course.objects.create(title="Hidden", is_active=False)
        catalog = build_catalog_text()
        self.assertIn("A", catalog)
        self.assertIn("ثبت‌نام باز", catalog)
        self.assertNotIn("Hidden", catalog)

    @override_settings(RAG_ENABLED=False)
    def test_rag_off_uses_full_knowledge_base(self):
        Course.objects.create(title="A", description="long description")
        knowledge, retrieved = build_llm_context(user("hi"))
        self.assertIn("long description", knowledge)
        self.assertIsNone(retrieved)

    def test_retrieved_context_attached_to_last_user_message_only(self):
        messages = [
            {"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]
        wrapped = with_retrieved_context(messages, "PASSAGE")
        self.assertEqual(wrapped[:2], messages[:2])
        self.assertIn("<retrieved_context>\nPASSAGE", wrapped[-1]["content"])
        self.assertIn("<user_question>\nq2", wrapped[-1]["content"])
        self.assertEqual(messages[-1]["content"], "q2")  # input not mutated
        self.assertIs(with_retrieved_context(messages, None), messages)


class EnglishSupportTests(TestCase):
    def test_glossary_expands_english_queries_to_persian_terms(self):
        from .rag.text import expand_query

        expanded = expand_query("How much do offline workshops cost?")
        for term in ("آفلاین", "کارگاه", "هزینه"):
            self.assertIn(term, expanded)
        self.assertEqual(expand_query("قیمت دوره چقدر است؟"), "قیمت دوره چقدر است؟")  # Persian untouched

    def test_english_question_gets_language_reminder_persian_does_not(self):
        wrapped = with_retrieved_context([{"role": "user", "content": "Do you have any tax courses?"}], None)
        self.assertIn("write your whole answer in English", wrapped[-1]["content"])
        persian = [{"role": "user", "content": "دوره مالیاتی دارید؟"}]
        self.assertIs(with_retrieved_context(persian, None), persian)
        mixed = [{"role": "user", "content": "دوره IFRS چقدر است؟"}]
        self.assertIs(with_retrieved_context(mixed, None), mixed)


class TomanOnlyTests(TestCase):
    def test_bot_visible_text_never_contains_rial_figures(self):
        from .knowledge import build_knowledge_text
        from .rag.nodes import course_facts_text

        course = Course.objects.create(title="Workshop", price_rial=12_000_000)
        for text in (course_facts_text(course), build_catalog_text(), build_knowledge_text()):
            self.assertIn("1,200,000 تومان", text)
            self.assertNotIn("12,000,000", text)
            self.assertNotIn("ریال", text)
