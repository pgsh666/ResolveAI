import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import KnowledgeDocument
from app.services.embeddings import EmbeddingService
from app.services.seed import RISK_POLICY_CONTENT, seed_database
from app.services.shop_assistant import handoff_reasons


@pytest.mark.parametrize("turns", [6, 10, 100, 500])
def test_turn_count_alone_never_requires_handoff(turns):
    conversation = [{"role":"customer", "content":"看看商品价格"} for _ in range(turns)]
    assert handoff_reasons({"message":"蓝牙耳机有什么推荐"}, conversation, {}) == []


@pytest.mark.parametrize("message,context,expected", [
    ("我要退款", {}, "退款或资金操作需人工处理"),
    ("请转人工客服", {}, "用户要求转人工"),
    ("请查询物流状态", {"overdue":True}, "运输天数超过预计送达天数"),
    ("请给我发放10元优惠券", {}, "发券等优惠写操作需人工处理"),
])
def test_other_handoff_boundaries_remain_after_many_turns(message, context, expected):
    conversation = [{"role":"customer", "content":"看看商品"} for _ in range(30)]
    assert expected in handoff_reasons({"message":message}, conversation, context)


@pytest.mark.parametrize("original,upgraded", [
    ("单笔退款金额不高于100元且无风险标记可自动执行。超过100元、命中近期退款风险或订单信息不一致时，必须进入人工审批并记录审批人。", True),
    ("退款、退货及其他资金写操作均需转人工处理，不设自动退款金额豁免。物流运输天数超过订单预计送达天数、同一会话超过五轮仍未解决或用户明确要求人工时，转交人工客服并保留上下文与路由记录。", True),
    ("商户自己维护的风控规范，不应被启动过程覆盖。", False),
])
def test_only_known_seed_policy_versions_upgrade_and_keep_enablement(tmp_path, monkeypatch, original, upgraded):
    from app.services import retrieval
    invalidations = []
    monkeypatch.setattr(retrieval, "invalidate_retrieval_cache", lambda: invalidations.append(True))
    engine = create_engine(f"sqlite:///{(tmp_path/'seed-policy.db').as_posix()}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            document = KnowledgeDocument(title="退款审批与风控规范", source="policy://risk/refund-approval",
                category="refund", content=original, enabled=False,
                embedding=EmbeddingService().legacy_embed(original))
            session.add(document)
            session.commit()
            identifier = document.id
            seed_database(session)
            seed_database(session)
            stored = session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.source == document.source))
            assert stored.id == identifier and stored.enabled is False
            assert stored.content == (RISK_POLICY_CONTENT if upgraded else original)
            assert len(invalidations) == int(upgraded)
            assert "超过五轮" not in RISK_POLICY_CONTENT and "不因对话轮数" in RISK_POLICY_CONTENT
    finally:
        engine.dispose()
