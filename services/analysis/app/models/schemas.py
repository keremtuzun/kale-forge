"""API request/response DTOs (pydantic) for the analysis service."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class ProjectSummary(BaseModel):
    id: str
    name: str
    description: str = ""
    status: str
    source_format: str = ""
    component_count: int = 0
    warning_count: int = 0
    error_count: int = 0
    critical_count: int = 0
    model_version: str = ""
    rule_engine_version: str = ""
    file_count: int = 0
    created_at: datetime
    updated_at: datetime


class UploadedFileOut(BaseModel):
    id: str
    filename: str
    kind: str
    size_bytes: int
    created_at: datetime


class JobOut(BaseModel):
    id: str
    status: str
    error: str = ""
    model_version: str = ""
    rule_engine_version: str = ""
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class FindingOut(BaseModel):
    id: str
    rule_id: str
    rule_version: str
    category: str
    severity: str
    title: str
    description: str
    suggested_fix: str
    confidence: float
    source: str
    affected_components: list[str]
    affected_nets: list[str]
    evidence: list[dict[str, Any]]


class FindingsResponse(BaseModel):
    rule_findings: list[FindingOut]
    ai_review: Optional[dict[str, Any]] = None
    model_version: str = ""
    provider: str = ""
    rule_engine_version: str = ""


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: Optional[str] = None


class ChatEvidence(BaseModel):
    components: list[str] = Field(default_factory=list)
    nets: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    source_files: list[str] = Field(default_factory=list)
    snippets: list[dict[str, Any]] = Field(default_factory=list)
    model_version: str = ""
    provider: str = ""


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    evidence: ChatEvidence
    ai_review: Optional[dict[str, Any]] = None


class FeedbackCreate(BaseModel):
    rating: str  # correct|incorrect|partially_correct|unclear|already_known|not_useful
    edited_explanation: str = ""
    edited_fix: str = ""
    added_evidence: str = ""
    false_positive: bool = False
    missed_issue: str = ""
    consent_to_train: bool = False


class ComponentSpecUpdate(BaseModel):
    manufacturer: Optional[str] = None
    mpn: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    operating_voltage_v: Optional[float] = None
    max_voltage_v: Optional[float] = None
    max_current_a: Optional[float] = None
    power_rating_w: Optional[float] = None
    voltage_rating_v: Optional[float] = None
    package: Optional[str] = None
    temperature_range: Optional[str] = None
    lifecycle_status: Optional[str] = None
    datasheet_location: Optional[str] = None
    notes: Optional[str] = None
    verification_status: Optional[str] = None
    field_provenance: dict[str, str] = Field(default_factory=dict)


class CurrentEstimateUpdate(BaseModel):
    overrides: dict[str, float] = Field(default_factory=dict)  # ref -> mA (or "REF:capacity_mah")


class ModelVersionOut(BaseModel):
    version: str
    base_model: str
    adapter_path: str
    provider: str
    status: str
    notes: str
    created_at: datetime


class ModelSelectRequest(BaseModel):
    version: str


class TrainingExportRequest(BaseModel):
    include_feedback: bool = True
    only_consented: bool = True
    dataset_version: Optional[str] = None


class FineTuneRequest(BaseModel):
    config_name: str = "lora-default"
    dataset_version: Optional[str] = None
    base_model: Optional[str] = None
    seed: int = 42
