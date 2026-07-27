"""PWR-003: power rails feeding multiple loads with no capacitance at all."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_capacitor


class RailNoBulkCapRule(Rule):
    id = "PWR-003"
    version = "1.0.0"
    category = RuleCategory.POWER
    title = "Power rail without any capacitance"
    description = "A distribution rail with zero capacitors will sag and ring under load transients."
    default_severity = Severity.WARNING

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        min_loads = int(ctx.threshold("rail_min_loads", 2))
        findings: list[RuleFinding] = []
        for net in ctx.graph.power_nets():
            members = ctx.graph.components_on_net(net.name)
            caps = [r for r in members if (c := ctx.graph.components.get(r)) and is_capacitor(c)]
            non_caps = [r for r in members if r not in caps]
            if len(non_caps) < min_loads or caps:
                continue
            findings.append(
                self.finding(
                    description=(
                        f"Power rail '{net.name}' feeds {len(non_caps)} components "
                        f"({', '.join(sorted(non_caps)[:8])}) but has no capacitors at all."
                    ),
                    affected_nets=[net.name],
                    affected_components=sorted(non_caps)[:10],
                    evidence=[
                        Evidence(kind="net", reference=net.name,
                                 description=f"0 capacitors, {len(non_caps)} loads (threshold rail_min_loads={min_loads})"),
                    ],
                    suggested_fix="Add bulk capacitance (e.g. 10–100 µF) plus per-IC 100 nF decoupling on this rail.",
                    confidence=0.85,
                )
            )
        return findings
