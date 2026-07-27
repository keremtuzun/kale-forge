"""CONN-008: duplicate component reference designators."""
from __future__ import annotations

from collections import Counter

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity


class DuplicateReferencesRule(Rule):
    id = "CONN-008"
    version = "1.0.0"
    category = RuleCategory.CONNECTIVITY
    title = "Duplicate reference designators"
    description = "Two components with the same reference break BOMs, netlists, and assembly."
    default_severity = Severity.ERROR

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        counts = Counter(c.reference for c in project.components)
        findings: list[RuleFinding] = []
        for ref, count in sorted(counts.items()):
            if count < 2 or ref.startswith("UNREF"):
                continue
            values = [c.value for c in project.components if c.reference == ref]
            findings.append(
                self.finding(
                    description=(
                        f"Reference '{ref}' is used by {count} components "
                        f"(values: {', '.join(v or '?' for v in values)}). Re-annotate the schematic."
                    ),
                    affected_components=[ref],
                    evidence=[
                        Evidence(kind="component", reference=ref,
                                 description=f"{count} components share this reference")
                    ],
                    suggested_fix="Run annotation to assign unique references.",
                    confidence=1.0,
                )
            )
        return findings
