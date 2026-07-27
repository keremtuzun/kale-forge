"""All analysis-service HTTP routes (prefix /api). Matches docs/architecture.md API table."""
from __future__ import annotations

import glob
import httpx
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import db as dbm
from app.models.schemas import (
    ChatRequest,
    ChatResponse,
    ComponentSpecUpdate,
    CurrentEstimateUpdate,
    FeedbackCreate,
    FindingOut,
    FindingsResponse,
    FineTuneRequest,
    JobOut,
    ModelSelectRequest,
    ModelVersionOut,
    ProjectCreate,
    ProjectSummary,
    TrainingExportRequest,
    UploadedFileOut,
)
from app.rules.registry import engine_version
from app.services import analysis_service, chat_service
from app.services.jobs import get_job_queue
from app.services.report import generate_report_html
from app.services.security import (
    classify_file,
    get_current_user,
    get_db,
    require_admin,
    validate_upload,
)
from app.services.storage import get_storage
from app.services.onshape import onshape

router = APIRouter(prefix="/api")

MODELS_EVAL_DIR = Path(__file__).resolve().parents[4] / "models" / "evaluations"
DATASETS_DIR = Path(__file__).resolve().parents[4] / "datasets" / "processed"


def _project_summary(session: Session, p: dbm.Project) -> ProjectSummary:
    file_count = session.query(dbm.UploadedFile).filter(dbm.UploadedFile.project_id == p.id).count()
    return ProjectSummary(
        id=p.id, name=p.name, description=p.description, status=p.status,
        source_format=p.source_format, component_count=p.component_count,
        warning_count=p.warning_count, error_count=p.error_count, critical_count=p.critical_count,
        model_version=p.model_version, rule_engine_version=p.rule_engine_version,
        file_count=file_count, created_at=p.created_at, updated_at=p.updated_at,
    )


def _project_for_user(db: Session, project_id: str, user: dbm.User) -> dbm.Project:
    project = db.get(dbm.Project, project_id)
    if project is None or project.owner_id != user.id:
        # Do not disclose the existence of another user's project.
        raise HTTPException(404, "project not found")
    return project


def _unique_filename(filename: str, used_names: set[str]) -> str:
    """Keep every uploaded source file instead of overwriting same-named uploads."""
    if filename not in used_names:
        return filename
    path = Path(filename)
    index = 2
    candidate = filename
    while candidate in used_names:
        candidate = f"{path.stem}-{index}{path.suffix}"
        index += 1
    return candidate


# --- projects ------------------------------------------------------------------------------


