import os
from alembic import context
from sqlalchemy import create_engine, pool
from app.core.config import get_settings
from app.core.database import Base
from app import models  # noqa: F401

NEW_TABLES = {"accounts", "login_sessions", "login_attempts", "ticket_access", "task_access",
              "knowledge_access", "workflow_runs", "model_usage"}


def include_object(obj, name, kind, reflected, compare_to):
    if os.environ.get("MIGRATION_GENERATE_LEGACY") == "1":
        if kind == "table" and name in NEW_TABLES:
            return False
    return True


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, include_object=include_object,
                      compare_type=True, render_as_batch=connection.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


provided = context.config.attributes.get("connection")
if provided is not None:
    run(provided)
elif context.is_offline_mode():
    context.configure(url=os.environ.get("MIGRATION_DATABASE_URL") or get_settings().database_url,
                      target_metadata=Base.metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(os.environ.get("MIGRATION_DATABASE_URL") or get_settings().database_url, poolclass=pool.NullPool)
    with engine.begin() as connection:
        run(connection)
