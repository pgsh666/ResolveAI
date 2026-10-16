from fastapi.testclient import TestClient

from app.main import app


def test_health_and_shared_knowledge():
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["agent_mode"] == "demo"
        assert health.json()["database_ready"] is True
        assert health.json()["checks"]["database"] == "ok"

        root_health = client.get("/health")
        assert root_health.status_code == 200
        assert root_health.json()["database_ready"] is True

        tickets = client.get("/api/v1/tickets")
        assert tickets.status_code == 200
        assert isinstance(tickets.json(), list)

        documents = client.get("/api/v1/knowledge/documents")
        assert documents.status_code == 200
        assert len(documents.json()) >= 20

        first_document = documents.json()[0]
        document_detail = client.get(f"/api/v1/knowledge/documents/{first_document['id']}")
        assert document_detail.status_code == 200
        assert document_detail.json()["content"] == first_document["content"]

        topology = client.get("/api/v1/agents/topology")
        assert topology.status_code == 200
        assert len(topology.json()["edges"]) >= 7


def test_agent_workflow_and_human_approval():
    with TestClient(app) as client:
        created = client.post("/api/v1/tickets", json={
            "customer_name": "自动化退款测试客户", "customer_tier": "VIP",
            "subject": "退款到账进度咨询", "message": "退款审核通过四天了，还没有到账，请协助核实。",
            "order_id": "ORD-FIXTURE-221", "amount": 329, "priority": "urgent"})
        assert created.status_code == 201, created.text
        refund_ticket = created.json()

        result = client.post(f"/api/v1/tickets/{refund_ticket['id']}/analyze")
        assert result.status_code == 200, result.text
        detail = result.json()
        assert detail["category"] == "refund"
        assert len(detail["citations"]) >= 1
        assert len(detail["audit_events"]) >= 7
        assert detail["approval"]["state"] == "pending"
        breakdown = detail["assessment"]["confidence_breakdown"]
        assert breakdown["method"] == "weighted_evidence"
        assert len(breakdown["components"]) == 5
        assert detail["confidence"] == breakdown["final_score"]

        rewritten = client.post(
            f"/api/v1/tickets/{refund_ticket['id']}/draft/rewrite",
            json={},
        )
        assert rewritten.status_code == 200, rewritten.text
        rewritten_detail = rewritten.json()
        assert rewritten_detail["draft_response"] != detail["draft_response"]
        assert rewritten_detail["audit_events"][-1]["agent"] == "回复优化 Agent"

        sent_text = rewritten_detail["draft_response"]
        sent = client.post(
            f"/api/v1/tickets/{refund_ticket['id']}/draft/send",
            json={"response": sent_text},
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["sent_response"] == sent_text
        assert sent.json()["draft_response"] is None
        assert sent.json()["audit_events"][-1]["action"] == "发送客户回复"

        persisted = client.get(f"/api/v1/tickets/{refund_ticket['id']}")
        assert persisted.json()["sent_response"] == sent_text

        decision = client.post(
            f"/api/v1/tickets/{refund_ticket['id']}/decision",
            json={"decision": "approve", "note": "测试审批", "reviewer": "PM"},
        )
        assert decision.status_code == 200, decision.text
        assert decision.json()["status"] == "awaiting_customer"
        assert decision.json()["approval"]["state"] == "approved"

        runs = client.get("/api/v1/agents/runs")
        assert runs.status_code == 200
        assert len(runs.json()) >= 8
        assert runs.json()[0]["ticket_no"] == refund_ticket["ticket_no"]

        normal_ticket = client.post(
            "/api/v1/tickets",
            json={
                "customer_name": "普通咨询用户",
                "subject": "商品使用咨询",
                "message": "请问这个商品应该如何使用？",
            },
        ).json()
        normal_analysis = client.post(f"/api/v1/tickets/{normal_ticket['id']}/analyze")
        assert normal_analysis.status_code == 200, normal_analysis.text
        assert normal_analysis.json()["status"] == "in_progress"


def test_customer_portal_flow():
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/customer/tickets",
            json={
                "customer_name": "客户端测试用户",
                "subject": "物流信息长时间未更新",
                "message": "订单已经三天没有新的物流记录，请帮我看看。",
                "order_id": "ORD-CUSTOMER-01",
            },
        )
        assert created.status_code == 201, created.text
        customer_ticket = created.json()
        ticket_id = customer_ticket["id"]
        assert customer_ticket["stage"] == "answered"
        assert "assessment" not in customer_ticket

        assert customer_ticket["messages"][-1]["role"] == "support"
        assert "请您提供订单编号" in customer_ticket["messages"][-1]["content"]

        sent = client.post(
            f"/api/v1/tickets/{ticket_id}/draft/send",
            json={"response": "您好，已收到您的反馈，当前需要进一步核查配送情况，我们会继续提供处理建议。"},
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["status"] == "awaiting_customer"

        refreshed = client.get(f"/api/v1/customer/tickets/{ticket_id}")
        assert refreshed.status_code == 200
        assert refreshed.json()["stage"] == "answered"
        assert refreshed.json()["sent_response"] == "您好，已收到您的反馈，当前需要进一步核查配送情况，我们会继续提供处理建议。"
        assert [item["role"] for item in refreshed.json()["messages"]] == ["customer", "support", "support"]

        needs_more_help = client.post(
            f"/api/v1/customer/tickets/{ticket_id}/confirmation",
            json={"resolved": False, "rating": 3, "feedback": "还需要更具体的物流时间。"},
        )
        assert needs_more_help.status_code == 200, needs_more_help.text
        assert needs_more_help.json()["stage"] == "follow_up"
        assert needs_more_help.json()["customer_confirmed"] is False

        follow_up = client.post(
            f"/api/v1/customer/tickets/{ticket_id}/messages",
            json={"message": "可以告诉我预计什么时候送到吗？"},
        )
        assert follow_up.status_code == 200, follow_up.text
        assert follow_up.json()["stage"] == "answered"
        assert follow_up.json()["messages"][-1]["role"] == "support"
        assert follow_up.json()["customer_confirmed"] is None

        second_reply = client.post(
            f"/api/v1/tickets/{ticket_id}/draft/send",
            json={"response": "承运商已确认，包裹预计明天下午送达。"},
        )
        assert second_reply.status_code == 200, second_reply.text
        assert second_reply.json()["status"] == "awaiting_customer"
        reopened = client.get(f"/api/v1/customer/tickets/{ticket_id}").json()
        assert reopened["stage"] == "answered"
        assert reopened["customer_confirmed"] is None
        assert [item["role"] for item in reopened["messages"]] == ["customer", "support", "support", "customer", "support", "support"]

        confirmed = client.post(
            f"/api/v1/customer/tickets/{ticket_id}/confirmation",
            json={"resolved": True, "rating": 5, "feedback": "回复及时，问题已解决。"},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["stage"] == "resolved"
        assert confirmed.json()["customer_confirmed"] is True
        assert confirmed.json()["customer_rating"] == 5

        internal = client.get(f"/api/v1/tickets/{ticket_id}").json()
        assert internal["status"] == "resolved"
        assert internal["audit_events"][-1]["agent"] == "客户"
        assert len(internal["conversation"]) == 6
