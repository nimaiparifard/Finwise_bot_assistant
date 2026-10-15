"""
Hybrid retrieval over the course knowledge base, built on LlamaIndex.

  question ──┬─> BM25 (keyword, Persian-normalised) ──┐
             │                                         ├─> reciprocal-rank fusion ─> top-k nodes
             └─> vector search (embeddings) ──────────┘          │
                                                      add each course's "facts" node
                                                                 │
                                                       <retrieved_context> text

The index lives in memory and is rebuilt (reusing cached embeddings) when courses or knowledge
entries change. If the embedding service is down the search degrades to BM25 only.
"""

import hashlib
import logging
import threading
import time

from django.conf import settings
from django.db.models import Count, Max
from llama_index.core import VectorStoreIndex
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.llms import MockLLM
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode
from llama_index.embeddings.openai_like import OpenAILikeEmbedding
from llama_index.retrievers.bm25 import BM25Retriever

from courses.models import Course, KnowledgeEntry

from .nodes import FACTS, build_nodes
from .text import expand_query, normalize

logger = logging.getLogger(__name__)

FRESHNESS_SECONDS = 20  # how often to check the database for changes
SHORT_FOLLOWUP_CHARS = 60  # a shorter question is probably a follow-up ("و قیمتش؟")


class NormalizedBM25Retriever(BaseRetriever):
    """BM25 over Persian-normalised text that returns the original (un-normalised) nodes."""

    def __init__(self, nodes, top_k):
        super().__init__()
        self._originals = {node.node_id: node for node in nodes}
        normalized = [TextNode(id_=node.node_id, text=normalize(node.text)) for node in nodes]
        self._bm25 = BM25Retriever.from_defaults(nodes=normalized, similarity_top_k=top_k, skip_stemming=True)

    def _retrieve(self, query_bundle):
        hits = self._bm25.retrieve(normalize(query_bundle.query_str))
        return [NodeWithScore(node=self._originals[h.node.node_id], score=h.score) for h in hits if h.score > 0]


# --- Embeddings ---------------------------------------------------------------------

_embedding_cache = {}  # sha1(model + text) -> vector, so a rebuild only embeds new/changed nodes


def get_embed_model():
    return OpenAILikeEmbedding(
        model_name=settings.EMBEDDING_MODEL,
        api_key=settings.EMBEDDING_API_KEY or "unused",
        api_base=settings.EMBEDDING_BASE_URL or "https://api.openai.com/v1",
        embed_batch_size=32,
        timeout=30,
    )


def attach_embeddings(nodes, embed_model):
    def key(node):
        return hashlib.sha1(f"{settings.EMBEDDING_MODEL}\0{node.text}".encode()).hexdigest()

    missing = [n for n in nodes if key(n) not in _embedding_cache]
    if missing:
        vectors = embed_model.get_text_embedding_batch([n.text for n in missing])
        _embedding_cache.update({key(n): v for n, v in zip(missing, vectors)})
    for node in nodes:
        node.embedding = _embedding_cache[key(node)]
    live = {key(n) for n in nodes}
    for stale in [k for k in _embedding_cache if k not in live]:
        del _embedding_cache[stale]


# --- Index snapshot -------------------------------------------------------------------

class Snapshot:
    def __init__(self, fingerprint, nodes, bm25=None, hybrid=None):
        self.fingerprint = fingerprint
        self.nodes = nodes
        self.by_id = {n.node_id: n for n in nodes}
        self.facts = {n.metadata["ref"]: n for n in nodes if n.metadata.get("part") == FACTS}
        self.bm25 = bm25
        self.hybrid = hybrid  # None when embeddings are unavailable

    @property
    def mode(self):
        return "hybrid" if self.hybrid else "bm25" if self.bm25 else "empty"

    def retrieve(self, query):
        if not self.nodes or not query.strip():
            return []
        bundle = QueryBundle(query)
        try:
            hits = (self.hybrid or self.bm25).retrieve(bundle)
        except Exception:  # noqa: BLE001 - e.g. the embedding API is down: keyword search still works
            logger.exception("Hybrid retrieval failed; falling back to keyword search")
            hits = self.bm25.retrieve(bundle)
        return [self.by_id[h.node.node_id] for h in hits[: settings.RAG_TOP_K] if h.node.node_id in self.by_id]


