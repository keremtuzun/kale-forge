"""Training-example schema (see docs/training-data-strategy.md). The output mirrors the
production AIReview shape so SFT directly teaches the runtime JSON contract."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ExampleInput(BaseModel):
    project: dict[str, Any] = Field(default_factory=dict)
    components: list[dict[str, Any]] = Field(default_factory=list)
    nets: list[dict[str, Any]] = Field(default_factory=list)
    rule_findings: list[dict[str, Any]] = Field(default_factory=list)
    power_tree: Optional[dict[str, Any]] = None
    question: Optional[str] = None


class ExampleOutput(BaseModel):
    summary: str = ""
    confirmed_findings: list[dict[str, Any]] = Field(default_factory=list)
    possible_findings: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    questions_for_engineer: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    limitations: list[str] = Field(default_factory=list)


class ExampleMetadata(BaseModel):
    id: str = ""
    source: str = "synthetic"  # synthetic|manual|rules|expert|public|user_feedback
    license: str = "internal"
    reviewed: bool = False
    safety_class: str = "general"  # general|power|safety_critical
    assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    created_at: str = ""
    dataset_version: Optional[str] = None


class TrainingExample(BaseModel):
    instruction: str
    input: ExampleInput
    output: ExampleOutput
    metadata: ExampleMetadata = Field(default_factory=ExampleMetadata)


TRAINING_EXAMPLE_JSON_SCHEMA = TrainingExample.model_json_schema()
