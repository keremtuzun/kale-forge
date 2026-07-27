"""Rule-engine core contract. Every deterministic check subclasses `Rule` and lives in its
own file under app/rules/{connectivity,components,power,pcb}/. Discovered by registry.py.

Rules are deterministic: same project + same config => same findings. They never call the
model, the network, or the filesystem.
"""
from __future__ import annotations

import abc
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.normalized import NormalizedProject

RULE_ENGINE_VERSION = "0.1.0"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


SEVERITY_ORDER = {
    Severity.CRITICAL: 0,
    Severity.ERROR: 1,
    Severity.WARNING: 2,
    Severity.INFO: 3,
}


class RuleCategory(str, Enum):
    CONNECTIVITY = "connectivity"
    COMPONENTS = "components"
    POWER = "power"
    PCB = "pcb"


class Evidence(BaseModel):
    kind: str  # component | net | pin | board | calculation | file | spec | rule
    description: str
    reference: Optional[str] = None  # e.g. "R1", "VCC", "U1.7", a file name, a rule id


class RuleFinding(BaseModel):
    rule_id: str
    rule_version: str
    category: RuleCategory
    severity: Severity
    title: str
    description: str
    affected_components: list[str] = Field(default_factory=list)
    affected_nets: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    suggested_fix: str = ""
    confidence: float = 1.0  # 0..1; deterministic structural facts = 1.0, heuristics lower
    source: str = "standard engineering practice"


class RuleContext(BaseModel):
    """Runtime context passed to every rule. `config` carries tunable thresholds
    (e.g. min trace widths); rules must read thresholds from here, not hardcode them,
    and must record the threshold used in the finding's evidence."""

    config: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}

    graph: Any = None  # app.rules.helpers.NetGraph — Any to avoid circular import

    def threshold(self, key: str, default: Any) -> Any:
        return self.config.get(key, default)


class Rule(abc.ABC):
    """Base class. Subclasses set the class attributes and implement check()."""

    id: str = ""  # e.g. "CONN-001"
    version: str = "1.0.0"
    category: RuleCategory = RuleCategory.CONNECTIVITY
    title: str = ""
    description: str = ""
    default_severity: Severity = Severity.WARNING
    source: str = "standard engineering practice"

    def applies(self, project: NormalizedProject) -> bool:
        """Cheap pre-filter, e.g. PCB rules skip schematic-only projects."""
        return True

    @abc.abstractmethod
    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]: ...

    def finding(
        self,
        *,
        title: Optional[str] = None,
        description: str,
        severity: Optional[Severity] = None,
        affected_components: Optional[list[str]] = None,
        affected_nets: Optional[list[str]] = None,
        evidence: Optional[list[Evidence]] = None,
        suggested_fix: str = "",
        confidence: float = 1.0,
    ) -> RuleFinding:
        return RuleFinding(
            rule_id=self.id,
            rule_version=self.version,
            category=self.category,
            severity=severity or self.default_severity,
            title=title or self.title,
            description=description,
            affected_components=affected_components or [],
            affected_nets=affected_nets or [],
            evidence=evidence or [],
            suggested_fix=suggested_fix,
            confidence=confidence,
            source=self.source,
        )
