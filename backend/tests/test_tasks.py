from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.routes import get_task_queue_service
from app.main import app


def _snapshot(task_type: str, status: str = "queued") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": f"task-{task_type}",
        "task_type": task_type,
        "status": status,
        "progress": 100 if status == "finished" else 0,
        "stage": "completed" if status == "finished" else "queued",
        "message": "任务已完成" if status == "finished" else "任务已进入队列",
        "result": {"passed_cases": 60, "evaluated_cases": 60} if status == "finished" else None,
        "error": None,
        "created_at": now,
        "started_at": now if status == "finished" else None,
        "updated_at": now,
        "finished_at": now if status == "finished" else None,
    }


class FakeTaskQueue:
    def __init__(self) -> None:
        self.last_type = "agent_analysis"

    def enqueue_agent_analysis(self, ticket_id: str) -> dict:
        assert ticket_id
        self.last_type = "agent_analysis"
        return _snapshot(self.last_type)

    def enqueue_knowledge_import(self, documents: list[dict]) -> dict:
        assert documents[0]["source"] == "policy://test/task-import"
        self.last_type = "knowledge_import"
        return _snapshot(self.last_type)

    def enqueue_evaluation(self, limit: int | None) -> dict:
        assert limit == 3
        self.last_type = "batch_evaluation"
        return _snapshot(self.last_type)

    def snapshot(self, task_id: str) -> dict:
        assert task_id
        return _snapshot(self.last_type, "finished")


def test_background_task_endpoints_and_sse():
    tasks = FakeTaskQueue()
    app.dependency_overrides[get_task_queue_service] = lambda: tasks
    try:
        with TestClient(app) as client:
            ticket_id = client.get("/api/v1/tickets").json()[0]["id"]
            agent = client.post(f"/api/v1/tasks/agent-analysis/{ticket_id}")
            assert agent.status_code == 202, agent.text
            assert agent.json()["task_type"] == "agent_analysis"
            assert agent.json()["event_url"].endswith("/events")

            knowledge = client.post(
                "/api/v1/tasks/knowledge-import",
                json={
                    "documents": [
                        {
                            "title": "异步导入测试文档",
                            "source": "policy://test/task-import",
                            "content": "这是一条用于验证异步知识库导入参数的测试内容。",
                            "category": "general",
                        }
                    ]
                },
            )
            assert knowledge.status_code == 202, knowledge.text
            assert knowledge.json()["task_type"] == "knowledge_import"

            evaluation = client.post("/api/v1/tasks/evaluation", json={"limit": 3})
            assert evaluation.status_code == 202, evaluation.text
            task_id = evaluation.json()["id"]
            assert evaluation.json()["task_type"] == "batch_evaluation"

            status_response = client.get(f"/api/v1/tasks/{task_id}")
            assert status_response.status_code == 200
            assert status_response.json()["status"] == "finished"

            with client.stream("GET", f"/api/v1/tasks/{task_id}/events") as stream:
                body = "".join(stream.iter_text())
            assert "event: completed" in body
            assert '"progress": 100' in body
    finally:
        app.dependency_overrides.pop(get_task_queue_service, None)


def test_background_tasks_are_explicitly_disabled_without_redis_config():
    with TestClient(app) as client:
        response = client.post("/api/v1/tasks/evaluation", json={"limit": 1})
    assert response.status_code == 503
    assert "后台任务未启用" in response.json()["detail"]
