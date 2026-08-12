"""One entry point for every kind of design Kale Forge makes.

    request -> intent -> engineering spec -> engine -> validation -> design result

The engines are behind one interface on purpose. Before this, the studio had a single path
that assumed a robot, and every new artifact type would have been another branch inside a
function that was already the longest in the codebase. Here, adding an engine is adding one
entry to `_ENGINES`; the studio, the viewer and the exports do not change.

Every result carries `designType`, so downstream code stops asking "what fields does a robot
have" and starts asking "what does THIS artifact have".

Stdlib-only: imported by the Vercel function.
"""
from __future__ import annotations

from typing import Any, Callable

from app.services import engineering_calc as calc
from app.services import mech_primitives, mech_spec
from app.services.cad_contract import (
    clearance_report, editable_manifest, fidelity_report, normalize_cad,
    require_valid_cad, structural_report)
from app.services.design_intent import (
    ASSUMED, EngineeringSpec, INTENT_VERSION, TYPE_LABELS, classify, season_is_relevant)
from app.services.frc_cad import CAD_VERSION

ROUTER_VERSION = "kale-router-1.0"


class DesignNotSupported(Exception):
    """The request was understood and this compiler cannot build it.

    Deliberately distinct from "we need more information": a user who asked for a
    four-bar linkage should be told this library does not make one, not asked what colour.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(payload.get("message", "unsupported"))
        self.payload = payload


def _cad_envelope(assemblies: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap assemblies in the CAD document shape the rest of the pipeline expects.

    Same document as a robot's, so the viewer, the contract, the exports and the fidelity
    report cannot tell the difference between one part and four hundred — which is the point.
    """
    counts: dict[str, int] = {}
    total = 0
    for asm in assemblies:
        for feature in asm["features"]:
            counts[feature["t"]] = counts.get(feature["t"], 0) + int(
                (feature.get("rep") or {}).get("n", 1))
            total += 1
    span = [0.0, 0.0, 0.0]
    for asm in assemblies:
        for feature in asm["features"]:
            size = feature.get("size") or []
            for i in range(min(3, len(size))):
                span[i] = max(span[i], abs(float(size[i])))
    return {
        "version": CAD_VERSION, "units": "in",
        "frame_of_reference": ("origin at the part's own datum; +X right, +Y up, "
                               "-Z toward the front"),
        "assemblies": assemblies, "feature_counts": counts, "feature_total": total,
        "envelope_in": [round(v, 2) for v in span],
        "caveat": ("Dimensioned concept geometry. Dimensions are consistent with each other "
                   "and with the parts catalog; nothing here has been checked against a "
                   "vendor drawing or a stress case. Do not machine from it without review."),
    }


def _provenance_rows(spec: EngineeringSpec) -> list[dict[str, Any]]:
    """Every resolved value with where it came from, ordered so the shakiest are visible.

    This is the table that makes a generated part trustworthy: a user can see at a glance
    which numbers they gave, which were derived and which this compiler chose for them.
    """
    rows: list[dict[str, Any]] = []
    for bucket in (spec.requirements, spec.calculated, spec.inferred, spec.assumptions):
        for key, entry in bucket.items():
            rows.append({"key": key, "label": key.replace("_in", "").replace("_", " "),
                         "value": entry["value"], "unit": entry.get("unit", ""),
                         "source": entry["source"], "note": entry.get("note", "")})
    for gap in spec.unresolved:
        rows.append({"key": gap["key"], "label": gap["key"].replace("_", " "),
                     "value": None, "unit": "", "source": gap["source"],
                     "note": gap["why"], "critical": gap["critical"]})
    return rows