def build_snapshot(fingerprint):
    nodes = build_nodes()
    if not nodes:
        return Snapshot(fingerprint, nodes)
    candidates = max(settings.RAG_CANDIDATES, settings.RAG_TOP_K)
    bm25 = NormalizedBM25Retriever(nodes, candidates)
    hybrid = None
    try:
        embed_model = get_embed_model()
        attach_embeddings(nodes, embed_model)
        vector = VectorStoreIndex(nodes, embed_model=embed_model).as_retriever(similarity_top_k=candidates)
        hybrid = QueryFusionRetriever(
            [vector, bm25],
            llm=MockLLM(),  # unused with num_queries=1, but stops LlamaIndex from resolving a default LLM
            mode="reciprocal_rerank",
            similarity_top_k=settings.RAG_TOP_K,
            num_queries=1,
            use_async=False,
        )
    except Exception:  # noqa: BLE001 - keep the bot working on keyword search alone
        logger.exception("Could not build the vector index; using keyword search only")
    snapshot = Snapshot(fingerprint, nodes, bm25=bm25, hybrid=hybrid)
    logger.info("RAG index built: %d nodes, mode=%s", len(nodes), snapshot.mode)
    return snapshot


def data_fingerprint():
    parts = []
    for model in (Course, KnowledgeEntry):
        stats = model.objects.filter(is_active=True).aggregate(count=Count("pk"), latest=Max("updated_at"))
        parts.append(f"{stats['count']}:{stats['latest']}")
    return "|".join(parts)


_lock = threading.Lock()
_snapshot = None
_checked_at = 0.0


def get_snapshot():
    global _snapshot, _checked_at
    with _lock:
        now = time.monotonic()
        if _snapshot is None or now - _checked_at > FRESHNESS_SECONDS:
            fingerprint = data_fingerprint()
            if _snapshot is None or fingerprint != _snapshot.fingerprint:
                _snapshot = build_snapshot(fingerprint)
            _checked_at = now
        return _snapshot


def reset():
    """Forget the cached index (used by tests and after bulk imports)."""
    global _snapshot, _checked_at
    with _lock:
        _snapshot, _checked_at = None, 0.0
        _embedding_cache.clear()


# --- Query and context formatting ------------------------------------------------------

def build_query(messages):
    """The latest user question, plus the one before it when it is a short follow-up."""
    questions = [m["content"] for m in messages if m["role"] == "user"]
    if not questions:
        return ""
    query = questions[-1]
    if len(questions) > 1 and len(query) < SHORT_FOLLOWUP_CHARS:
        query = f"{questions[-2]}\n{query}"
    return expand_query(query[-1000:])


def format_context(nodes, facts):
    """Group passages by source; every retrieved course also gets its facts (price, dates, status)."""
    groups = {}
    for node in nodes:
        groups.setdefault(node.metadata["ref"], []).append(node)
    blocks = []
    for number, (ref, group) in enumerate(groups.items(), start=1):
        fact = facts.get(ref)
        if fact is not None:
            group = [fact] + [n for n in group if n.node_id != fact.node_id]
        kind = group[0].metadata["kind"]
        body = "\n\n".join(n.text for n in group)
        blocks.append(f'<source n="{number}" type="{kind}">\n{body}\n</source>')
    return "\n".join(blocks)


def retrieve_context(messages):
    """Relevant passages for the conversation as prompt text, or None if there is nothing to retrieve."""
    snapshot = get_snapshot()
    nodes = snapshot.retrieve(build_query(messages))
    if not nodes:
        return None
    logger.info("RAG (%s) retrieved: %s", snapshot.mode, ", ".join(n.node_id for n in nodes))
    return format_context(nodes, snapshot.facts)
