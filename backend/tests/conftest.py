import os
import tempfile
from pathlib import Path

import pytest


TEST_DB = Path(tempfile.mkdtemp(prefix="resolveai-tests-")) / "test_resolveai.db"
os.environ["APP_ENV"] = "test"
os.environ["AUTH_ENABLED"] = "false"
os.environ["AGENT_MODE"] = "demo"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["MCP_ENABLED"] = "false"
os.environ["EMBEDDING_PROVIDER"] = "local"


@pytest.fixture(scope="session", autouse=True)
def clean_test_database():
    if TEST_DB.exists():
        TEST_DB.unlink()
    yield
    from app.core.database import engine

    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()
