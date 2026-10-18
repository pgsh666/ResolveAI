"""One canonical manual-FAQ matcher for every business category."""

import re
import unicodedata

from sqlalchemy import select

from app.models import KnowledgeDocument
from app.services.auth import knowledge_scope
from app.services.business_questions import allows_manual_faq, is_guidance_question
from app.services.security import SecurityService, redact_text


def question_key(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", text)
    text = re.sub(r"^(?:(?:您好|你好|请问|麻烦问一下|我想知道|我想了解|想了解|我该|我应该|请教一下))+", "", text)
    text = re.sub(r"[吗呢呀啊]+$", "", text)
    text = text.replace("我的", "").replace("本人的", "")
    for old, new in [("订单状态", "订单进度"), ("登录密码", "密码"), ("更改", "修改"),
                     ("变更", "修改"), ("查询", "查看"), ("查阅", "查看")]:
        text = text.replace(old, new)
    if is_guidance_question(text):
        text = re.sub(r"怎么|如何|怎样|咋|在哪里|在哪|哪里|哪儿|一下|我该|的", "", text)
        text = re.sub(r"(?<!查)看", "查看", text)
        # Reordered instruction questions retain all topic/action/negation characters.
        return "guidance:" + "".join(sorted(text))
    return text


def canonical_manual_faq(session, workspace_id, document_id, *, score=1.0):
    document = session.scalar(select(KnowledgeDocument).where(
        KnowledgeDocument.id == document_id, KnowledgeDocument.enabled.is_(True),
        KnowledgeDocument.source.like("faq://manual/%"), knowledge_scope(workspace_id)))
    if not document or not document.content.strip():
        return None
    if SecurityService().assess(subject=document.title, message=document.content).blocked:
        return None
    return {"id": document.id, "title": redact_text(document.title), "source": document.source,
            "snippet": redact_text(document.content.strip()), "score": score}


def refresh_manual_faq_citations(session, workspace_id, citations):
    """Cached retrieval text is not the authority for manual FAQs."""
    refreshed = []
    for citation in citations:
        document = session.get(KnowledgeDocument, citation.get("id"))
        manual = citation.get("source", "").startswith("faq://manual/") or bool(
            document and document.source.startswith("faq://manual/"))
        if manual:
            canonical = canonical_manual_faq(session, workspace_id, citation.get("id"), score=citation.get("score", 0))
            if canonical:
                refreshed.append(canonical)
        else:
            refreshed.append(citation)
    return refreshed


def match_manual_faq(session, workspace_id, question, citations, rag_status):
    if not allows_manual_faq(question):
        return None
    key = question_key(question)
    documents = session.scalars(select(KnowledgeDocument).where(
        KnowledgeDocument.enabled.is_(True), KnowledgeDocument.source.like("faq://manual/%"),
        knowledge_scope(workspace_id))).all()
    exact = [document for document in documents if question_key(document.title) == key]
    if len(exact) == 1:
        citation = canonical_manual_faq(session, workspace_id, exact[0].id)
        if citation:
            method = "configured_faq_title" if question.strip() == exact[0].title.strip() else "configured_faq_normalized"
            return {"citation": citation, "match_method": method}
    if exact:
        return None  # Ambiguous/unsafe titles do not fall through to an arbitrary cached hit.
    if not rag_status.get("reliable"):
        return None
    by_id = {document.id: document for document in documents}
    candidates = []
    for item in citations:
        if item.get("score", 0) < rag_status.get("threshold", 1):
            continue
        document = by_id.get(item.get("id"))
        if document is None:
            continue
        # A payment PIN and a login password are not interchangeable instructions.
        if ("支付密码" in question) != ("支付密码" in document.title):
            continue
        if bool(re.search(r"不|未|没", question)) != bool(re.search(r"不|未|没", document.title)):
            continue
        left, right = set(key.removeprefix("guidance:")), set(question_key(document.title).removeprefix("guidance:"))
        overlap = 2 * len(left & right) / max(1, len(left) + len(right))
        if len(left & right) >= 3 and overlap >= .6:
            candidates.append((item, overlap))
    candidates.sort(key=lambda pair: pair[0].get("score", 0), reverse=True)
    if not candidates or (len(candidates) > 1 and candidates[0][0]["score"] - candidates[1][0]["score"] < .05):
        return None
    citation = canonical_manual_faq(session, workspace_id, candidates[0][0]["id"], score=candidates[0][0]["score"])
    return {"citation": citation, "match_method": "manual_faq_semantic"} if citation else None
