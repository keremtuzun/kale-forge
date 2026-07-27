"""COMP-002: BOM components without an assigned footprint."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_power_symbol_component


class MissingFootprintRule(Rule):
    id = "COMP-002"
    version = "1.0.0"
    category = RuleCategory.COMPONENTS
    title = "Missing footprint"
    description = "Components without footprints cannot be placed on the PCB."
    default_severity = Severity.WARNING

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        missing = [
            c for c in project.components
            if c.in_bom and not c.dnp and not c.footprint.strip() and not is_power_symbol_component(c)
        ]
        return [
            self.finding(
                description=f"{c.reference} ({c.value or c.lib_id}) has no footprint assigned.",
                affected_components=[c.reference],
                evidence=[Evidence(kind="component", reference=c.reference, description="footprint field empty")],
                suggested_fix="Assign the correct footprint before layout.",
                confidence=1.0,
            )
            for c in missing
        ]
