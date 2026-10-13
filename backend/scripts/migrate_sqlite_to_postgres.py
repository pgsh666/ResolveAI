"""One-time migration from the local ResolveAI SQLite database to PostgreSQL."""

from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import Base
from app.models import Approval, AuditEvent, KnowledgeDocument, Ticket


BACKEND_DIR = Path(__file__).resolve().parents[1]
SOURCE_DATABASE = BACKEND_DIR / "resolveai.db"
MODELS = (KnowledgeDocument, Ticket, AuditEvent, Approval)


def row_count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def column_values(instance: object, model: type) -> dict[str, object]:
    return {
        column.name: getattr(instance, column.name)
        for column in model.__table__.columns
    }


def migrate() -> None:
    settings = get_settings()
    if not settings.is_postgres:
        raise RuntimeError("DATABASE_URL 必须指向 PostgreSQL，迁移已取消")
    if not SOURCE_DATABASE.exists():
        raise FileNotFoundError(f"找不到 SQLite 数据库：{SOURCE_DATABASE}")

    source_engine = create_engine(
        f"sqlite:///{SOURCE_DATABASE.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    target_engine = create_engine(settings.database_url, pool_pre_ping=True)

    try:
        Base.metadata.create_all(bind=target_engine)
        with Session(source_engine) as source, Session(target_engine) as target:
            populated = {
                model.__tablename__: row_count(target, model)
                for model in MODELS
            }
            if any(populated.values()):
                summary = ", ".join(f"{name}={count}" for name, count in populated.items())
                raise RuntimeError(f"目标数据库不是空库（{summary}），迁移已取消")

            migrated: dict[str, int] = {}
            for model in MODELS:
                records = list(source.scalars(select(model)).all())
                if records:
                    target.execute(
                        model.__table__.insert(),
                        [column_values(record, model) for record in records],
                    )
                migrated[model.__tablename__] = len(records)
            target.commit()

        print("SQLite -> PostgreSQL migration completed")
        for table_name, count in migrated.items():
            print(f"{table_name}: {count}")
    finally:
        source_engine.dispose()
        target_engine.dispose()


if __name__ == "__main__":
    migrate()