# ── mechanical engine ────────────────────────────────────────────────────────
def _mechanical(prompt: str, intent: dict[str, Any], **_: Any) -> dict[str, Any]:
    """One part, or a small assembly of parts. Never a robot."""
    spec = mech_spec.resolve(prompt, intent["design_type"])

    critical = spec.critical_gaps
    if critical:
        raise DesignNotSupported({
            "state": "unsupported",
            "designType": intent["design_type"],
            "message": ("Kale can generate a part from its primitive library, and nothing in "
                        "this request names one of them."),
            "supported": list(mech_primitives.SUPPORTED_PART_TYPES),
            "questions": [g["why"] for g in critical],
        })

    built = mech_primitives.build(spec.part_type, spec)
    if built is None:
        raise DesignNotSupported({
            "state": "unsupported", "designType": intent["design_type"],
            "message": f"No generator for '{spec.part_type}' yet.",
            "supported": list(mech_primitives.SUPPORTED_PART_TYPES),
        })

    assembly = built["assembly"]
    cad = require_valid_cad(normalize_cad(_cad_envelope([assembly])))

    # Mass from the geometry that was actually emitted, not from the nominal block: a plate
    # with a bearing pocket and four holes in it does not weigh what the block weighed.
    material = str(spec.get("material") or "6061-T6 aluminium")
    volume = 0.0
    counted = 0
    skipped: dict[str, int] = {}
    for feature in assembly["features"]:
        if feature["t"] == "plate":
            size = feature["size"]
            volume += calc.plate_volume_in3(size[0], size[1], size[2], feature.get("bores"))
            counted += 1
        elif feature["t"] == "shaft":
            volume += calc.volume_cylinder_in3(float(feature.get("dia") or 0),
                                               float(feature.get("len") or 0))
            counted += 1
        else:
            skipped[feature["t"]] = skipped.get(feature["t"], 0) + 1
    # What the mass covers is stated with the mass. A roller's number that silently left out
    # the rollers would be a wrong weight presented as a right one — and the parts left out
    # are bought, not made, so the omission is deliberate rather than a gap.
    excluded = ", ".join(f"{n} {kind}" for kind, n in sorted(skipped.items()))
    mass = calc.mass_lb(volume, material)
    if mass is not None:
        note = f"{volume:.3f} in^3 of {material} with the holes removed"
        if excluded:
            note += f"; excludes the catalog parts on it ({excluded})"
        spec.calc("mass_lb", round(mass, 4), "lb", note)
    else:
        spec.unknown("mass_lb", f"no density on file for {material}")

    return {
        "designType": intent["design_type"],
        "partType": spec.part_type,
        "name": assembly["name"],
        "engine": "mechanical",
        "cad": cad,
        "parameters": built["parameters"],
        "dimensions": built["dimensions"],
        "hardware": built["hardware"],
        "material": {"name": material, "source": spec.source_of("material"),
                     "density_lb_in3": calc.density(material)},
        "process": {"name": spec.get("process"), "source": spec.source_of("process")},
        "mass": {"value": round(mass, 4) if mass is not None else None, "unit": "lb",
                 "source": "CALCULATED" if mass is not None else "UNRESOLVED",
                 "covers": f"{counted} machined "
                           f"{'body' if counted == 1 else 'bodies'}",
                 "excludes": excluded},
        "interfaces": spec.interfaces or _interfaces_for(spec),
        "spec": spec.to_dict(),
        "provenance": _provenance_rows(spec),
        "integrity": structural_report(cad),
        "clearance": clearance_report(cad),
        "fidelity": fidelity_report(cad),
        "editable_manifest": editable_manifest(cad),
        "risks": _risks(spec),
    }


def _interfaces_for(spec: EngineeringSpec) -> list[dict[str, Any]]:
    """What this part touches. Stated as interfaces rather than prose because that is the
    thing a user has to check against the rest of their robot."""
    out: list[dict[str, Any]] = []
    shaft = spec.get("shaft_size_in")
    if shaft:
        out.append({"name": "shaft", "detail": f"{shaft:g} in {spec.get('shaft_form') or 'hex'}",
                    "source": spec.source_of("shaft_size_in")})
    od = spec.get("bearing_od_in")
    if od and spec.part_type in ("bearing_block", "bearing_housing", "gearbox_plate"):
        out.append({"name": "bearing", "detail": f"{od:g} in OD",
                    "source": spec.source_of("bearing_od_in")})
    fastener = spec.get("fastener")
    if fastener:
        out.append({"name": "mounting", "detail": f"{fastener} screws",
                    "source": spec.source_of("fastener")})
    tube = spec.get("tube_section_in")
    if tube:
        out.append({"name": "tube", "detail": f"{tube[0]:g}x{tube[1]:g} in",
                    "source": spec.source_of("tube_section_in")})
    return out


def _risks(spec: EngineeringSpec) -> list[dict[str, Any]]:
    """What could be wrong with this part, said plainly.

    Every assumption that controls whether the part FITS is a risk, because an assumption a
    user does not read is a part that does not go together.
    """
    risks: list[dict[str, Any]] = []
    for key, entry in spec.assumptions.items():
        if key in ("material", "process", "fastener"):
            continue
        risks.append({"severity": "check",
                      "detail": f"{key.replace('_in', '').replace('_', ' ')} was assumed "
                                f"({entry['value']}{' ' + entry.get('unit', '') if entry.get('unit') else ''})"
                                f" — {entry.get('note', 'no basis stated')}"})
    for gap in spec.unresolved:
        risks.append({"severity": "critical" if gap["critical"] else "unresolved",
                      "detail": gap["why"]})
    if spec.part_type in ("bearing_block", "bearing_housing"):
        risks.append({"severity": "check",
                      "detail": "the pocket is cut to a workshop fit allowance, not an ISO "
                                "fit class — take a test cut before committing the part"})
    return risks


