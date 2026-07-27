"""AIReview — the structured output contract for the self-hosted model, plus the
validation gate every model response must pass before it is displayed or stored.

Gate policy (docs/architecture.md):
- response must parse as JSON matching this schema (one repair attempt upstream);
- `confirmed_findings` may only restate deterministic rule findings (matched by rule_id);
  anything else is demoted to `possible_findings`;
- cited components/nets that do not exist in the project are dropped; items whose citations
  all vanish are dropped entirely (hallucination gate);
- the gate returns warnings describing everything it changed, for logging and evaluation.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, RuleFinding, Severity


class ReviewItem(BaseModel):
    title: str
    detail: str = ""
    severity: Optional[Severity] = None
    components: list[str] = Field(default_factory=list)
    nets: list[str] = Field(default_factory=list)
    rule_id: Optional[str] = None
    evidence: list[str] = Field(default_factory=list)


class AIReview(BaseModel):
    summary: str = ""
    confirmed_findings: list[ReviewItem] = Field(default_factory=list)
    possible_findings: list[ReviewItem] = Field(default_factory=list)
    recommendations: list[ReviewItem] = Field(default_factory=list)
    questions_for_engineer: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = 0.0
    limitations: list[str] = Field(default_factory=list)


AI_REVIEW_JSON_SCHEMA = AIReview.model_json_schema()


def validate_ai_review(
    review: AIReview,
    project: NormalizedProject,
    rule_findings: list[RuleFinding],
) -> tuple[AIReview, list[str]]:
    """Enforce the citation/hallucination gate. Returns (cleaned review, warnings)."""
    warnings: list[str] = []
    valid_refs = project.component_references()
    valid_nets = project.net_names()
    valid_rule_ids = {f.rule_id for f in rule_findings}

    def clean_item(item: ReviewItem, where: str) -> Optional[ReviewItem]:
        kept_refs = [r for r in item.components if r in valid_refs]
        kept_nets = [n for n in item.nets if n in valid_nets]
        dropped = (set(item.components) - set(kept_refs)) | (set(item.nets) - set(kept_nets))
        if dropped:
            warnings.append(
                f"{where} '{item.title}': dropped citations to nonexistent entities: {sorted(dropped)}"
            )
        had_citations = bool(item.components or item.nets)
        if had_citations and not kept_refs and not kept_nets:
            warnings.append(f"{where} '{item.title}': removed — no valid citations remain")
            return None
        return item.model_copy(update={"components": kept_refs, "nets": kept_nets})

    confirmed: list[ReviewItem] = []
    possible: list[ReviewItem] = []
    for item in review.confirmed_findings:
        cleaned = clean_item(item, "confirmed_finding")
        if cleaned is None:
            continue
        if cleaned.rule_id in valid_rule_ids:
            confirmed.append(cleaned)
        else:
            warnings.append(
                f"confirmed_finding '{item.title}' has no matching rule finding — demoted to possible"
            )
            possible.append(cleaned.model_copy(update={"rule_id": None}))

    for item in review.possible_findings:
        cleaned = clean_item(item, "possible_finding")
        if cleaned is not None:
            possible.append(cleaned)

    recommendations = [
        c for item in review.recommendations if (c := clean_item(item, "recommendation")) is not None
    ]

    confidence = min(max(review.confidence, 0.0), 1.0)

    cleaned_review = review.model_copy(
        update={
            "confirmed_findings": confirmed,
            "possible_findings": possible,
            "recommendations": recommendations,
            "confidence": confidence,
        }
    )
    return cleaned_review, warnings
