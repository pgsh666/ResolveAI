"""Binary pg_dump backup + isolated restore rehearsal; never drops a database."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        # Do not print commands/environments that may contain connection secrets.
        raise RuntimeError(f"Database safety operation failed (exit {result.returncode})")
    return result.stdout.strip()


def fingerprint(docker, container, database):
    def sql(query):
        return run([docker, "exec", container, "psql", "-U", "resolveai", "-d", database, "-At", "-c", query])
    tables = sql("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").splitlines()
    result = {}
    for table in tables:
        if not re.fullmatch(r"[a-z_0-9]+", table):
            raise ValueError("Unsupported table identifier; no unsafe SQL generated")
        canonical = "to_jsonb(t)::text"
        if table == "langgraph_checkpoint_writes":
            canonical = "(to_jsonb(t)-'task_path')::text"  # new migration's metadata-only column
        data = sql(f'SELECT count(*),md5(coalesce(string_agg(md5({canonical}),\'\' ORDER BY md5({canonical})),\'\')) FROM "{table}" t')
        count, digest = data.split("|")
        result[table] = {"rows": int(count), "content_digest": digest}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--container", required=True)
    parser.add_argument("--restore-check", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "output/hardening/database-backup.json")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", args.container):
        raise ValueError("Invalid container")
    identity = uuid4().hex
    directory = ROOT / ".backups"
    directory.mkdir(exist_ok=True)
    if os.name != "nt": directory.chmod(0o700)
    backup = directory / f"resolveai-{identity}.dump"
    temporary = f"/tmp/resolveai-backup-{identity}.dump"
    before = fingerprint(args.docker, args.container, "resolveai")
    run([args.docker, "exec", args.container, "pg_dump", "-U", "resolveai", "-d", "resolveai", "-Fc", "-f", temporary])
    run([args.docker, "cp", f"{args.container}:{temporary}", str(backup)])
    if os.name != "nt": backup.chmod(0o600)
    report = {"backup_file": str(backup), "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(), "before": before,
              "binary_custom_format": True, "business_database_changed": False, "restore_verified": False}
    if args.restore_check:
        database = f"resolveai_restorecheck_{identity}"
        # Always CREATE a fresh UUID database, never --clean and never overwrite business data.
        run([args.docker, "exec", args.container, "createdb", "-U", "resolveai", database])
        run([args.docker, "exec", args.container, "pg_restore", "-U", "resolveai", "-d", database, "--exit-on-error", temporary])
        after = fingerprint(args.docker, args.container, database)
        report.update(restore_database=database, restored=after, restore_verified=before == after)
        if before != after: raise RuntimeError("Restored content does not match; backup and isolated clone retained")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"restore_verified": report["restore_verified"], "tables": len(before), "report": str(args.output)}))
    return 0


if __name__ == "__main__": raise SystemExit(main())
