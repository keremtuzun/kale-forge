"""PCB-011: decoupling capacitors placed far from their IC on the board."""
from __future__ import annotations

from app.models.normalized import NormalizedProject, PinType
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_capacitor, is_ic


class DecouplingDistanceRule(Rule):
    id = "PCB-011"
    version = "1.0.0"
    category = RuleCategory.PCB
    title = "Decoupling capacitor far from IC"
    description = "Decoupling works through low loop inductance — distance defeats it."
    default_severity = Severity.WARNING

    def applies(self, project: NormalizedProject) -> bool:
        return project.board is not None

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        max_dist = float(ctx.threshold("decoupling_distance_mm", 5.0))
        findings: list[RuleFinding] = []
        for ic in project.components:
            if not is_ic(ic) or ic.position is None or ic.layer is None:
                continue
            supply_nets = {
                p.net for p in ic.pins
                if p.net and p.type == PinType.POWER_IN
                and (n := ctx.graph.nets.get(p.net)) is not None and not n.is_ground
            }
            for net_name in sorted(supply_nets):
                caps = [
                    c for ref in ctx.graph.components_on_net(net_name)
                    if (c := ctx.graph.components.get(ref)) is not None
                    and is_capacitor(c) and c.position is not None
                ]
                if not caps:
                    continue  # schematic-level absence is COMP-012's finding
                nearest = min(
                    ((c, ((c.position.x - ic.position.x) ** 2 + (c.position.y - ic.position.y) ** 2) ** 0.5)
                     for c in caps),
                    key=lambda t: t[1],
                )
                cap, dist = nearest
                if dist <= max_dist:
                    continue
                findings.append(
                    self.finding(
                        description=(
                            f"Nearest capacitor to {ic.reference} on rail '{net_name}' is {cap.reference} "
                            f"at {dist:.1f} mm (threshold decoupling_distance_mm={max_dist:g})."
                        ),
                        affected_components=[ic.reference, cap.reference],
                        affected_nets=[net_name],
                        evidence=[Evidence(kind="board",
                                           description=f"center-to-center distance {dist:.1f} mm > {max_dist:g} mm")],
                        suggested_fix="Move a 100 nF ceramic within a few mm of the IC supply pin.",
                        confidence=0.7,
                    )
                )
        return findings