# ── robot engine (existing, wrapped) ─────────────────────────────────────────
def _robot(prompt: str, intent: dict[str, Any], *, season: str = "",
           use_model: bool = False, **_: Any) -> dict[str, Any]:
    """The existing robot compiler, unchanged, with a design type stamped on it.

    Imported lazily so a bearing block never pays for loading the robot compiler, and so a
    failure in robot code cannot stop a part being generated.
    """
    from app.services.robot_spec import build_robot_spec  # noqa: PLC0415

    spec = build_robot_spec(prompt, use_model=use_model, season=season)
    spec["designType"] = intent["design_type"]
    spec["engine"] = "robot"
    return spec


def _reads_as_a_robot(intent: dict[str, Any]) -> bool:
    """Is there robot evidence in this request, even though it did not win?

    Deliberately narrow. "Design a four bar linkage" has no robot evidence at all and must
    stay an honest refusal; "an algae processor bot" has some, and is a robot.
    """
    scores = intent.get("scores") or {}
    return (scores.get("robot", 0) > 0 or scores.get("subsystem", 0) > 0)


def _pcb(prompt: str, intent: dict[str, Any], **_: Any) -> dict[str, Any]:
    from app.services.pcb_design import generate_board  # noqa: PLC0415

    out = generate_board(prompt)
    out["designType"] = intent["design_type"]
    out["engine"] = "pcb"

    # A placed board is geometry, so it goes through the same CAD document and the same
    # validator as everything else. That is what puts a PCB in the 3D viewer and the STEP
    # export: neither of them had to learn what a PCB is.
    assembly = out.pop("cad_assembly", None)
    if assembly:
        cad = require_valid_cad(normalize_cad(_cad_envelope([assembly])))
        out["cad"] = cad
        out["integrity"] = structural_report(cad)
        out["editable_manifest"] = editable_manifest(cad)
    return out


# Subsystems and assemblies still run through the robot compiler, which is where the
# elevator, intake and shooter generators live. That is reuse, not a stopgap: those
# generators are good, and the difference is that the result is now labelled as a subsystem
# rather than pretending the whole robot was asked for.
_ENGINES: dict[str, Callable[..., dict[str, Any]]] = {
    "robot": _robot,
    "subsystem": _robot,
    "mechanical_part": _mechanical,
    "mechanical_assembly": _mechanical,
    "enclosure": _mechanical,
    "other": _mechanical,
    "pcb": _pcb,
    "electronics": _pcb,
}


def route(prompt: str, *, requested_type: str = "auto", season: str = "",
          use_model: bool = False) -> dict[str, Any]:
    """Classify, generate, and hand back one design result.

    The result always carries `designType`, `intent` and `seasonRelevant`, whatever engine
    produced it, so the studio can render the right dossier without sniffing for robot keys.
    """
    intent = classify(prompt, requested_type)
    relevance = season_is_relevant(prompt, intent["design_type"])
    engine = _ENGINES.get(intent["design_type"], _mechanical)

    # A season only reaches the robot compiler when it is relevant. Passing one to a bearing
    # block would be harmless and meaningless; passing one to a subsystem that does not touch
    # the game piece silently constrains a part that no rule applies to.
    try:
        result = engine(prompt, intent,
                        season=season if relevance["relevant"] else "",
                        use_model=use_model)
    except DesignNotSupported:
        # Compatibility guard, and the closest thing this app has to a migration. A saved
        # design IS its prompt chain — the studio owns no database — so the classifier
        # re-derives the type on every load, and a misread prompt would take a working design
        # away from its owner. When the request carries robot evidence but names no part this
        # library can make, it is a robot that was read slightly wrong, not a refusal.
        if requested_type in ("", "auto") and not _reads_as_a_robot(intent):
            raise
        if requested_type not in ("", "auto") and requested_type not in ("robot", "subsystem"):
            raise
        intent = dict(intent, design_type="robot", confidence=round(intent["confidence"], 2),
                      why=(intent["why"] + "; no part in the library matches, and the request "
                                           "reads as a robot"))
        relevance = season_is_relevant(prompt, "robot")
        result = _robot(prompt, intent, season=season, use_model=use_model)
    result.setdefault("designType", intent["design_type"])
    result["designTypeLabel"] = TYPE_LABELS.get(result["designType"], result["designType"])
    result["intent"] = intent
    result["seasonRelevant"] = relevance
    result["routerVersion"] = ROUTER_VERSION
    result["intentVersion"] = INTENT_VERSION
    return result


__all__ = ["route", "DesignNotSupported", "ROUTER_VERSION"]
