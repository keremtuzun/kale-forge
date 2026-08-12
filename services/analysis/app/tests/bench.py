"""Graded benchmark for the design pipeline.

    python -m app.tests.bench            score every case, print the table
    python -m app.tests.bench --json     machine-readable, for tracking over time
    python -m app.tests.bench --only pcb run one slice

The test suite answers "is anything broken". This answers "is it getting better", which is a
different question and the one that matters once the obvious bugs are gone. Partial credit is
the point: a classifier that gets the type right and the shaft diameter wrong scores 0.6, not
zero, so an improvement that fixes half a problem is visible instead of invisible.

Scoring, per case, out of 1.0:

    0.35  design type is right
    0.20  part type is right (mechanical only)
    0.15  season relevance is right
    0.30  every checked parameter is within tolerance

Refusal cases score 1.0 for refusing and 0.0 for building something. Getting those wrong is
the failure mode that matters most, because a confidently wrong part is worse than no part.

Latency is recorded but not scored. It is here because it is the number nobody looks at until
a user does.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

CASES = Path(__file__).with_name("bench_prompts.json")
TOLERANCE = 0.02          # inches; tighter than any dimension a user would type


def _score_case(case: dict) -> dict:
    from app.services.design_router import DesignNotSupported, route
    from app.services.robot_spec import DesignNotBuildable

    started = time.time()
    notes: list[str] = []
    got: dict = {}
    try:
        out = route(case["prompt"], season="2026-rebuilt")
        state = "built"
    except DesignNotSupported:
        out, state = {}, "refused"
    except DesignNotBuildable:
        out, state = {}, "asked"
    except Exception as exc:                                    # noqa: BLE001
        out, state = {}, f"error: {type(exc).__name__}: {exc}"
    ms = int((time.time() - started) * 1000)

    if case.get("expect") == "refused":
        ok = state == "refused"
        return {"id": case["id"], "score": 1.0 if ok else 0.0, "ms": ms,
                "state": state, "notes": [] if ok else ["should have refused, did not"]}

    if state != "built":
        return {"id": case["id"], "score": 0.0, "ms": ms, "state": state,
                "notes": [f"did not build ({state})"]}

    score = 0.0
    got["type"] = out.get("designType")
    # A subsystem prompt answered as a robot is close enough to be useful and wrong enough to
    # note; half credit says so without pretending it passed.
    if got["type"] == case["type"]:
        score += 0.35
    elif {got["type"], case["type"]} <= {"robot", "subsystem"}:
        score += 0.18
        notes.append(f"type {got['type']}, expected {case['type']}")
    else:
        notes.append(f"type {got['type']}, expected {case['type']}")

    if "part" in case:
        got["part"] = out.get("partType")
        if got["part"] == case["part"]:
            score += 0.20
        else:
            notes.append(f"part {got['part']}, expected {case['part']}")
    else:
        score += 0.20

    if "season" in case:
        got["season"] = bool((out.get("seasonRelevant") or {}).get("relevant"))
        if got["season"] == case["season"]:
            score += 0.15
        else:
            notes.append(f"season relevance {got['season']}, expected {case['season']}")
    else:
        score += 0.15

    expected = case.get("params") or {}
    if expected:
        params = out.get("parameters") or {}
        hits = 0
        for key, want in expected.items():
            have = params.get(key)
            if isinstance(have, (int, float)) and abs(float(have) - float(want)) <= TOLERANCE:
                hits += 1
            else:
                notes.append(f"{key}={have}, expected {want}")
        score += 0.30 * hits / len(expected)
    else:
        score += 0.30

    # Structural expectations that are not scored dimensions but are still failures.
    floor = case.get("min_parts")
    if floor:
        n = (out.get("cad") or {}).get("feature_total", 0)
        got["parts"] = n
        if n < floor:
            score *= 0.5
            notes.append(f"{n} parts, expected at least {floor}")
    if case.get("placed"):
        layout = out.get("layout") or {}
        bad = [c["check"] for c in layout.get("checks", []) if not c["ok"]]
        got["placed"] = len(layout.get("placements", []))
        if bad:
            score *= 0.6
            notes.append("placement fails: " + ", ".join(bad[:2]))
    if case.get("routed"):
        routing = out.get("routing") or {}
        drc = out.get("drc") or []
        got["traces"] = len(routing.get("traces") or [])
        bad = [c["check"] for c in drc if not c["ok"]]
        if not routing.get("complete"):
            score *= 0.6
            notes.append("did not route: " + ", ".join(routing.get("unrouted", [])[:2]))
        elif bad:
            score *= 0.8
            notes.append("DRC: " + ", ".join(bad[:2]))
    if case.get("min_bores"):
        # A hole the user asked for and did not get is a wrong part, and it is invisible in
        # the parameter table — so the check has to look at the geometry.
        holes = sum(len(f.get("bores") or [])
                    for a in (out.get("cad") or {}).get("assemblies", [])
                    for f in a["features"])
        got["bores"] = holes
        if holes < case["min_bores"]:
            score *= 0.6
            notes.append(f"{holes} holes, expected at least {case['min_bores']}")
    if case.get("min_bodies"):
        n = (out.get("cad") or {}).get("feature_total", 0)
        got["bodies"] = n
        if n < case["min_bodies"]:
            score *= 0.6
            notes.append(f"{n} bodies, expected at least {case['min_bodies']}")
    if case.get("min_nets"):
        n = len(out.get("nets") or [])
        got["nets"] = n
        if n < case["min_nets"]:
            score *= 0.7
            notes.append(f"{n} nets, expected at least {case['min_nets']}")

    return {"id": case["id"], "score": round(min(score, 1.0), 3), "ms": ms,
            "state": state, "got": got, "notes": notes}


def run(only: str = "") -> dict:
    data = json.loads(CASES.read_text(encoding="utf-8"))
    cases = [c for c in data["cases"] if not only or only in c["id"]]
    results = [_score_case(c) for c in cases]

    groups: dict[str, list[float]] = {}
    for case, result in zip(cases, results):
        key = case.get("expect") or case.get("type") or "other"
        groups.setdefault(key, []).append(result["score"])

    total = sum(r["score"] for r in results)
    return {
        "version": data["version"],
        "cases": len(results),
        "score": round(total / len(results), 4) if results else 0.0,
        "perfect": sum(1 for r in results if r["score"] >= 0.999),
        "zero": sum(1 for r in results if r["score"] <= 0.001),
        "median_ms": sorted(r["ms"] for r in results)[len(results) // 2] if results else 0,
        "slowest_ms": max((r["ms"] for r in results), default=0),
        "by_group": {k: round(sum(v) / len(v), 3) for k, v in sorted(groups.items())},
        "results": results,
    }


def main(argv: list[str]) -> int:
    only = ""
    if "--only" in argv:
        only = argv[argv.index("--only") + 1]
    report = run(only)

    if "--json" in argv:
        print(json.dumps(report, indent=1))
        return 0

    print(f"\nKale bench {report['version']}  {report['cases']} cases\n")
    for r in report["results"]:
        mark = "ok " if r["score"] >= 0.999 else ("   " if r["score"] > 0 else "XX ")
        print(f"  {mark}{r['score']:.2f}  {r['ms']:>6} ms  {r['id']}")
        for note in r["notes"]:
            print(f"              {note}")
    print(f"\n  score      {report['score']:.3f}")
    print(f"  perfect    {report['perfect']}/{report['cases']}")
    print(f"  zero       {report['zero']}")
    print(f"  latency    {report['median_ms']} ms median, {report['slowest_ms']} ms worst")
    for group, value in report["by_group"].items():
        print(f"  {group:<10} {value:.3f}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
