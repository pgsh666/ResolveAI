"""Create the schema and shared knowledge, without example business records."""

from sqlalchemy import func, select

from app.core.database import SessionLocal, engine, init_db
from app.models import KnowledgeDocument, Ticket
from app.services.seed import normalize_legacy_ticket_statuses, seed_database
from app.services.auth import ensure_bootstrap_accounts


def main() -> int:
    init_db()
    with SessionLocal() as session:
        seed_database(session)
        normalize_legacy_ticket_statuses(session)
        ensure_bootstrap_accounts(session)
        document_count = session.scalar(select(func.count(KnowledgeDocument.id))) or 0
        ticket_count = session.scalar(select(func.count(Ticket.id))) or 0
    engine.dispose()
    if document_count < 20:
        raise RuntimeError(f"知识库初始化不完整：仅有 {document_count} 篇文档")
    print(f"Database initialized: knowledge_documents={document_count}, tickets={ticket_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
