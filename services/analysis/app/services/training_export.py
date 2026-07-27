"""Export analyzed projects (+ consented feedback) into the JSONL training format.

Consent-gated: feedback only flows in when consent_to_train is set. Writes a versioned
dataset directory and records a DatasetVersion row."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import db as dbm

DATASETS_DIR = Path(__file__).resolve().parents[4] / "datasets" / "processed"


def _example_from_project(project: dbm.Project, findings: list[dbm.RuleFinding], review) -> dict:
    normalized = project.normalized or {}
    return {
        "instruction": "Review this circuit for design issues.",
        "input": {
            "project": normalized.get("project", {}),
            "components": normalized.get("components", []),
            "nets": normalized.get("nets", []),
            "rule_findings": [
                {"rule_id": f.rule_id, "severity": f.severity, "title": f.title,
                 "affected_components": f.affected_components, "affected_nets": f.affected_nets}
                for f in findings
            ],
            "power_tree": project.power_tree,
            "question": None,
        },
        "output": (review.payload if review else {}),
        "metadata": {
            "source": "analyzed_project", "license": "internal", "reviewed": False,
            "safety_class": "general", "project_id": project.id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def export_training_data(session: Session, only_consented: bool = True,
                         include_feedback: bool = True) -> dict:
    version = f"export-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    out_dir = DATASETS_DIR / version
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    source_mix: dict[str, int] = {}

    for project in session.query(dbm.Project).filter(dbm.Project.status == "completed").all():
        findings = session.query(dbm.RuleFinding).filter(dbm.RuleFinding.project_id == project.id).all()
        review = (
            session.query(dbm.AIReview).filter(dbm.AIReview.project_id == project.id)
            .order_by(dbm.AIReview.created_at.desc()).first()
        )
        rows.append(_example_from_project(project, findings, review))
        source_mix["analyzed_project"] = source_mix.get("analyzed_project", 0) + 1

    if include_feedback:
        fb_query = session.query(dbm.UserFeedback)
        if only_consented:
            fb_query = fb_query.filter(dbm.UserFeedback.consent_to_train.is_(True))
        for fb in fb_query.all():
            finding = session.get(dbm.RuleFinding, fb.finding_id) if fb.finding_id else None
            if finding is None:
                continue
            rows.append({
                "instruction": "Incorporate this engineer correction into the review.",
                "input": {"rule_finding": {"rule_id": finding.rule_id, "title": finding.title},
                          "rating": fb.rating},
                "output": {"corrected_explanation": fb.edited_explanation,
                           "corrected_fix": fb.edited_fix, "false_positive": fb.false_positive,
                           "missed_issue": fb.missed_issue},
                "metadata": {"source": "user_feedback", "license": "user-consented",
                             "reviewed": False, "safety_class": "general",
                             "consent": fb.consent_to_train},
            })
            source_mix["user_feedback"] = source_mix.get("user_feedback", 0) + 1

    jsonl_path = out_dir / "dataset.jsonl"
    payload = "\n".join(json.dumps(r, default=str) for r in rows)
    jsonl_path.write_text(payload + ("\n" if payload else ""))
    content_hash = hashlib.sha256(payload.encode()).hexdigest()

    manifest = {"version": version, "row_count": len(rows), "source_mix": source_mix,
                "content_hash": content_hash, "only_consented": only_consented,
                "created_at": datetime.now(timezone.utc).isoformat()}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    session.add(dbm.DatasetVersion(
        version=version, content_hash=content_hash, row_count=len(rows),
        source_mix=source_mix, storage_path=str(out_dir),
    ))
    session.commit()
    return {"version": version, "row_count": len(rows), "path": str(out_dir),
            "content_hash": content_hash, "source_mix": source_mix,
            "note": "reviewed=false on all rows; a human must review before training use "
                    "(see docs/training-data-strategy.md)."}
