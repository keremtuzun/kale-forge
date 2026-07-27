"""PWR-008: estimated rail current versus connector rating (estimate data required)."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_connector


class ConnectorCurrentRule(Rule):
    id = "PWR-008"
    version = "1.0.0"
    category = RuleCategory.POWER
    title = "Estimated current may exceed connector rating"
    description = "Sum of load estimates through a supply connector compared against its rated current."
    default_severity = Severity.WARNING
    source = "user-provided current estimates and connector ratings"

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        estimates: dict[str, float] = ctx.config.get("current_estimates_ma", {})
        if not estimates:
            return []  # no data — skip silently rather than guess
        ratings: dict[str, float] = ctx.config.get("connector_rating_a", {})
        default_rating = float(ctx.threshold("default_connector_rating_a", 1.0))
        findings: list[RuleFinding] = []
        for comp in project.components:
            if not is_connector(comp):
                continue
            power_nets = [
                p.net for p in comp.pins
                if p.net and (n := ctx.graph.nets.get(p.net)) is not None and n.is_power
            ]
            for net_name in power_nets:
                loads = [
                    (ref, estimates[ref])
                    for ref in ctx.graph.components_on_net(net_name)
                    if ref != comp.reference and ref in estimates
                ]
                if not loads:
                    continue
                total_ma = sum(ma for _, ma in loads)
                rating_a = float(ratings.get(comp.reference, default_rating))
                if total_ma / 1000.0 <= rating_a:
                    continue
                formula = " + ".join(f"{ref}:{ma:g}mA" for ref, ma in loads)
                findings.append(
                    self.finding(
                        description=(
                            f"Connector {comp.reference} feeds '{net_name}' with an estimated "
                            f"{total_ma / 1000.0:.2f} A, above its rating of {rating_a:g} A."
                        ),
                        affected_components=[comp.reference] + [ref for ref, _ in loads],
                        affected_nets=[net_name],
                        evidence=[
                            Evidence(kind="calculation",
                                     description=f"I_total = Σ I_load = {formula} = {total_ma:g} mA; "
                                                 f"rating {rating_a:g} A "
                                                 f"({'per-connector config' if comp.reference in ratings else 'default_connector_rating_a config default'}); "
                                                 "estimates are user-provided/inferred, not measured"),
                        ],
                        suggested_fix="Use a higher-rated connector, split the load across pins, or reduce load.",
                        confidence=0.5,
                    )
                )
        return findings
