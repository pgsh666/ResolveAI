from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import text


def migrate_database(engine, revision="head"):
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(724831912)"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        config.attributes["connection"] = connection
        command.upgrade(config, revision)
