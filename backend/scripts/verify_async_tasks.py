"""Black-box verification for Redis/RQ jobs and SSE progress delivery."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


API_URL = os.environ.get("VERIFY_API_URL", "http://127.0.0.1:8000/api/v1").rstrip("/")
CLIENT = None
CSRF = ""


def request_json(method: str, path: str, payload: dict | None = None) -> dict | list:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{API_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "X-CSRF-Token": CSRF},
    )
    with CLIENT.open(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_with_sse(task: dict, timeout: int = 240) -> tuple[dict, list[int]]:
    request = urllib.request.Request(
        f"{API_URL.removesuffix('/api/v1')}{task['event_url']}",
        headers={"Accept": "text/event-stream"},
    )
    progress: list[int] = []
    event_name = "progress"
    with CLIENT.open(request, timeout=timeout) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                snapshot = json.loads(line.split(":", 1)[1].strip())
                progress.append(snapshot["progress"])
                if event_name == "failed":
                    raise RuntimeError(snapshot.get("error") or snapshot["message"])
                if event_name == "completed":
                    return snapshot, progress
    raise RuntimeError("SSE stream ended before a terminal task event")


def main() -> None:
    from scripts.auth_client import acceptance_client
    global CLIENT, CSRF
    CLIENT, CSRF = acceptance_client(API_URL)
    health = request_json("GET", "/health")
    queue_check = health["checks"]["queue"]
    assert queue_check.startswith("ready:"), queue_check

    ticket = request_json(
        "POST",
        "/tickets",
        {
            "customer_name": "异步验收用户",
            "subject": "异步任务验证：物流停滞",
            "message": "订单三天没有物流更新，请帮助核查。",
            "order_id": "ORD-ASYNC-VERIFY",
            "amount": 88,
        },
    )
    agent_task = request_json("POST", f"/tasks/agent-analysis/{ticket['id']}")
    agent_result, agent_progress = wait_with_sse(agent_task)
    assert agent_result["status"] == "finished"
    assert agent_result["result"]["ticket_id"] == ticket["id"]

    import_task = request_json(
        "POST",
        "/tasks/knowledge-import",
        {
            "documents": [
                {
                    "title": "异步任务验收文档",
                    "source": "policy://verification/async-task",
                    "category": "general",
                    "content": "这是用于验证 Redis 队列、后台向量生成与 SSE 进度推送的一条知识文档。",
                }
            ]
        },
    )
    import_result, import_progress = wait_with_sse(import_task)
    assert import_result["status"] == "finished"
    assert import_result["result"]["total"] == 1

    evaluation_task = request_json("POST", "/tasks/evaluation", {"limit": 3})
    evaluation_result, evaluation_progress = wait_with_sse(evaluation_task, timeout=600)
    assert evaluation_result["status"] == "finished"
    assert evaluation_result["result"]["evaluated_cases"] == 3
    assert evaluation_result["result"]["metrics"]["unapproved_write_count"]["count"] == 0

    summary: dict[str, Any] = {
        "queue": queue_check,
        "agent_analysis": {
            "task_id": agent_result["id"],
            "progress_events": agent_progress,
            "ticket_no": agent_result["result"]["ticket_no"],
        },
        "knowledge_import": {
            "task_id": import_result["id"],
            "progress_events": import_progress,
            "result": import_result["result"],
        },
        "batch_evaluation": {
            "task_id": evaluation_result["id"],
            "progress_events": evaluation_progress,
            "passed": evaluation_result["result"]["passed_cases"],
            "evaluated": evaluation_result["result"]["evaluated_cases"],
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if os.environ.get("VERIFY_OUTPUT_PATH"):
        output = Path(os.environ["VERIFY_OUTPUT_PATH"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {error.code}: {detail}") from error
