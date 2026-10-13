"""Black-box OpenTelemetry verification against a running Compose stack."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


API_URL = os.getenv("VERIFY_API_URL", "http://127.0.0.1:8000/api/v1").rstrip("/")
JAEGER_URL = os.getenv("VERIFY_JAEGER_URL", "http://127.0.0.1:16686").rstrip("/")
CLIENT = None
CSRF = ""


def request_json(method: str, url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **({"X-CSRF-Token": CSRF} if url.startswith(API_URL) else {})},
    )
    opener = CLIENT.open if url.startswith(API_URL) else urllib.request.urlopen
    with opener(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    from scripts.auth_client import acceptance_client
    global CLIENT, CSRF
    CLIENT, CSRF = acceptance_client(API_URL)
    stamp = datetime.now(timezone.utc).strftime("%H%M%S")
    customer_message = f"链路验收消息-{stamp}：订单超过承诺时间仍未送到"
    ticket = request_json(
        "POST",
        f"{API_URL}/tickets",
        {
            "customer_name": f"链路验收用户-{stamp}",
            "customer_tier": "standard",
            "channel": "web",
            "subject": "物流延迟链路自动验收",
            "message": customer_message,
            "priority": "medium",
            "order_id": f"ORD-TRACE-{stamp}",
            "amount": 89,
        },
    )
    detail = request_json("POST", f"{API_URL}/tickets/{ticket['id']}/analyze")
    trace_id = detail["assessment"]["observability"]["trace_id"]
    if not trace_id:
        raise RuntimeError("Agent response did not contain an OpenTelemetry trace_id")

    trace_payload = None
    for _ in range(15):
        try:
            trace_payload = request_json("GET", f"{JAEGER_URL}/api/traces/{trace_id}")
            if trace_payload.get("data"):
                break
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        time.sleep(1)
    if not trace_payload or not trace_payload.get("data"):
        raise RuntimeError(f"Jaeger did not receive trace {trace_id}")

    spans = trace_payload["data"][0]["spans"]
    names = {span["operationName"] for span in spans}
    expected_agents = {
        "resolveai.agent.triage_agent",
        "resolveai.agent.knowledge_agent",
        "resolveai.agent.customer_agent",
        "resolveai.agent.policy_agent",
        "resolveai.agent.resolution_agent",
        "resolveai.agent.quality_agent",
        "resolveai.agent.approval_gate",
    }
    required = expected_agents | {
        "resolveai.workflow.analyze",
        "resolveai.llm.triage",
        "resolveai.llm.resolution",
        "resolveai.rag.vector_search",
        "resolveai.tool.get_order_context",
    }
    missing = sorted(required - names)
    if missing:
        raise RuntimeError(f"Trace is missing required spans: {missing}")

    serialized = json.dumps(trace_payload, ensure_ascii=False)
    leaked = [
        value
        for value in (customer_message, "approval_token", "api_key", "sk-secret")
        if value in serialized
    ]
    if leaked:
        raise RuntimeError(f"Sensitive telemetry content detected: {leaked}")

    print(
        json.dumps(
            {
                "status": "passed",
                "ticket_no": detail["ticket_no"],
                "trace_id": trace_id,
                "trace_url": detail["assessment"]["observability"]["trace_url"],
                "span_count": len(spans),
                "agent_node_count": len(expected_agents.intersection(names)),
                "privacy_scan": "passed",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
