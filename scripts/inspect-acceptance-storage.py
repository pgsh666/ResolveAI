"""Read-only pgvector/checkpoint proof from the isolated Compose project."""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSPECTION = """
import json
from sqlalchemy import text
from app.core.database import SessionLocal
with SessionLocal() as session:
    result = {
        'database': session.execute(text('select current_database()')).scalar(),
        'vector_extension': session.execute(text("select extversion from pg_extension where extname='vector'")).scalar(),
        'documents': session.execute(text('select count(*) from knowledge_documents')).scalar(),
        'legacy_dimensions': list(session.execute(text('select distinct vector_dims(embedding) from knowledge_documents')).scalars()),
        'semantic_dimensions': list(session.execute(text('select distinct vector_dims(embedding) from knowledge_embeddings')).scalars()),
        'semantic_rows': session.execute(text('select count(*) from knowledge_embeddings')).scalar(),
        'semantic_type': session.execute(text('select pg_typeof(embedding)::text from knowledge_embeddings limit 1')).scalar(),
        'checkpoint_rows': session.execute(text('select count(*) from workflow_checkpoints')).scalar(),
        'completed_write_rows': session.execute(text("select count(*) from tool_executions where status='completed'")).scalar(),
    }
    assert result['vector_extension'] and result['legacy_dimensions'] == [256]
    assert result['semantic_dimensions'] == [512] and result['semantic_type'] == 'vector'
    assert result['semantic_rows'] >= result['documents'] and result['checkpoint_rows'] > 0
    print(json.dumps(result))
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--docker", default="docker")
    args = parser.parse_args()
    result = subprocess.run([args.docker, "compose", "-p", "resolveai-acceptance", "-f", "docker-compose.yml",
                             "-f", "docker-compose.acceptance.yml", "exec", "-T", "backend", "python", "-c", INSPECTION],
                            cwd=ROOT, text=True, capture_output=True, check=True, timeout=30)
    data = json.loads(result.stdout)
    output = ROOT / "output" / "closeout" / "postgres-evidence.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
