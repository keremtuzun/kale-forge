"""HTML design-review report generation (printable; PDF via browser print)."""
from __future__ import annotations

from datetime import datetime, timezone

from jinja2 import Environment, select_autoescape

DISCLAIMER = (
    "Kale Forge provides automated design-review assistance and may miss errors or produce "
    "incorrect recommendations. It is not a substitute for professional electrical engineering "
    "review, laboratory testing, simulation, regulatory certification, or manufacturer design "
    "guidance."
)

_SAFETY_KEYWORDS = {
    "mains": ["mains", "230v", "240v", "120v", "110v", "ac line", "line voltage"],
    "high voltage": ["hv", "high voltage", "kv"],
    "lithium battery": ["lipo", "li-ion", "lithium", "18650", "battery pack"],
    "medical": ["medical", "ecg", "eeg", "patient", "defibrill"],
    "automotive": ["automotive", "can bus", "obd", "12v auto", "24v auto"],
    "aerospace": ["aerospace", "avionics", "do-160"],
    "RF power": ["rf power", "amplifier pa", "transmitter"],
}

_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Kale Forge Review — {{ project.name }}</title>
<style>
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; color: #1a1a1a; margin: 2rem; line-height: 1.5; }
  h1 { border-bottom: 3px solid #2f7d4f; padding-bottom: .3rem; }
  h2 { margin-top: 2rem; color: #2f7d4f; border-bottom: 1px solid #ddd; }
  table { border-collapse: collapse; width: 100%; margin: .5rem 0; font-size: .9rem; }
  th, td { border: 1px solid #ccc; padding: .35rem .5rem; text-align: left; vertical-align: top; }
  th { background: #f2f7f4; }
  .sev-critical { color: #b00020; font-weight: 700; }
  .sev-error { color: #d9480f; font-weight: 600; }
  .sev-warning { color: #b8860b; }
  .sev-info { color: #555; }
  .disclaimer { background: #fff8e1; border: 1px solid #e0c060; padding: 1rem; margin: 1rem 0; border-radius: 6px; }
  .safety { background: #fde8e8; border: 2px solid #b00020; padding: 1rem; margin: 1rem 0; border-radius: 6px; }
  .meta { color: #666; font-size: .85rem; }
  .prov { font-size: .75rem; color: #777; }
  code { background: #f4f4f4; padding: 0 .2rem; }
  @media print { body { margin: 1rem; } h2 { page-break-after: avoid; } table { page-break-inside: avoid; } }
</style></head><body>
<h1>Kale Forge Design-Review Report</h1>
<p class="meta">Project: <strong>{{ project.name }}</strong> &middot; Generated {{ generated_at }} &middot;
Model version: <code>{{ model_version }}</code> &middot; Rule-engine version: <code>{{ rule_engine_version }}</code></p>

<div class="disclaimer"><strong>Disclaimer.</strong> {{ disclaimer }}</div>
{% if safety_flags %}
<div class="safety"><strong>Elevated-risk design detected ({{ safety_flags|join(', ') }}).</strong>
This report must not be treated as a safety or compliance assessment. Kale Forge does not certify
compliance with UL, CE, FCC, IEC, ISO, IPC, automotive, or medical standards. Have a qualified
professional review this design before use.</div>
{% endif %}

<h2>Summary</h2>
<p>{{ ai_summary }}</p>
<table>
<tr><th>Components</th><th>Critical</th><th>Errors</th><th>Warnings</th><th>Nets</th></tr>
<tr><td>{{ counts.components }}</td><td class="sev-critical">{{ counts.critical }}</td>
<td class="sev-error">{{ counts.error }}</td><td class="sev-warning">{{ counts.warning }}</td>
<td>{{ counts.nets }}</td></tr>
</table>

<h2>Uploaded Files</h2>
<ul>{% for f in files %}<li><code>{{ f }}</code></li>{% endfor %}</ul>

<h2>Component Inventory</h2>
<table><tr><th>Ref</th><th>Value</th><th>Footprint</th><th>MPN</th></tr>
{% for c in components %}<tr><td>{{ c.reference }}</td><td>{{ c.value }}</td>
<td>{{ c.footprint }}</td><td>{{ c.mpn or '' }}</td></tr>{% endfor %}</table>

<h2>Critical Findings &amp; Errors</h2>
{% if critical_findings %}<table><tr><th>Rule</th><th>Severity</th><th>Title</th><th>Affected</th><th>Fix</th></tr>
{% for f in critical_findings %}<tr><td><code>{{ f.rule_id }}</code></td>
<td class="sev-{{ f.severity }}">{{ f.severity }}</td><td>{{ f.title }}</td>
<td>{{ (f.affected_components + f.affected_nets)|join(', ') }}</td><td>{{ f.suggested_fix }}</td></tr>{% endfor %}
</table>{% else %}<p>No critical or error-level findings.</p>{% endif %}

<h2>Warnings</h2>
{% if warnings %}<table><tr><th>Rule</th><th>Title</th><th>Affected</th></tr>
{% for f in warnings %}<tr><td><code>{{ f.rule_id }}</code></td><td>{{ f.title }}</td>
<td>{{ (f.affected_components + f.affected_nets)|join(', ') }}</td></tr>{% endfor %}</table>
{% else %}<p>No warnings.</p>{% endif %}

<h2>Power Analysis</h2>
{% if power_tree %}
<table><tr><th>Rail</th><th>Voltage</th><th>Est. current</th><th>Loads</th></tr>
{% for r in power_tree.rails %}<tr><td>{{ r.net }}</td>
<td>{% if r.voltage_v is not none %}{{ '%.2f'|format(r.voltage_v) }} V{% else %}?{% endif %}</td>
<td>{% if r.total_current_ma is not none %}{{ '%.0f'|format(r.total_current_ma) }} mA{% else %}?{% endif %}</td>
<td>{{ r.load_refs|join(', ') }}</td></tr>{% endfor %}</table>
<h3 class="prov">Calculations (with formulas &amp; assumptions)</h3>
{% for calc in power_tree.calculations %}
<p><strong>{{ calc.name }}</strong>:
{% if calc.value is not none %}{{ '%.3g'|format(calc.value) }} {{ calc.unit }}{% else %}not computed{% endif %}<br>
<span class="prov">Formula: {{ calc.formula }}<br>
{% if calc.assumptions %}Assumptions: {{ calc.assumptions|join('; ') }}<br>{% endif %}
{% if calc.missing %}Missing: {{ calc.missing|join('; ') }}<br>{% endif %}
Uncertainty: {{ calc.uncertainty }}</span></p>
{% endfor %}
{% else %}<p>No power tree available (schematic power information insufficient).</p>{% endif %}

<h2>Kale Forge Recommendations</h2>
{% if recommendations %}<ul>
{% for r in recommendations %}<li><strong>{{ r.title }}</strong>: {{ r.detail }}
{% if r.components or r.nets %}<span class="prov">({{ (r.components + r.nets)|join(', ') }})</span>{% endif %}</li>{% endfor %}
</ul>{% else %}<p>No additional recommendations.</p>{% endif %}

{% if possible_findings %}
<h2>Possible Findings (model hypotheses — not confirmed by rules)</h2>
<ul>{% for p in possible_findings %}<li>{{ p.title }}: {{ p.detail }}</li>{% endfor %}</ul>
{% endif %}

<h2>Assumptions &amp; Limitations</h2>
<ul>
{% for a in assumptions %}<li>{{ a }}</li>{% endfor %}
{% for l in limitations %}<li>{{ l }}</li>{% endfor %}
</ul>

<h2>Evidence Provenance</h2>
<p class="prov">Findings above are produced by Kale Forge's deterministic rule engine
(version {{ rule_engine_version }}). AI review content, where present, is generated by the
self-hosted Kale review model (version {{ model_version }}, provider {{ provider }}) and is
schema-validated with a citation gate. Rule findings and AI findings are kept distinct.</p>

<p class="meta">Report generated by Kale Forge on {{ generated_at }}.</p>
</body></html>"""


def _safety_flags(normalized: dict) -> list[str]:
    flags: set[str] = set()
    haystack = " ".join(
        [normalized.get("project", {}).get("name", "")]
        + [f"{c.get('value', '')} {c.get('lib_id', '')}" for c in normalized.get("components", [])]
        + [n.get("name", "") for n in normalized.get("nets", [])]
    ).lower()
    for net in normalized.get("nets", []):
        v = net.get("inferred_voltage")
        if v is not None and abs(v) >= 48:
            flags.add("high voltage")
    for label, keywords in _SAFETY_KEYWORDS.items():
        if any(kw in haystack for kw in keywords):
            flags.add(label)
    return sorted(flags)


def generate_report_html(project_row, normalized: dict, findings: list[dict],
                         ai_review: dict, power_tree: dict | None) -> str:
    env = Environment(autoescape=select_autoescape(["html"]))
    template = env.from_string(_TEMPLATE)

    crit = [f for f in findings if f["severity"] in ("critical", "error")]
    warns = [f for f in findings if f["severity"] == "warning"]

    return template.render(
        project={"name": project_row.name},
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        model_version=project_row.model_version or "n/a",
        provider=(ai_review or {}).get("_provider", "n/a"),
        rule_engine_version=project_row.rule_engine_version or "n/a",
        disclaimer=DISCLAIMER,
        safety_flags=_safety_flags(normalized),
        ai_summary=(ai_review or {}).get("summary", "No AI summary available."),
        counts={
            "components": len(normalized.get("components", [])),
            "nets": len(normalized.get("nets", [])),
            "critical": project_row.critical_count,
            "error": project_row.error_count,
            "warning": project_row.warning_count,
        },
        files=normalized.get("project", {}).get("files", []),
        components=normalized.get("components", []),
        critical_findings=crit,
        warnings=warns,
        power_tree=power_tree,
        recommendations=(ai_review or {}).get("recommendations", []),
        possible_findings=(ai_review or {}).get("possible_findings", []),
        assumptions=(power_tree or {}).get("assumptions", []),
        limitations=(ai_review or {}).get("limitations", []),
    )
