"""Analysis orchestration: read stored files → normalize → rules → power tree → AI review
(self-hosted model, else deterministic stub) → persist everything with versions."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import db as dbm
from app.models.ai_review import AI_REVIEW_JSON_SCHEMA, AIReview, validate_ai_review
from app.parsers.normalizer import normalize_project
from app.parsers.zip_safe import safe_extract_zip
from app.rules.base import RuleFinding
from app.rules.registry import engine_version, run_rules
from app.services.inference_client import InferenceClient, InferenceUnavailable
from app.services.prompts import build_review_prompt
from app.services.storage import get_storage
from app.services.stub_review import build_stub_review

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc)


def _collect_files(session: Session, project: dbm.Project, storage) -> list[tuple[str, bytes]]:
    import tempfile
    from pathlib import Path

    files: list[tuple[str, bytes]] = []
    for uf in project.files:
        try:
            data = storage.read(uf.storage_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not read %s: %s", uf.filename, exc)
            continue
        if uf.filename.lower().endswith(".zip"):
            with tempfile.TemporaryDirectory() as tmp:
                try:
                    paths, warnings = safe_extract_zip(data, Path(tmp))
                    for p in paths:
                        files.append((p.name, p.read_bytes()))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("zip extraction failed for %s: %s", uf.filename, exc)
        else:
            files.append((uf.filename, data))
    return files


def _component_specs(session: Session, project_id: str) -> dict:
    specs: dict[str, dict] = {}
    rows = (
        session.query(dbm.ComponentSpecification)
        .join(dbm.Component, dbm.ComponentSpecification.component_id == dbm.Component.id)
        .filter(dbm.Component.project_id == project_id)
        .all()
    )
    comp_by_id = {c.id: c for c in project_components(session, project_id)}
    for spec in rows:
        comp = comp_by_id.get(spec.component_id)
        if comp is None:
            continue
        specs[comp.reference] = {
            "max_voltage_v": spec.max_voltage_v,
            "voltage_rating_v": spec.voltage_rating_v,
            "max_current_a": spec.max_current_a,
            "provenance": spec.verification_status,
        }
    return specs


def project_components(session: Session, project_id: str):
    return session.query(dbm.Component).filter(dbm.Component.project_id == project_id).all()


def run_analysis(project_id: str, job_id: str | None = None) -> None:
    """Entry point for the job queue. Owns its own DB session and updates job/project rows."""
    settings = get_settings()
    SessionLocal = dbm.get_sessionmaker()
    session = SessionLocal()
    job = None
    try:
        project = session.get(dbm.Project, project_id)
        if project is None:
            logger.error("analysis: project %s not found", project_id)
            return
        job = session.get(dbm.AnalysisJob, job_id) if job_id else None
        if job is None:
            job = dbm.AnalysisJob(
                project_id=project_id, rule_engine_version=engine_version(),
                model_version=settings.model_version,
            )
            session.add(job)
        if job.project_id != project_id:
            raise ValueError("analysis job does not belong to project")
        job.status = "running"
        job.error = ""
        job.started_at = _now()
        job.finished_at = None
        project.status = "analyzing"
        session.commit()

        storage = get_storage(settings)
        files = _collect_files(session, project, storage)

        normalized = normalize_project(files, project_name=project.name)

        # rule config: component specs + current overrides
        config = {
            "component_specs": _component_specs(session, project_id),
            "current_estimates_ma": project.current_overrides or {},
        }
        findings = run_rules(normalized, config)

        # power tree (lazy import; degrade gracefully)
        power_tree_dict = None
        try:
            from app.calculations.power_tree import build_power_tree

            power_tree_dict = build_power_tree(normalized, project.current_overrides or {}).model_dump()
        except Exception:  # noqa: BLE001
            logger.exception("power tree build failed for %s", project_id)

        # AI review via self-hosted model, else deterministic stub
        review, provider, model_version, warnings, usage = _run_ai_review(normalized, findings, settings)

        _persist(session, project, normalized, findings, power_tree_dict, review, provider,
                 model_version, warnings, usage)

        job.status = "completed"
        job.finished_at = _now()
        session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("analysis failed for %s", project_id)
        session.rollback()
        if job is not None:
            job.status = "failed"
            job.error = str(exc)[:2000]
            job.finished_at = _now()
        proj = session.get(dbm.Project, project_id)
        if proj is not None:
            proj.status = "failed"
        session.commit()
    finally:
        session.close()


def _run_ai_review(normalized, findings: list[RuleFinding], settings):
    project_json = normalized.model_dump()
    findings_json = [f.model_dump() for f in findings]
    client = InferenceClient(settings.inference_url, timeout=settings.inference_timeout_seconds)
    system, user = build_review_prompt(project_json, findings_json, AI_REVIEW_JSON_SCHEMA)
    try:
        result = client.generate(system, user, json_schema=AI_REVIEW_JSON_SCHEMA, max_tokens=800)
        raw = result.json or {}
        review = AIReview.model_validate(raw)
        cleaned, warnings = validate_ai_review(review, normalized, findings)
        usage = {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "latency_ms": result.latency_ms,
        }
        return cleaned, result.provider or "self_hosted", result.model_version or settings.model_version, warnings, usage
    except (InferenceUnavailable, Exception) as exc:  # noqa: BLE001
        logger.warning("AI review falling back to deterministic stub: %s", exc)
        review = build_stub_review(findings)
        cleaned, warnings = validate_ai_review(review, normalized, findings)
        return cleaned, "stub", "rule-engine-fallback", warnings, {}


def _persist(session, project, normalized, findings, power_tree_dict, review, provider,
             model_version, warnings, usage) -> None:
    # clear prior derived rows (idempotent re-analysis)
    component_ids = [row[0] for row in session.query(dbm.Component.id).filter(
        dbm.Component.project_id == project.id
    ).all()]
    if component_ids:
        session.query(dbm.ComponentSpecification).filter(
            dbm.ComponentSpecification.component_id.in_(component_ids)
        ).delete(synchronize_session=False)
    for table in (
        dbm.UserFeedback, dbm.Connection, dbm.RuleFinding, dbm.ModelFinding, dbm.AIReview,
        dbm.Net, dbm.Component,
    ):
        session.query(table).filter(table.project_id == project.id).delete()

    project.normalized = normalized.model_dump()
    project.power_tree = power_tree_dict
    project.source_format = normalized.project.source_format
    project.rule_engine_version = engine_version()
    project.model_version = model_version

    for comp in normalized.components:
        session.add(dbm.Component(
            project_id=project.id, reference=comp.reference, value=comp.value,
            footprint=comp.footprint, mpn=comp.mpn, lib_id=comp.lib_id, data=comp.model_dump(),
        ))
    for net in normalized.nets:
        session.add(dbm.Net(
            project_id=project.id, name=net.name, is_power=net.is_power, is_ground=net.is_ground,
            inferred_voltage=net.inferred_voltage, pin_count=len(net.pins), data=net.model_dump(),
        ))
        for np in net.pins:
            session.add(dbm.Connection(
                project_id=project.id, net_name=net.name, component_ref=np.component, pin=np.pin,
            ))

    counts = {"warning": 0, "error": 0, "critical": 0}
    for f in findings:
        counts[f.severity.value] = counts.get(f.severity.value, 0) + 1
        session.add(dbm.RuleFinding(
            project_id=project.id, rule_id=f.rule_id, rule_version=f.rule_version,
            category=f.category.value, severity=f.severity.value, title=f.title,
            description=f.description, suggested_fix=f.suggested_fix, confidence=f.confidence,
            source=f.source, affected_components=f.affected_components,
            affected_nets=f.affected_nets, evidence=[e.model_dump() for e in f.evidence],
        ))

    review_row = dbm.AIReview(
        project_id=project.id, model_version=model_version, provider=provider,
        payload=review.model_dump(), validation_warnings=warnings,
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        latency_ms=usage.get("latency_ms", 0.0),
    )
    session.add(review_row)
    session.flush()
    for item in review.possible_findings:
        session.add(dbm.ModelFinding(
            project_id=project.id, review_id=review_row.id, kind="possible", title=item.title,
            detail=item.detail, rule_id=item.rule_id, components=item.components,
            nets=item.nets, evidence=item.evidence,
        ))

    project.component_count = len(normalized.components)
    project.warning_count = counts["warning"]
    project.error_count = counts["error"]
    project.critical_count = counts["critical"]
    project.status = "completed"
    session.commit()
