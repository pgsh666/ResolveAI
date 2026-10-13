"""Build a new semantic index without changing documents or their legacy vectors."""

from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import KnowledgeDocument, KnowledgeEmbedding, new_id
from app.services.embeddings import EmbeddingService


def ensure_semantic_index(session, embeddings: EmbeddingService) -> dict:
    documents = session.scalars(select(KnowledgeDocument)).all()
    hashes = {d.id: sha256(f"{d.title}\n{d.content}".encode()).hexdigest() for d in documents}
    existing = {
        row.document_id: row.content_sha256
        for row in session.scalars(select(KnowledgeEmbedding).where(
            KnowledgeEmbedding.profile == embeddings.profile,
        )).all()
    }
    missing = [d for d in documents if existing.get(d.id) != hashes[d.id]]
    if missing:
        vectors = embeddings.embed_documents([f"{d.title}\n{d.content}" for d in missing])
        insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
        for document, vector in zip(missing, vectors, strict=True):
            statement = insert(KnowledgeEmbedding).values(
                id=new_id(), document_id=document.id, profile=embeddings.profile,
                content_sha256=hashes[document.id], dimensions=len(vector), embedding=vector,
            )
            session.execute(statement.on_conflict_do_update(
                index_elements=["document_id", "profile"],
                set_={"content_sha256": hashes[document.id], "dimensions": len(vector), "embedding": vector},
            ))
        session.commit()
    return {"profile": embeddings.profile, "documents": len(documents), "updated": len(missing)}
