"""COMP-005: connectors without a functional label."""
from __future__ import annotations

import re

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_connector

_GENERIC_RE = re.compile(r"^(conn|connector|header|conn_\d+x\d+.*|pin_?header.*|~|)$", re.IGNORECASE)


class UnlabeledConnectorsRule(Rule):
    id = "COMP-005"
    version = "1.0.0"
    category = RuleCategory.COMPONENTS
    title = "Connector without a functional label"
    description = "Connectors should say what plugs into them; generic names cause assembly/bring-up mistakes."
    default_severity = Severity.INFO

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for comp in project.components:
            if comp.dnp or not is_connector(comp):
                continue
            if not _GENERIC_RE.match(comp.value.strip()):
                continue
            findings.append(
                self.finding(
                    description=(
                        f"Connector {comp.reference} has a generic value ('{comp.value or ''}'). "
                        "Label its function (e.g. 'UART DEBUG', 'BATTERY IN')."
                    ),
                    affected_components=[comp.reference],
                    evidence=[Evidence(kind="component", reference=comp.reference,
                                       description=f"generic connector value '{comp.value}'")],
                    suggested_fix="Rename the connector value to its function and add pin-1 marking on silkscreen.",
                    confidence=0.7,
                )
            )
        return findings
