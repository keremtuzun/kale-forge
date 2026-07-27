"""CONN-009: the design splits into multiple unconnected component islands."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import ref_prefix

_EXCLUDED_PREFIXES = {"H", "MH", "LOGO", "TP", "FID", "G"}


class IsolatedIslandsRule(Rule):
    id = "CONN-009"
    version = "1.0.0"
    category = RuleCategory.CONNECTIVITY
    title = "Isolated circuit islands"
    description = "Groups of components with no electrical path between them — possibly an unfinished section."
    default_severity = Severity.INFO

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        refs = [
            c.reference
            for c in project.components
            if ref_prefix(c.reference) not in _EXCLUDED_PREFIXES
        ]
        if len(refs) < 2:
            return []
        # union components through shared nets
        parent: dict[str, str] = {r: r for r in refs}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for net in project.nets:
            members = [np.component for np in net.pins if np.component in parent]
            for other in members[1:]:
                union(members[0], other)

        islands: dict[str, list[str]] = {}
        for r in refs:
            islands.setdefault(find(r), []).append(r)
        big_islands = [sorted(members) for members in islands.values() if len(members) >= 2]
        if len(big_islands) <= 1:
            return []
        big_islands.sort(key=len, reverse=True)
        smaller = big_islands[1:]
        return [
            self.finding(
                description=(
                    f"The design contains {len(big_islands)} electrically isolated islands. "
                    f"Main island has {len(big_islands[0])} components; separate island(s): "
                    + "; ".join(", ".join(island[:8]) + ("…" if len(island) > 8 else "") for island in smaller)
                ),
                affected_components=[ref for island in smaller for ref in island][:20],
                evidence=[
                    Evidence(kind="component", description=f"island of {len(island)} components: {', '.join(island[:8])}")
                    for island in smaller
                ],
                suggested_fix="Verify whether the islands should be interconnected or belong in another project.",
                confidence=0.8,
            )
        ]
