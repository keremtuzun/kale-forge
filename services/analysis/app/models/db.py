"""SQLAlchemy 2.0 ORM models — the authoritative schema for the analysis service.

The web app mirrors this in prisma/schema.prisma; migrations live in ./migrations.
JSON columns hold structured payloads (normalized project, evidence, power tree) that the
service treats as opaque blobs at the DB layer and validates via pydantic at the edges.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    api_token: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    password_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    account_status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    projects: Mapped[list["Project"]] = relationship(back_populates="owner")


class WebSession(Base):
    __tablename__ = "web_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PasswordResetToken(Base):
    """Single-use, short-lived password reset token.

    Only the SHA-256 digest is stored. A database leak therefore does not expose
    usable reset links.
    """

    __tablename__ = "password_reset_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="created")
    # created | uploading | queued | analyzing | completed | failed
    source_format: Mapped[str] = mapped_column(String(32), default="")
    component_count: Mapped[int] = mapped_column(Integer, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    critical_count: Mapped[int] = mapped_column(Integer, default=0)
    model_version: Mapped[str] = mapped_column(String(64), default="")
    rule_engine_version: Mapped[str] = mapped_column(String(32), default="")
    normalized: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    power_tree: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    current_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    owner: Mapped[Optional[User]] = relationship(back_populates="projects")
    files: Mapped[list["UploadedFile"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    jobs: Mapped[list["AnalysisJob"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    components: Mapped[list["Component"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    nets: Mapped[list["Net"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    rule_findings: Mapped[list["RuleFinding"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    reviews: Mapped[list["AIReview"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class UploadedFile(Base):
    __tablename__ = "uploaded_files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    filename: Mapped[str] = mapped_column(String(300))
    storage_key: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(120), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(32), default="")  # schematic|pcb|project|netlist|bom|datasheet|archive
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    project: Mapped[Project] = relationship(back_populates="files")


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    status: Mapped[str] = mapped_column(String(32), default="queued")  # queued|running|completed|failed
    error: Mapped[str] = mapped_column(Text, default="")
    model_version: Mapped[str] = mapped_column(String(64), default="")
    rule_engine_version: Mapped[str] = mapped_column(String(32), default="")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    project: Mapped[Project] = relationship(back_populates="jobs")


class Component(Base):
    __tablename__ = "components"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    reference: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(String(200), default="")
    footprint: Mapped[str] = mapped_column(String(300), default="")
    mpn: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    lib_id: Mapped[str] = mapped_column(String(200), default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # full normalized component
    project: Mapped[Project] = relationship(back_populates="components")
    specifications: Mapped[list["ComponentSpecification"]] = relationship(
        back_populates="component", cascade="all, delete-orphan"
    )


class Net(Base):
    __tablename__ = "nets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(200))
    is_power: Mapped[bool] = mapped_column(Boolean, default=False)
    is_ground: Mapped[bool] = mapped_column(Boolean, default=False)
    inferred_voltage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pin_count: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    project: Mapped[Project] = relationship(back_populates="nets")


class Connection(Base):
    __tablename__ = "connections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    net_name: Mapped[str] = mapped_column(String(200))
    component_ref: Mapped[str] = mapped_column(String(64))
    pin: Mapped[str] = mapped_column(String(32))


class RuleFinding(Base):
    __tablename__ = "rule_findings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    rule_id: Mapped[str] = mapped_column(String(32))
    rule_version: Mapped[str] = mapped_column(String(16), default="")
    category: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    suggested_fix: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(300), default="")
    affected_components: Mapped[list[Any]] = mapped_column(JSON, default=list)
    affected_nets: Mapped[list[Any]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[Any]] = mapped_column(JSON, default=list)
    project: Mapped[Project] = relationship(back_populates="rule_findings")
    feedback: Mapped[list["UserFeedback"]] = relationship(back_populates="finding", cascade="all, delete-orphan")


class ModelFinding(Base):
    """A finding originated by the model (possible_finding), stored distinctly from rule findings."""
    __tablename__ = "model_findings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    review_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ai_reviews.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="possible")  # confirmed|possible|recommendation
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str] = mapped_column(Text, default="")
    rule_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    components: Mapped[list[Any]] = mapped_column(JSON, default=list)
    nets: Mapped[list[Any]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[Any]] = mapped_column(JSON, default=list)


class AIReview(Base):
    __tablename__ = "ai_reviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    model_version: Mapped[str] = mapped_column(String(64), default="")
    provider: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # validated AIReview JSON
    validation_warnings: Mapped[list[Any]] = mapped_column(JSON, default=list)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    project: Mapped[Project] = relationship(back_populates="reviews")


class ChatConversation(Base):
    __tablename__ = "chat_conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("chat_conversations.id"))
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    model_version: Mapped[str] = mapped_column(String(64), default="")
    provider: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    conversation: Mapped[ChatConversation] = relationship(back_populates="messages")


class ComponentSpecification(Base):
    """Per-field provenance: each spec field is paired with a status
    (verified|user-provided|extracted|inferred|missing)."""
    __tablename__ = "component_specifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    component_id: Mapped[str] = mapped_column(ForeignKey("components.id"))
    manufacturer: Mapped[str] = mapped_column(String(200), default="")
    mpn: Mapped[str] = mapped_column(String(200), default="")
    category: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    operating_voltage_v: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_voltage_v: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_current_a: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    power_rating_w: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    voltage_rating_v: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    package: Mapped[str] = mapped_column(String(120), default="")
    temperature_range: Mapped[str] = mapped_column(String(120), default="")
    lifecycle_status: Mapped[str] = mapped_column(String(64), default="")
    datasheet_location: Mapped[str] = mapped_column(String(500), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(200), default="")
    verification_status: Mapped[str] = mapped_column(String(32), default="missing")
    field_provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    component: Mapped[Component] = relationship(back_populates="specifications")


class ExportedReport(Base):
    __tablename__ = "exported_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    storage_key: Mapped[str] = mapped_column(String(500))
    format: Mapped[str] = mapped_column(String(16), default="html")
    model_version: Mapped[str] = mapped_column(String(64), default="")
    rule_engine_version: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class UserFeedback(Base):
    __tablename__ = "user_feedback"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    finding_id: Mapped[Optional[str]] = mapped_column(ForeignKey("rule_findings.id"), nullable=True)
    review_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    rating: Mapped[str] = mapped_column(String(32))
    # correct|incorrect|partially_correct|unclear|already_known|not_useful
    edited_explanation: Mapped[str] = mapped_column(Text, default="")
    edited_fix: Mapped[str] = mapped_column(Text, default="")
    added_evidence: Mapped[str] = mapped_column(Text, default="")
    false_positive: Mapped[bool] = mapped_column(Boolean, default=False)
    missed_issue: Mapped[str] = mapped_column(Text, default="")
    consent_to_train: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finding: Mapped[Optional[RuleFinding]] = relationship(back_populates="feedback")


class TrainingExample(Base):
    __tablename__ = "training_examples"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(64))
    license: Mapped[str] = mapped_column(String(64), default="internal")
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    safety_class: Mapped[str] = mapped_column(String(32), default="general")
    dataset_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    version: Mapped[str] = mapped_column(String(64), unique=True)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    parent_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    source_mix: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    storage_path: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    version: Mapped[str] = mapped_column(String(64), unique=True)
    base_model: Mapped[str] = mapped_column(String(200), default="")
    adapter_path: Mapped[str] = mapped_column(String(500), default="")
    provider: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="available")  # active|available|rolled_back
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class FineTuningRun(Base):
    __tablename__ = "fine_tuning_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    config_name: Mapped[str] = mapped_column(String(120), default="")
    base_model: Mapped[str] = mapped_column(String(200), default="")
    dataset_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="created")  # created|running|completed|failed
    seed: Mapped[int] = mapped_column(Integer, default=42)
    command: Mapped[str] = mapped_column(Text, default="")
    output_adapter: Mapped[str] = mapped_column(String(500), default="")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    config_name: Mapped[str] = mapped_column(String(64))  # rules-only|base|finetuned|rules+finetuned
    model_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    dataset_hash: Mapped[str] = mapped_column(String(64), default="")
    overall: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    results: Mapped[list["EvaluationResult"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"))
    category: Mapped[str] = mapped_column(String(64))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    run: Mapped[EvaluationRun] = relationship(back_populates="results")


# --- engine / session helpers --------------------------------------------------------------

_engine = None
_SessionLocal = None


def init_engine(database_url: str):
    global _engine, _SessionLocal
    is_sqlite = database_url.startswith("sqlite")
    connect_args = {"check_same_thread": False, "timeout": 30} if is_sqlite else {}
    _engine = create_engine(database_url, connect_args=connect_args, future=True)
    if is_sqlite:
        # WAL + busy_timeout let the background analysis thread write while the API reads,
        # instead of colliding on SQLite's default database-level lock.
        @event.listens_for(_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)
    return _engine


def init_db(database_url: str):
    engine = init_engine(database_url)
    Base.metadata.create_all(engine)
    if database_url.startswith("sqlite"):
        # create_all intentionally does not alter existing tables. Keep old Kale
        # installations forward-compatible without a destructive database rebuild.
        with engine.begin() as connection:
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(users)")}
            if "password_hash" not in columns:
                connection.exec_driver_sql("ALTER TABLE users ADD COLUMN password_hash TEXT")
            if "account_status" not in columns:
                connection.exec_driver_sql("ALTER TABLE users ADD COLUMN account_status VARCHAR(32) DEFAULT 'active'")
    return engine


def get_sessionmaker():
    if _SessionLocal is None:
        raise RuntimeError("database not initialized; call init_db first")
    return _SessionLocal
