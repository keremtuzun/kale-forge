"""Rule discovery and execution. Rules live one-per-file under
app/rules/{connectivity,components,power,pcb}/ and subclass Rule.

Adding a rule = adding a file. No central list to edit.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil

from app.models.normalized import NormalizedProject
from app.rules.base import RULE_ENGINE_VERSION, Rule, RuleContext, RuleFinding, SEVERITY_ORDER
from app.rules.helpers import NetGraph

logger = logging.getLogger(__name__)

_CATEGORY_PACKAGES = [
    "app.rules.connectivity",
    "app.rules.components",
    "app.rules.power",
    "app.rules.pcb",
]

_rules_cache: list[Rule] | None = None


def discover_rules(force: bool = False) -> list[Rule]:
    global _rules_cache
    if _rules_cache is not None and not force:
        return _rules_cache
    rules: list[Rule] = []
    seen_ids: set[str] = set()
    for package_name in _CATEGORY_PACKAGES:
        try:
            package = importlib.import_module(package_name)
        except ModuleNotFoundError:
            logger.warning("rule package %s missing", package_name)
            continue
        for mod_info in pkgutil.iter_modules(package.__path__):
            try:
                module = importlib.import_module(f"{package_name}.{mod_info.name}")
            except Exception:  # noqa: BLE001 — one broken rule file must not disable the engine
                logger.exception("failed to import rule module %s.%s", package_name, mod_info.name)
                continue
            for _name, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, Rule) and obj is not Rule and not inspect.isabstract(obj):
                    if obj.__module__ != module.__name__:
                        continue  # imported, not defined here
                    rule = obj()
                    if not rule.id:
                        raise ValueError(f"rule {obj.__name__} has no id")
                    if rule.id in seen_ids:
                        raise ValueError(f"duplicate rule id {rule.id}")
                    seen_ids.add(rule.id)
                    rules.append(rule)
    rules.sort(key=lambda r: r.id)
    _rules_cache = rules
    return rules


def run_rules(
    project: NormalizedProject,
    config: dict | None = None,
) -> list[RuleFinding]:
    """Run every applicable rule. A crashing rule is logged and skipped — one bad rule
    must never take down an analysis."""
    ctx = RuleContext(config=config or {})
    ctx.graph = NetGraph(project)
    findings: list[RuleFinding] = []
    for rule in discover_rules():
        try:
            if not rule.applies(project):
                continue
            findings.extend(rule.check(project, ctx))
        except Exception:  # noqa: BLE001 — isolation is the point
            logger.exception("rule %s failed", rule.id)
    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.rule_id))
    return findings


def engine_version() -> str:
    return RULE_ENGINE_VERSION
