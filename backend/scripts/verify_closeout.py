"""Real HTTP/SSE acceptance against the isolated Compose stack; money stays simulated."""

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from dotenv import dotenv_values
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:8002/api/v1")
    parser.add_argument("--jaeger-url", default="http://127.0.0.1:16687")
    parser.add_argument("--restart-checkpoint", action="store_true")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parents[2] / "output" / "closeout")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = {"started_at": datetime.now(timezone.utc).isoformat(), "checks": {}, "status": "running"}
    checks = summary["checks"]
    cookies = CookieJar()
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    csrf = ""

    def request(method, path, payload=None, *, expected=200):
        data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        req = urllib.request.Request(args.api_url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", **({"X-CSRF-Token": csrf} if csrf else {})})
        try:
            with client.open(req, timeout=120) as response:
                status_code, result = response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            status_code, result = error.code, json.loads(error.read())
        if status_code != expected:
            raise AssertionError(f"{method} {path}: HTTP {status_code}, expected {expected}; detail={result.get('detail')}")
        return result

    def wait_ready():
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                health = request("GET", "/health")
                if health["database_ready"] and health["checks"]["queue"].startswith("ready:"):
                    return health
            except Exception:
                pass
            time.sleep(2)
        raise RuntimeError("Acceptance stack not ready")

    def analyze(ticket_id):
        task = request("POST", f"/tasks/agent-analysis/{ticket_id}", expected=202)
        progress = []
        with client.open(args.api_url + f"/tasks/{task['id']}/events", timeout=180) as stream:
            for raw in stream:
                line = raw.decode().strip()
                if not line.startswith("data: "):
                    continue
                snapshot = json.loads(line[6:])
                progress.append(snapshot["progress"])
                if snapshot["status"] == "failed":
                    raise RuntimeError(snapshot.get("error") or "Agent task failed")
                if snapshot["status"] == "finished":
                    break
        assert progress and progress[-1] == 100
        detail = request("GET", f"/tickets/{ticket_id}")
        for call in detail["assessment"]["model_trace"]:
            if not call.get("skipped"):
                assert call["provider"] == "deepseek" and not call.get("fallback")
        return detail, progress

    try:
        auth_config = dotenv_values(Path(__file__).parents[2] / ".env.auth", interpolate=False)
        account = next(item for item in json.loads(auth_config["AUTH_BOOTSTRAP_USERS"]) if item["role"] == "admin")
        logged = request("POST", "/auth/login", {"username": account["username"], "password": account["password"]})
        csrf = logged["csrf_token"]
        checks["authenticated_admin"] = True
        health = wait_ready()
        assert health["database"] == "PostgreSQL + pgvector"
        assert health["checks"]["embedding"].startswith("fastembed:")
        assert health["agent_mode"] == "deepseek"
        checks["stack"] = {"database": health["database"], "queue": health["checks"]["queue"], "embedding": health["checks"]["embedding"]}

        created = request("POST", "/customer/tickets", {
            "customer_name": "收尾验收客户", "subject": "退款与售后",
            "message": "商品有质量问题，想申请退款，但还没找到订单编号。",
        }, expected=201)
        ticket_id = created["id"]
        summary["ticket_id"] = ticket_id
        summary["ticket_no"] = created["ticket_no"]
        first, first_progress = analyze(ticket_id)
        assert first["status"] == "in_progress"
        assert first["assessment"]["customer_context"]["tool_source"] == "not_called"
        assert "ORD-10024" not in first["draft_response"]
        request("POST", f"/tickets/{ticket_id}/draft/send", {"response": first["draft_response"]})
        request("POST", f"/customer/tickets/{ticket_id}/confirmation", {"resolved": False, "feedback": "我会补充订单编号"})
        checks["missing_order_clarification"] = True

        request("POST", f"/customer/tickets/{ticket_id}/messages", {"message": "订单号是ORD-CLOSE-222，我申请退款180元。"})
        updated = request("GET", f"/tickets/{ticket_id}")
        assert updated["order_id"] == "ORD-CLOSE-222" and updated["amount"] == 180
        pending, second_progress = analyze(ticket_id)
        assert pending["status"] == "pending_approval"
        assert pending["assessment"]["customer_context"]["tool_source"] == "mcp-server"
        assert not pending["assessment"]["customer_context"].get("tool_fallback")
        checks["real_mcp_and_multi_turn"] = True
        checks["sse_progress"] = [first_progress, second_progress]

        key = "closeout:" + uuid4().hex
        arguments = {"order_id": "ORD-CLOSE-222", "amount": 180, "currency": "CNY", "reason": "验收模拟退款"}
        request("POST", f"/tickets/{ticket_id}/tools/execute_refund/execute",
                {"idempotency_key": key, "arguments": arguments}, expected=403)
        checks["unapproved_write_rejected"] = True
        checkpoint_before = request("GET", f"/tickets/{ticket_id}/checkpoint")
        audit_before = len(pending["audit_events"])
        if args.restart_checkpoint:
            workspace = Path(__file__).parents[2]
            subprocess.run([args.docker, "compose", "-p", "resolveai-acceptance",
                            "-f", str(workspace / "docker-compose.yml"),
                            "-f", str(workspace / "docker-compose.acceptance.yml"),
                            "restart", "backend", "worker"], cwd=workspace, check=True)
            wait_ready()
        approved = request("POST", f"/tickets/{ticket_id}/decision",
                           {"decision": "approve", "reviewer": "隔离验收审核员", "note": "批准本次模拟资金动作"})
        checkpoint_after = request("GET", f"/tickets/{ticket_id}/checkpoint")
        assert checkpoint_before["thread_id"] == checkpoint_after["thread_id"]
        assert approved["approval"]["state"] == "approved"
        assert len(approved["audit_events"]) - audit_before <= 3
        checks["checkpoint_restart_resume"] = bool(args.restart_checkpoint)

        executed = request("POST", f"/tickets/{ticket_id}/tools/execute_refund/execute",
                           {"idempotency_key": key, "arguments": arguments})
        assert executed["result"]["success"] and executed["result"]["mode"] == "simulated"
        assert executed["result"]["tool_source"] == "mcp-server"
        duplicate = request("POST", f"/tickets/{ticket_id}/tools/execute_refund/execute",
                            {"idempotency_key": key, "arguments": arguments})
        assert duplicate["replayed"] and duplicate["id"] == executed["id"]
        checks["mcp_simulated_refund_and_idempotency"] = True

        rewritten = request("POST", f"/tickets/{ticket_id}/draft/rewrite", {"instruction": "明确说明这是项目模拟退款，不承诺真实到账；用简洁中文说明本次进展。"})
        assert rewritten["draft_response"] != approved["draft_response"]
        assert "模拟" in rewritten["draft_response"] or "演示" in rewritten["draft_response"]
        assert rewritten["assessment"]["model_trace"][-1]["fallback"] is False
        sent = request("POST", f"/tickets/{ticket_id}/draft/send", {"response": rewritten["draft_response"]})
        assert sent["status"] == "awaiting_customer"
        customer = request("GET", f"/customer/tickets/{ticket_id}")
        assert customer["stage"] == "answered" and customer["customer_confirmed"] is None
        checks["rewrite_send_without_auto_resolve"] = True

        request("POST", f"/customer/tickets/{ticket_id}/messages", {"message": "请说明退款到账时间，我只是查询进度，不申请新的退款。"})
        followed, follow_progress = analyze(ticket_id)
        assert followed["status"] == "in_progress"
        assert followed["assessment"]["refund_amount"] is None
        request("POST", f"/tickets/{ticket_id}/draft/send", {"response": followed["draft_response"]})
        confirmed = request("POST", f"/customer/tickets/{ticket_id}/confirmation",
                            {"resolved": True, "rating": 5, "feedback": "本次模拟流程已确认"})
        assert confirmed["stage"] == "resolved"
        assert len(confirmed["messages"]) >= 6
        checks["continued_conversation_and_customer_confirmation"] = True
        checks["sse_progress"].append(follow_progress)

        trace_id = followed["assessment"]["observability"]["trace_id"]
        trace_data = None
        for _ in range(20):
            try:
                with urllib.request.urlopen(args.jaeger_url + "/api/traces/" + trace_id, timeout=10) as response:
                    trace_data = json.loads(response.read())
                if trace_data.get("data"):
                    current_names = {s["operationName"] for s in trace_data["data"][0]["spans"]}
                    if len({n for n in current_names if n.startswith("resolveai.agent.")}) == 7:
                        break
            except Exception:
                pass
            time.sleep(1)
        assert trace_data and trace_data.get("data")
        spans = trace_data["data"][0]["spans"]
        names = {s["operationName"] for s in spans}
        assert len({n for n in names if n.startswith("resolveai.agent.")}) == 7
        assert "resolveai.rag.vector_search" in names and "resolveai.tool.get_order_context" in names
        checks["jaeger_seven_nodes"] = {"trace_id": trace_id, "spans": len(spans)}
        (args.output_dir / "jaeger-trace.json").write_text(json.dumps(trace_data, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        summary["status"] = "passed"
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except Exception as error:
        summary["status"] = "failed"
        summary["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        (args.output_dir / "fullstack-acceptance.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
        )


if __name__ == "__main__":
    main()
