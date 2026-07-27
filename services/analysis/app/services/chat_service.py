"""Design-chat orchestration: retrieve relevant slices → prompt the self-hosted model →
validate → fall back to a deterministic, clearly-labeled answer. Persists conversation with
a mandatory Evidence payload."""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import db as dbm
from app.models.ai_review import AI_REVIEW_JSON_SCHEMA, AIReview, validate_ai_review
from app.models.normalized import NormalizedProject
from app.models.schemas import ChatEvidence, ChatResponse
from app.rules.base import Evidence, RuleCategory, RuleFinding, Severity
from app.services.inference_client import InferenceClient, InferenceUnavailable
from app.services.prompts import build_chat_prompt

logger = logging.getLogger(__name__)


def _load_findings(session: Session, project_id: str) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for row in session.query(dbm.RuleFinding).filter(dbm.RuleFinding.project_id == project_id).all():
        findings.append(RuleFinding(
            rule_id=row.rule_id, rule_version=row.rule_version, category=RuleCategory(row.category),
            severity=Severity(row.severity), title=row.title, description=row.description,
            affected_components=row.affected_components or [], affected_nets=row.affected_nets or [],
            evidence=[Evidence(**e) for e in (row.evidence or [])],
            suggested_fix=row.suggested_fix, confidence=row.confidence, source=row.source,
        ))
    return findings


def _retrieve(project: NormalizedProject, findings: list[RuleFinding], question: str):
    """Try the full local retriever; fall back to simple structured matching."""
    try:
        from app.retrieval.retriever import ProjectRetriever

        retriever = ProjectRetriever()
        retriever.build(project, findings)
        ctx = retriever.retrieve(question)
        return ctx.model_dump(), ctx.method_notes
    except Exception as exc:  # noqa: BLE001
        logger.warning("retriever unavailable, using structured fallback: %s", exc)
        import re

        def present(name: str) -> bool:
            return re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", question, re.I) is not None

        comps = [c.reference for c in project.components if present(c.reference)]
        nets = [n.name for n in project.nets if present(n.name)]
        related = [f for f in findings if set(f.affected_components) & set(comps)
                   or set(f.affected_nets) & set(nets)] or findings
        ctx = {
            "components": comps, "nets": nets,
            "findings": [f.model_dump() for f in related],
            "snippets": [], "method_notes": ["retriever=structured-fallback"],
        }
        return ctx, ctx["method_notes"]


def ask(session: Session, project_id: str, question: str, conversation_id: Optional[str] = None) -> ChatResponse:
    settings = get_settings()
    project_row = session.get(dbm.Project, project_id)
    if project_row is None or not project_row.normalized:
        raise ValueError("project not analyzed yet")
    normalized = NormalizedProject.model_validate(project_row.normalized)
    findings = _load_findings(session, project_id)

    retrieved, notes = _retrieve(normalized, findings, question)

    provider = "stub"
    model_version = "rule-engine-fallback"
    ai_review_dict = None
    system, user = build_chat_prompt(question, retrieved, AI_REVIEW_JSON_SCHEMA, normalized.project.name)
    client = InferenceClient(settings.inference_url, timeout=settings.inference_timeout_seconds)
    try:
        result = client.generate(system, user, json_schema=AI_REVIEW_JSON_SCHEMA, max_tokens=600)
        review = AIReview.model_validate(result.json or {})
        review, _warnings = validate_ai_review(review, normalized, findings)
        answer = review.summary or "(no answer)"
        provider = result.provider or "self_hosted"
        model_version = result.model_version or settings.model_version
        ai_review_dict = review.model_dump()
    except (InferenceUnavailable, Exception) as exc:  # noqa: BLE001
        logger.warning("chat falling back to deterministic answer: %s", exc)
        answer = _fallback_answer(question, retrieved, findings)

    evidence = ChatEvidence(
        components=retrieved.get("components", []),
        nets=retrieved.get("nets", []),
        rules=[f["rule_id"] for f in retrieved.get("findings", [])],
        source_files=normalized.project.files,
        snippets=retrieved.get("snippets", []),
        model_version=model_version,
        provider=provider,
    )

    # persist
    if conversation_id:
        conv = session.get(dbm.ChatConversation, conversation_id)
        if conv is None or conv.project_id != project_id:
            conv = None
    else:
        conv = None
    if conv is None:
        conv = dbm.ChatConversation(project_id=project_id, title=question[:80])
        session.add(conv)
        session.flush()
    session.add(dbm.ChatMessage(conversation_id=conv.id, role="user", content=question))
    session.add(dbm.ChatMessage(
        conversation_id=conv.id, role="assistant", content=answer,
        evidence=evidence.model_dump(), model_version=model_version, provider=provider,
    ))
    session.commit()

    return ChatResponse(conversation_id=conv.id, answer=answer, evidence=evidence, ai_review=ai_review_dict)


def _fallback_answer(question: str, retrieved: dict, findings: list[RuleFinding]) -> str:
    comps = retrieved.get("components", [])
    nets = retrieved.get("nets", [])
    related = retrieved.get("findings", [])
    lines = [
        "[Deterministic fallback — the specialized model is unavailable, so this answer is "
        "assembled from the rule engine and project structure only.]",
        "",
    ]
    if comps:
        lines.append(f"Components matching your question: {', '.join(comps)}.")
    if nets:
        lines.append(f"Related nets: {', '.join(nets)}.")
    if related:
        lines.append("Relevant rule findings:")
        for f in related[:6]:
            lines.append(f"  • [{f['severity']}] {f['rule_id']} {f['title']} — {f['description']}")
    else:
        lines.append("No rule findings are directly associated with the entities in your question.")
    lines.append("")
    lines.append("For interpretation and prioritization, run with the model service enabled.")
    return "\n".join(lines)