@router.post("/projects", response_model=ProjectSummary, status_code=201)
def create_project(body: ProjectCreate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    project = dbm.Project(name=body.name, description=body.description, owner_id=user.id, status="created")
    db.add(project)
    db.commit()
    return _project_summary(db, project)


@router.get("/projects", response_model=list[ProjectSummary])
def list_projects(db: Session = Depends(get_db), user=Depends(get_current_user)):
    rows = db.execute(
        select(dbm.Project).where(dbm.Project.owner_id == user.id).order_by(dbm.Project.created_at.desc())
    ).scalars().all()
    return [_project_summary(db, p) for p in rows]


@router.get("/projects/{project_id}", response_model=ProjectSummary)
def get_project(project_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    p = _project_for_user(db, project_id, user)
    return _project_summary(db, p)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    p = _project_for_user(db, project_id, user)
    try:
        get_storage(get_settings()).delete_project(project_id)
    except Exception:  # noqa: BLE001 — storage cleanup best-effort
        pass
    component_ids = [row[0] for row in db.query(dbm.Component.id).filter(dbm.Component.project_id == p.id).all()]
    conversation_ids = [row[0] for row in db.query(dbm.ChatConversation.id).filter(
        dbm.ChatConversation.project_id == p.id
    ).all()]
    if component_ids:
        db.query(dbm.ComponentSpecification).filter(dbm.ComponentSpecification.component_id.in_(component_ids)).delete(
            synchronize_session=False
        )
    if conversation_ids:
        db.query(dbm.ChatMessage).filter(dbm.ChatMessage.conversation_id.in_(conversation_ids)).delete(
            synchronize_session=False
        )
    for table in (
        dbm.UserFeedback, dbm.ModelFinding, dbm.AIReview, dbm.RuleFinding, dbm.Connection, dbm.Net,
        dbm.Component, dbm.ChatConversation, dbm.ExportedReport, dbm.AnalysisJob, dbm.UploadedFile,
    ):
        db.query(table).filter(table.project_id == p.id).delete(synchronize_session=False)
    db.delete(p)
    db.commit()


# --- files & analysis ----------------------------------------------------------------------


@router.post("/projects/{project_id}/files", response_model=list[UploadedFileOut])
async def upload_files(
    project_id: str,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    settings = get_settings()
    project = _project_for_user(db, project_id, user)
    storage = get_storage(settings)
    out: list[UploadedFileOut] = []
    used_names = {row.filename for row in project.files}
    for upload in files:
        data = await upload.read()
        safe_name = _unique_filename(
            validate_upload(upload.filename or "unnamed", len(data), settings), used_names
        )
        used_names.add(safe_name)
        key = storage.save(project_id, safe_name, data)
        row = dbm.UploadedFile(
            project_id=project_id, filename=safe_name, storage_key=key,
            content_type=upload.content_type or "", size_bytes=len(data),
            kind=classify_file(safe_name),
        )
        db.add(row)
        db.commit()
        out.append(UploadedFileOut(id=row.id, filename=row.filename, kind=row.kind,
                                   size_bytes=row.size_bytes, created_at=row.created_at))
    project.status = "uploaded"
    db.commit()
    return out


@router.post("/projects/{project_id}/analyze", response_model=JobOut, status_code=202)
def start_analysis(project_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    settings = get_settings()
    project = _project_for_user(db, project_id, user)
    if not project.files:
        raise HTTPException(400, "no files uploaded")
    active_job = db.query(dbm.AnalysisJob).filter(
        dbm.AnalysisJob.project_id == project_id, dbm.AnalysisJob.status.in_(("queued", "running"))
    ).first()
    if active_job is not None:
        raise HTTPException(409, "analysis is already running")
    queue = get_job_queue(settings)
    project.status = "queued"
    # create a job row up front so status can be polled immediately
    job = dbm.AnalysisJob(project_id=project_id, status="queued",
                          rule_engine_version=engine_version(), model_version=settings.model_version)
    db.add(job)
    db.commit()
    job_id = job.id
    queue.submit(job_id, lambda: analysis_service.run_analysis(project_id, job_id))
    return JobOut(id=job_id, status="queued", model_version=job.model_version,
                  rule_engine_version=job.rule_engine_version, created_at=job.created_at)


@router.get("/projects/{project_id}/jobs/{job_id}", response_model=JobOut)
def job_status(project_id: str, job_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _project_for_user(db, project_id, user)
    job = db.get(dbm.AnalysisJob, job_id)
    if job is None or job.project_id != project_id:
        raise HTTPException(404, "job not found")
    return JobOut(id=job.id, status=job.status, error=job.error, model_version=job.model_version,
                  rule_engine_version=job.rule_engine_version, created_at=job.created_at,
                  started_at=job.started_at, finished_at=job.finished_at)


@router.get("/projects/{project_id}/data")
def get_normalized_data(project_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    p = _project_for_user(db, project_id, user)
    return {"normalized": p.normalized, "power_tree": p.power_tree,
            "model_version": p.model_version, "rule_engine_version": p.rule_engine_version}


@router.post("/projects/{project_id}/onshape/import")
def import_project_obj(project_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    project = _project_for_user(db, project_id, user)
    source = next((item for item in project.files if item.filename.lower().endswith(".obj")), None)
    if source is None:
        raise HTTPException(400, "Upload an OBJ file to this project first")
    try:
        payload = get_storage(get_settings()).read(source.storage_key)
        return onshape.import_obj(user.id, f"{project.name} — Kale OBJ Import", source.filename, payload)
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"Onshape import failed: {exc}") from exc
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/projects/{project_id}/findings", response_model=FindingsResponse)
def get_findings(project_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    p = _project_for_user(db, project_id, user)
    rows = db.query(dbm.RuleFinding).filter(dbm.RuleFinding.project_id == project_id).all()
    findings = [
        FindingOut(
            id=r.id, rule_id=r.rule_id, rule_version=r.rule_version, category=r.category,
            severity=r.severity, title=r.title, description=r.description,
            suggested_fix=r.suggested_fix, confidence=r.confidence, source=r.source,
            affected_components=r.affected_components or [], affected_nets=r.affected_nets or [],
            evidence=r.evidence or [],
        )
        for r in rows
    ]
    review = (
        db.query(dbm.AIReview)
        .filter(dbm.AIReview.project_id == project_id)
        .order_by(dbm.AIReview.created_at.desc())
        .first()
    )
    return FindingsResponse(
        rule_findings=findings,
        ai_review=(review.payload if review else None),
        model_version=(review.model_version if review else p.model_version),
        provider=(review.provider if review else ""),
        rule_engine_version=p.rule_engine_version,
    )


# --- power ---------------------------------------------------------------------------------


@router.get("/projects/{project_id}/power")
def get_power(project_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    p = _project_for_user(db, project_id, user)
    return {"power_tree": p.power_tree, "current_overrides": p.current_overrides or {}}


@router.patch("/projects/{project_id}/power")
def update_power(project_id: str, body: CurrentEstimateUpdate, db: Session = Depends(get_db),
                 user=Depends(get_current_user)):
    from app.models.normalized import NormalizedProject

    p = _project_for_user(db, project_id, user)
    overrides = dict(p.current_overrides or {})
    overrides.update(body.overrides)
    p.current_overrides = overrides
    if p.normalized:
        try:
            from app.calculations.power_tree import build_power_tree

            normalized = NormalizedProject.model_validate(p.normalized)
            p.power_tree = build_power_tree(normalized, overrides).model_dump()
        except Exception:  # noqa: BLE001
            pass
    db.commit()
    return {"power_tree": p.power_tree, "current_overrides": overrides}


# --- chat ----------------------------------------------------------------------------------


@router.post("/projects/{project_id}/chat", response_model=ChatResponse)
def chat(project_id: str, body: ChatRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    p = _project_for_user(db, project_id, user)
    try:
        return chat_service.ask(db, project_id, body.question, body.conversation_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


# --- feedback ------------------------------------------------------------------------------


@router.post("/findings/{finding_id}/feedback", status_code=201)
def submit_feedback(finding_id: str, body: FeedbackCreate, db: Session = Depends(get_db),
                    user=Depends(get_current_user)):
    finding = db.get(dbm.RuleFinding, finding_id)
    if finding is None:
        raise HTTPException(404, "finding not found")
    _project_for_user(db, finding.project_id, user)
    fb = dbm.UserFeedback(
        project_id=finding.project_id, finding_id=finding_id, user_id=user.id,
        rating=body.rating, edited_explanation=body.edited_explanation, edited_fix=body.edited_fix,
        added_evidence=body.added_evidence, false_positive=body.false_positive,
        missed_issue=body.missed_issue, consent_to_train=body.consent_to_train,
    )
    db.add(fb)
    db.commit()
    return {"id": fb.id, "status": "recorded"}


# --- components ----------------------------------------------------------------------------


@router.patch("/components/{component_id}")
def update_component(component_id: str, body: ComponentSpecUpdate, db: Session = Depends(get_db),
                     user=Depends(get_current_user)):
    comp = db.get(dbm.Component, component_id)
    if comp is None:
        raise HTTPException(404, "component not found")
    _project_for_user(db, comp.project_id, user)
    spec = comp.specifications[0] if comp.specifications else dbm.ComponentSpecification(component_id=comp.id)
    if not comp.specifications:
        db.add(spec)
    provenance = dict(spec.field_provenance or {})
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "field_provenance":
            continue
        setattr(spec, field, value)
        # user-edited fields are user-provided unless the client marked otherwise
        provenance[field] = body.field_provenance.get(field, "user-provided")
    provenance.update(body.field_provenance)
    spec.field_provenance = provenance
    db.commit()
    return {"id": spec.id, "field_provenance": spec.field_provenance,
            "verification_status": spec.verification_status}


# --- report --------------------------------------------------------------------------------


@router.post("/projects/{project_id}/report")
def generate_report(project_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    p = _project_for_user(db, project_id, user)
    if not p.normalized:
        raise HTTPException(409, "project not analyzed yet")
    findings = [
        {
            "rule_id": r.rule_id, "severity": r.severity, "title": r.title,
            "description": r.description, "suggested_fix": r.suggested_fix,
            "affected_components": r.affected_components or [], "affected_nets": r.affected_nets or [],
        }
        for r in db.query(dbm.RuleFinding).filter(dbm.RuleFinding.project_id == project_id).all()
    ]
    review = (
        db.query(dbm.AIReview).filter(dbm.AIReview.project_id == project_id)
        .order_by(dbm.AIReview.created_at.desc()).first()
    )
    review_payload = dict(review.payload) if review else {}
    if review:
        review_payload["_provider"] = review.provider
    html = generate_report_html(p, p.normalized, findings, review_payload, p.power_tree)
    storage = get_storage(get_settings())
    key = storage.save(project_id, f"report-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.html", html.encode())
    db.add(dbm.ExportedReport(project_id=project_id, storage_key=key, format="html",
                              model_version=p.model_version, rule_engine_version=p.rule_engine_version))
    db.commit()
    from fastapi.responses import HTMLResponse

    return HTMLResponse(content=html)


# --- model registry ------------------------------------------------------------------------


@router.get("/models", response_model=list[ModelVersionOut])
def list_models(db: Session = Depends(get_db), user=Depends(get_current_user)):
    rows = db.query(dbm.ModelVersion).order_by(dbm.ModelVersion.created_at.desc()).all()
    return [
        ModelVersionOut(version=m.version, base_model=m.base_model, adapter_path=m.adapter_path,
                        provider=m.provider, status=m.status, notes=m.notes, created_at=m.created_at)
        for m in rows
    ]


@router.post("/models/select")
def select_model(body: ModelSelectRequest, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    target = db.execute(select(dbm.ModelVersion).where(dbm.ModelVersion.version == body.version)).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, "model version not found")
    for m in db.query(dbm.ModelVersion).all():
        if m.status == "active":
            m.status = "available"
    target.status = "active"
    db.commit()
    return {"active": target.version}


# --- evaluations ---------------------------------------------------------------------------


@router.get("/evaluations")
def list_evaluations(db: Session = Depends(get_db), user=Depends(get_current_user)):
    runs = []
    for m in db.query(dbm.EvaluationRun).order_by(dbm.EvaluationRun.created_at.desc()).all():
        runs.append({
            "id": m.id, "config_name": m.config_name, "model_version": m.model_version,
            "dataset_hash": m.dataset_hash, "overall": m.overall, "created_at": m.created_at.isoformat(),
            "results": [{"category": r.category, "metrics": r.metrics} for r in m.results],
        })
    # also surface any evaluation report JSON files on disk (from services/evaluation)
    file_reports = []
    for path in sorted(glob.glob(str(MODELS_EVAL_DIR / "*.json")), reverse=True)[:20]:
        try:
            with open(path) as fh:
                file_reports.append({"file": os.path.basename(path), **json.load(fh)})
        except Exception:  # noqa: BLE001
            continue
    return {"db_runs": runs, "file_reports": file_reports}


# --- training (admin) ----------------------------------------------------------------------


@router.post("/training/export")
def training_export(body: TrainingExportRequest, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    from app.services.training_export import export_training_data

    return export_training_data(db, only_consented=body.only_consented,
                                include_feedback=body.include_feedback)


@router.post("/training/finetune")
def start_finetune(body: FineTuneRequest, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    base_model = body.base_model or "Qwen/Qwen2.5-7B-Instruct"
    dataset = body.dataset_version or "latest"
    command = (
        f"python services/training/scripts/train_sft.py "
        f"--config training/configs/{body.config_name}.yaml "
        f"--dataset-version {dataset} --seed {body.seed}"
    )
    run = dbm.FineTuningRun(
        config_name=body.config_name, base_model=base_model, dataset_version=body.dataset_version,
        status="created", seed=body.seed, command=command,
    )
    db.add(run)
    db.commit()
    return {
        "id": run.id, "status": run.status, "command": command,
        "note": "Fine-tuning runs on GPU infrastructure; this endpoint records the run and the "
                "exact command to execute. See docs/local-gpu-setup.md.",
    }


@router.get("/training/runs/{run_id}")
def finetune_status(run_id: str, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    run = db.get(dbm.FineTuningRun, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return {"id": run.id, "status": run.status, "config_name": run.config_name,
            "base_model": run.base_model, "command": run.command, "metrics": run.metrics,
            "output_adapter": run.output_adapter, "created_at": run.created_at.isoformat()}
