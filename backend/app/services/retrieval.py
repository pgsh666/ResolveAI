import json
import math
import threading
import time
from collections import OrderedDict
from functools import lru_cache
from hashlib import sha256

from opentelemetry.trace import SpanKind, Status, StatusCode
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.observability import get_tracer, hash_text, record_error, safe_payload
from app.models import KnowledgeDocument, KnowledgeEmbedding
from app.schemas import Citation
from app.services.embeddings import EmbeddingService
from app.services.performance import get_performance_registry
from app.services.auth import knowledge_scope


_LOCAL_CACHE: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()
_LOCAL_CACHE_LOCK = threading.Lock()
_LOCAL_CACHE_VERSION = 1


@lru_cache
def _redis_client(redis_url: str) -> Redis:
    return Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=0.25,
        socket_timeout=0.5,
    )


def invalidate_retrieval_cache() -> None:
    global _LOCAL_CACHE_VERSION
    settings = get_settings()
    with _LOCAL_CACHE_LOCK:
        _LOCAL_CACHE.clear()
        _LOCAL_CACHE_VERSION += 1
    if settings.async_tasks_enabled:
        try:
            _redis_client(settings.redis_url).incr("resolveai:retrieval:version")
        except Exception:
            pass


class RetrievalService:
    def __init__(self, session: Session, embeddings: EmbeddingService | None = None, workspace_id: str = "default") -> None:
        self.session = session
        self.embeddings = embeddings or EmbeddingService()
        self.settings = get_settings()
        self.last_cache_hit = False
        self.workspace_id = workspace_id

    def search(self, query: str, limit: int = 3) -> list[Citation]:
        limit = min(limit, self.settings.rag_max_documents)
        with get_tracer("rag").start_as_current_span(
            "resolveai.rag.vector_search",
            kind=SpanKind.CLIENT,
            attributes={
                "resolveai.rag.query_hash": hash_text(query),
                "resolveai.rag.query_length": len(query),
                "resolveai.rag.limit": limit,
                "resolveai.rag.engine": "pgvector" if self.settings.is_postgres else "memory",
            },
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                cached = self._cache_get(query, limit)
                if cached:
                    active_ids = set(self.session.scalars(select(KnowledgeDocument.id).where(
                        KnowledgeDocument.id.in_([c.id for c in cached]), KnowledgeDocument.enabled.is_(True),
                        knowledge_scope(self.workspace_id))).all())
                    if len(active_ids) != len(cached):
                        cached = None  # A different process may have disabled a document.
                if cached is not None:
                    self.last_cache_hit = True
                    get_performance_registry().record_cache(True)
                    span.set_attribute("resolveai.rag.cache_hit", True)
                    span.set_attribute("resolveai.rag.result_count", len(cached))
                    span.set_status(Status(StatusCode.OK))
                    return cached
                self.last_cache_hit = False
                get_performance_registry().record_cache(False)
                span.set_attribute("resolveai.rag.cache_hit", False)
                semantic = self.embeddings.provider == "fastembed"
                if semantic:
                    from app.services.knowledge_index import ensure_semantic_index

                    ensure_semantic_index(self.session, self.embeddings)
                query_embedding = self.embeddings.embed_query(query)
                if self.settings.is_postgres and semantic:
                    distance = KnowledgeEmbedding.embedding.cosine_distance(query_embedding)
                    rows = self.session.execute(
                        select(KnowledgeDocument, (1 - distance).label("score"))
                        .join(KnowledgeEmbedding, KnowledgeEmbedding.document_id == KnowledgeDocument.id)
                        .where(KnowledgeEmbedding.profile == self.embeddings.profile, knowledge_scope(self.workspace_id), KnowledgeDocument.enabled.is_(True))
                        .order_by(distance).limit(limit)
                    ).all()
                    results = [self._citation(document, float(score)) for document, score in rows]
                elif self.settings.is_postgres:
                    distance = KnowledgeDocument.embedding.cosine_distance(query_embedding)
                    rows = self.session.execute(
                        select(KnowledgeDocument, (1 - distance).label("score"))
                        .where(knowledge_scope(self.workspace_id), KnowledgeDocument.enabled.is_(True))
                        .order_by(distance)
                        .limit(limit)
                    ).all()
                    results = [
                        self._citation(document, float(score)) for document, score in rows
                    ]
                else:
                    if semantic:
                        rows = self.session.execute(
                            select(KnowledgeDocument, KnowledgeEmbedding.embedding)
                            .join(KnowledgeEmbedding, KnowledgeEmbedding.document_id == KnowledgeDocument.id)
                            .where(KnowledgeEmbedding.profile == self.embeddings.profile, knowledge_scope(self.workspace_id), KnowledgeDocument.enabled.is_(True))
                        ).all()
                        scored = [(document, self._cosine_similarity(query_embedding, vector))
                                  for document, vector in rows]
                    else:
                        documents = self.session.scalars(select(KnowledgeDocument).where(knowledge_scope(self.workspace_id), KnowledgeDocument.enabled.is_(True))).all()
                        scored = [(document, self._cosine_similarity(query_embedding, document.embedding))
                                  for document in documents]
                    scored.sort(key=lambda row: row[1], reverse=True)
                    results = [
                        self._citation(document, score) for document, score in scored[:limit]
                    ]
                span.set_attribute("resolveai.rag.result_count", len(results))
                span.set_attribute(
                    "resolveai.rag.documents",
                    safe_payload(
                        [
                            {
                                "id": item.id,
                                "title": item.title,
                                "source": item.source,
                                "score": round(item.score, 4),
                            }
                            for item in results
                        ]
                    ),
                )
                span.set_status(Status(StatusCode.OK))
                self._cache_set(query, limit, results)
                return results
            except Exception as error:
                record_error(span, error)
                raise

    def search_reliable(
        self,
        query: str,
        limit: int = 3,
    ) -> tuple[list[Citation], dict]:
        with get_tracer("rag").start_as_current_span(
            "resolveai.rag.reliability_gate",
            attributes={"resolveai.rag.threshold": self.settings.rag_min_score},
        ) as span:
            threshold = (self.settings.semantic_rag_min_score
                         if self.embeddings.provider == "fastembed" else self.settings.rag_min_score)
            candidates = self.search(query, limit=limit)
            reliable = [
                item for item in candidates if item.score >= threshold
            ]
            top_score = max((item.score for item in candidates), default=0.0)
            status = {
                "reliable": bool(reliable),
                "threshold": threshold,
                "embedding_profile": self.embeddings.profile,
                "top_score": round(top_score, 4),
                "candidate_count": len(candidates),
                "reliable_count": len(reliable),
                "failure_reason": (
                    None
                    if reliable
                    else (
                        "知识库没有返回候选文档"
                        if not candidates
                        else "最高相似度低于可靠性阈值"
                    )
                ),
                "cache_hit": self.last_cache_hit,
            }
            span.set_attribute("resolveai.rag.reliable", bool(reliable))
            span.set_attribute("resolveai.rag.top_score", round(top_score, 4))
            span.set_attribute("resolveai.rag.reliable_count", len(reliable))
            if status["failure_reason"]:
                span.set_attribute("resolveai.rag.failure_reason", status["failure_reason"])
            return reliable, status

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        numerator = sum(a * b for a, b in zip(left, right, strict=False))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        return max(0.0, min(1.0, numerator / (left_norm * right_norm)))

    def _citation(self, document: KnowledgeDocument, score: float) -> Citation:
        size = self.settings.rag_snippet_chars
        snippet = document.content[:size] + ("…" if len(document.content) > size else "")
        return Citation(
            id=document.id,
            title=document.title,
            source=document.source,
            snippet=snippet,
            score=max(0.0, min(1.0, score)),
        )

    def _cache_key(self, query: str, limit: int) -> str:
        global _LOCAL_CACHE_VERSION
        version = _LOCAL_CACHE_VERSION
        if self.settings.async_tasks_enabled:
            try:
                client = _redis_client(self.settings.redis_url)
                version = int(client.get("resolveai:retrieval:version") or 1)
            except Exception:
                pass
        digest = sha256(
            f"{version}|{self.workspace_id}|{self.embeddings.profile}|{limit}|{query}".encode("utf-8")
        ).hexdigest()
        return f"resolveai:retrieval:v{version}:{digest}"

    def _cache_get(self, query: str, limit: int) -> list[Citation] | None:
        if not self.settings.retrieval_cache_enabled:
            return None
        key = self._cache_key(query, limit)
        if self.settings.async_tasks_enabled:
            try:
                raw = _redis_client(self.settings.redis_url).get(key)
                if raw:
                    return [Citation.model_validate(item) for item in json.loads(raw)]
            except Exception:
                pass
        now = time.monotonic()
        with _LOCAL_CACHE_LOCK:
            item = _LOCAL_CACHE.get(key)
            if item and item[0] > now:
                _LOCAL_CACHE.move_to_end(key)
                return [Citation.model_validate(value) for value in item[1]]
            if item:
                _LOCAL_CACHE.pop(key, None)
        return None

    def _cache_set(self, query: str, limit: int, results: list[Citation]) -> None:
        if not self.settings.retrieval_cache_enabled:
            return
        key = self._cache_key(query, limit)
        payload = [item.model_dump() for item in results]
        if self.settings.async_tasks_enabled:
            try:
                _redis_client(self.settings.redis_url).setex(
                    key,
                    self.settings.retrieval_cache_ttl_seconds,
                    json.dumps(payload),
                )
                return
            except Exception:
                pass
        with _LOCAL_CACHE_LOCK:
            _LOCAL_CACHE[key] = (
                time.monotonic() + self.settings.retrieval_cache_ttl_seconds,
                payload,
            )
            _LOCAL_CACHE.move_to_end(key)
            while len(_LOCAL_CACHE) > self.settings.retrieval_cache_max_entries:
                _LOCAL_CACHE.popitem(last=False)
