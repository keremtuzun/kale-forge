"""CONN-007: multiple ground nets with no component bridging them."""
from __future__ import annotations

from itertools import combinations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity


class SplitGroundDomainsRule(Rule):
    id = "CONN-007"
    version = "1.0.0"
    category = RuleCategory.CONNECTIVITY
    title = "Disconnected ground domains"
    description = (
        "Separate ground nets (e.g. GND vs AGND) must join at some star/bridge point; fully "
        "isolated grounds break return paths."
    )
    default_severity = Severity.WARNING

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        grounds = ctx.graph.ground_nets()
        if len(grounds) < 2:
            return []
        findings: list[RuleFinding] = []
        for a, b in combinations(grounds, 2):
            comps_a = ctx.graph.components_on_net(a.name)
            comps_b = ctx.graph.components_on_net(b.name)
            if comps_a & comps_b:
                continue  # bridged by at least one component
            findings.append(
                self.finding(
                    description=(
                        f"Ground nets '{a.name}' and '{b.name}' share no component — the two "
                        "ground domains are electrically isolated in this schematic. If they are "
                        "meant to be split grounds, they still need a deliberate join (0 Ω link, "
                        "ferrite, or a single star point)."
                    ),
                    affected_nets=[a.name, b.name],
                    evidence=[
                        Evidence(kind="net", reference=a.name,
                                 description=f"{len(comps_a)} components, none shared with {b.name}"),
                        Evidence(kind="net", reference=b.name,
                                 description=f"{len(comps_b)} components, none shared with {a.name}"),
                    ],
                    suggested_fix="Join the domains at one point (net tie, 0 Ω resistor, or ferrite bead).",
                    confidence=0.7,
                )
            )
        return findings
