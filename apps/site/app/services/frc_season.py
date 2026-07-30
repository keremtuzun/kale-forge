"""Season model: the field, the rules and the design constraints they imply.

Everything upstream of this module knows how to build *a* robot.  This module is what makes
it a robot for a particular game.  A season here is four things:

1. **The field**, as dimensioned elements — how high the goal opening is, how tall the
   obstacle you drive under is, where the climb rungs sit.  These are the numbers a
   mechanism is actually sized against.
2. **The scoring table and the ranking bonuses**, so a strategy answer can be arithmetic
   instead of an opinion: what a climb is worth against a cycle, how many gamepieces a
   ranking point costs.
3. **The construction rules that differ between seasons** — perimeter budget, starting
   height, extension allowance, propulsion motor count.  These are the constraints that make
   the *same* prompt produce a different robot in a different year, and getting them wrong is
   an inspection failure rather than a preference.
4. **Derived design targets**, computed from 1–3 rather than stored: the shot a shooter has
   to make, the height a climber has to reach, the envelope that fits under an obstacle.

Deriving targets instead of listing them is the whole point.  A model trained on "the 2026
robot is 27 inches wide" has memorised a number; a model trained on "the perimeter budget is
110 inches, so a square frame is at most 27.5 and 28 × 28 fails inspection" has learned the
constraint and can apply it to a season it has never seen.

Field and rule figures are the public published values, restated as engineering inputs.  They
are a starting point for sizing, not a compliance statement: rules change by team update
during a season, and the manual is the only authority.
"""
from __future__ import annotations

import math
from typing import Any

SEASON_VERSION = "kale-seasons-2026.07.28"

_G_FPS2 = 32.174  # ft/s², for the projectile solutions below


# ─────────────────────────────────────────────────────────────────────────────
# The seasons
# ─────────────────────────────────────────────────────────────────────────────
# `key` is stable and is what the API, the UI and the training corpus all pass around.
# Legacy profile ids are kept as aliases so existing designs keep resolving.
SEASONS: dict[str, dict[str, Any]] = {
    "2026-rebuilt": {
        "key": "2026-rebuilt",
        "year": 2026,
        "game": "REBUILT",
        "label": "2026 REBUILT",
        "selectable": True,
        "summary": ("Two alliances score FUEL into their HUB, cross a BUMP or a TRENCH between "
                    "zones, and climb the TOWER at the end of the match. A HUB only pays while "
                    "it is active, so cycle timing and passing matter as much as raw capacity."),
        "gamepiece": {
            "name": "fuel",
            "form": "high-density foam ball",
            "diameter_in": 5.91,
            "mass_lb": [0.448, 0.500],
            "handling": ("A 5.91 in sphere rolls, nests and is handled in bulk — the design "
                         "problem is throughput and jam-free indexing, not single-piece "
                         "precision. Foam wears: a ball late in an event compresses more "
                         "easily than a fresh one, so a shooter tuned on day one drifts."),
        },
        "field": {
            "carpet_in": [317.7, 651.2],
            "elements": [
                {"name": "HUB", "kind": "goal",
                 "size_in": [47.0, 47.0],
                 "opening_across_in": 41.7, "opening_shape": "hexagonal",
                 "opening_front_edge_in": 72.0,
                 "design_range_ft": 18.0, "long_range_ft": 30.0,
                 "note": ("A 47 × 47 in prism with a 41.7 in hexagonal opening whose front edge "
                          "is 72 in off the carpet. The shot has to arrive above that edge and "
                          "still be descending into the opening. The design and long ranges are "
                          "Kale's sizing assumptions, not field dimensions: 18 ft is a "
                          "comfortable working distance and 30 ft is roughly a cross-zone shot. "
                          "Set them from where your strategy actually shoots.")},
                {"name": "TOWER", "kind": "climb",
                 "size_in": [49.25, 45.0, 78.25],
                 "upright_spacing_in": 32.25, "upright_height_in": 72.1,
                 "rungs_in": {"L1": 27.0, "L2": 45.0, "L3": 63.0},
                 "rung_od_in": 1.66, "rung_spacing_in": 18.0, "rung_overhang_in": 5.875,
                 "support_structure_in": [28.40, 43.38],
                 # The scoring criteria are about where the ROBOT ends up, not what it reaches.
                 # This is the single most important thing to get right about a 2026 climber.
                 "level_criteria": {
                     "L1": "no longer touching the carpet or the TOWER BASE",
                     "L2": "BUMPER covers completely above the LOW RUNG (27 in)",
                     "L3": "BUMPER covers completely above the MID RUNG (45 in)",
                 },
                 "note": ("Two uprights 32.25 in apart carry three 1.66 in OD pipe rungs at 27, "
                          "45 and 63 in, 18 in apart, each overhanging 5.875 in past the upright "
                          "face. Levels are scored on where the ROBOT's BUMPERS are, not on what "
                          "it touches: L2 needs the bumpers entirely above the 27 in rung and L3 "
                          "entirely above the 45 in rung, while contacting a rung or upright. "
                          "The robot hoists its own body up the tower — it does not extend a "
                          "mast, because R107 caps total height at 30 in the whole time. "
                          "Supporting structure crosses from upright to wall between 28.40 and "
                          "43.38 in, which a climbing robot has to clear.")},
                {"name": "DEPOT", "kind": "source",
                 "size_in": [42.0, 27.0],
                 "note": "42 × 27 in bounded by 3 in steel barriers — an intake wider than the "
                         "opening cannot sweep it cleanly."},
                {"name": "BUMP", "kind": "obstacle",
                 "size_in": [73.0, 44.4, 6.513], "ramp_deg": 15.0,
                 "note": ("A 6.513 in crest on 15° ramps. Ground clearance and wheelbase decide "
                          "whether a robot drives over it or beaches on it, and a full hopper "
                          "spills across the crest.")},
                {"name": "TRENCH", "kind": "obstacle",
                 "size_in": [65.65, 47.0, 40.25],
                 "underpass_in": [50.34, 22.25],
                 "note": ("The underpass is 50.34 in wide and 22.25 in tall. A robot that fits "
                          "under it has a second route between zones; one that does not is "
                          "committed to the BUMP.")},
            ],
        },
        "match": {"auto_s": 20, "teleop_s": 140,
                  "note": ("HUBs alternate between active and inactive; all HUBs go active as "
                           "time runs out. Scoring capacity is worth nothing during an inactive "
                           "window, which is what makes passing and stealing real strategies.")},
        "scoring": [
            {"phase": "auto", "action": "FUEL in the active HUB", "points": 1},
            {"phase": "auto", "action": "TOWER level 1 climb", "points": 15,
             "note": "per robot, maximum two robots"},
            {"phase": "teleop", "action": "FUEL in the active HUB", "points": 1},
            {"phase": "teleop", "action": "FUEL in an inactive HUB", "points": 0},
            {"phase": "teleop", "action": "TOWER level 2 climb", "points": 20, "note": "per robot"},
            {"phase": "teleop", "action": "TOWER level 3 climb", "points": 30, "note": "per robot"},
        ],
        # Thresholds rise with event level (Table 6-5), which changes what a robot has to be
        # able to do between a regional and championship — worth stating rather than flattening
        # to the regional number.
        "ranking": [
            {"name": "Win", "rp": 3}, {"name": "Tie", "rp": 1},
            {"name": "ENERGIZED", "rp": 1,
             "threshold": "FUEL in the active HUB: 100 regional / 240 DCMP / 360 CMP"},
            {"name": "SUPERCHARGED", "rp": 1,
             "threshold": "FUEL in the active HUB: 360 regional / 360 DCMP / 500 CMP"},
            {"name": "TRAVERSAL", "rp": 1, "threshold": "50 TOWER points in the match (all levels)"},
        ],
        "rules": {
            "perimeter_in": 110.0, "perimeter_rule": "R104",
            "start_height_in": 30.0, "height_rule": "R104",
            # R107 is the rule that makes 2026 a different design problem from 2025, and it is
            # easy to read past: the 30 in limit is not a starting-configuration limit, it
            # applies for the whole match. Nothing may ever extend above it. The measurement is
            # taken perpendicular to the robot perimeter as if the robot were sitting on a flat
            # floor, which is why a robot hanging 45 in up the TOWER is still legal — its own
            # height has not changed, only its altitude.
            "max_height_in": 30.0, "max_height_rule": "R107",
            "extension_in": 12.0, "extension_rule": "R105",
            "extension_one_direction": True, "extension_direction_rule": "R106",
            "weight_lb": 115.0, "weight_rule": "R103",
            "weight_with_bumpers_lb": 135.0, "bumper_weight_rule": "R408",
            "bumper_zone_in": [2.5, 5.75], "bumper_rule": "R405",
            "propulsion_motors": 4, "propulsion_rule": "R502",
        },
        "frame": (27.0, 27.0),
        "starting_height_in": 29.0,
        "target_weight_lb": 105,
        "default_subsystems": ["intake", "hopper", "shooter", "climber"],
        "archetypes": [
            {"name": "Turreted cycler",
             "shape": "over-bumper intake → spindexer → feeder tower → turret + hooded shooter → L2/L3 climb",
             "why": ("A turret decouples aiming from driving, so the robot can shoot while "
                     "crossing and can pass to a partner without turning. It costs a slew "
                     "bearing, a wire-management path and a zeroing procedure."),
             "against": "Complexity and packaging; a turret that leaves the frame perimeter is illegal."},
            {"name": "Fixed wide shooter",
             "shape": "wide intake → twin-lane hopper → two fixed hooded shooters → climb",
             "why": ("Two lanes double throughput without a turret, and a fixed shooter is far "
                     "simpler to build, wire and tune."),
             "against": "The drivebase has to aim, so every shot costs a rotation and defence hurts more."},
            {"name": "Low-profile trench runner",
             "shape": "under-22.25 in envelope, flat hopper, low shooter, L1/L2 climb",
             "why": ("Fitting under the TRENCH gives a route the tall robots cannot take, which "
                     "is worth more than a few points of capacity in a congested match."),
             "against": "Everything has to live under 22.25 in, which fights hopper volume and shot angle."},
            {"name": "Defender / feeder",
             "shape": "fast drivebase, wide floor intake, shuttling shooter, quick L1 climb",
             "why": ("Denying an active HUB window is worth as much as scoring in it, and "
                     "shuttling fuel to a partner converts your floor time into their points."),
             "against": "Scores little on its own; needs a strong alliance and a good driver."},
        ],
        "verify": ("Figures are the published field and rule values for 2026 REBUILT. Rules move "
                   "by team update during the season — check the current manual and every team "
                   "update before you cut metal or go to inspection."),
    },
    "2025-reefscape": {
        "key": "2025-reefscape",
        "year": 2025,
        "game": "REEFSCAPE",
        "label": "2025 REEFSCAPE",
        "selectable": True,
        "summary": ("Two alliances score CORAL on four REEF levels, ALGAE into the PROCESSOR or "
                    "the BARGE net, and end the match hanging from a CAGE. Scoring height is the "
                    "design problem: L4 is six feet up."),
        "gamepiece": {
            "name": "coral/algae",
            "form": "PVC tube and inflated ball",
            "diameter_in": 16.0,
            "mass_lb": [1.1, 1.8],
            "handling": ("Two pieces with opposite requirements. CORAL is an 11.875 in length of "
                         "4.5 in OD pipe — rigid, oriented, placed precisely. ALGAE is a 16 in "
                         "inflated ball — light, bulky, thrown or dropped. A robot that does both "
                         "well is carrying two mechanisms, not one."),
        },
        "field": {
            "carpet_in": [317.0, 690.0],
            "elements": [
                {"name": "REEF", "kind": "goal",
                 "levels_in": {"L1": 18.0, "L2": 31.875, "L3": 47.625, "L4": 72.0},
                 "note": ("Branch tips at 18, 31.9, 47.6 and 72 in. L4 at six feet is what forces "
                          "an elevator; L1–L3 are reachable with an arm on a short lift.")},
                {"name": "PROCESSOR", "kind": "goal",
                 "opening_in": [28.0, 20.0], "opening_bottom_in": 7.0,
                 "note": "A 28 × 20 in opening starting 7 in off the carpet — a low, close, gentle delivery."},
                {"name": "BARGE", "kind": "goal",
                 "size_in": [350.0, 44.0, 101.0],
                 "note": "The net stands 8 ft 5 in tall; scoring ALGAE into it is a throw, not a placement."},
                {"name": "CAGE", "kind": "climb",
                 "shallow_bottom_in": 3.5, "deep_bottom_in": 29.375,
                 "note": ("Shallow hangs from 3.5 in, deep from 29.4 in. Deep is worth more and "
                          "needs a mechanism that reaches down and pulls the robot up.")},
                {"name": "CORAL STATION", "kind": "source",
                 "opening_in": [76.0, 7.0], "opening_bottom_in": 37.5,
                 "note": "A 76 × 7 in slot 37.5 in up — CORAL arrives sliding, at a known angle."},
            ],
        },
        "match": {"auto_s": 15, "teleop_s": 135, "note": "Leave, then cycle; endgame is the CAGE."},
        "scoring": [
            {"phase": "auto", "action": "Leave the starting line", "points": 3},
            {"phase": "auto", "action": "CORAL on L1 / L2 / L3 / L4", "points": "3 / 4 / 6 / 7"},
            {"phase": "teleop", "action": "CORAL on L1 / L2 / L3 / L4", "points": "2 / 3 / 4 / 5"},
            {"phase": "teleop", "action": "ALGAE in the PROCESSOR", "points": 6},
            {"phase": "teleop", "action": "ALGAE in the BARGE net", "points": 4},
            {"phase": "endgame", "action": "PARK / shallow CAGE / deep CAGE", "points": "2 / 6 / 12"},
        ],
        "ranking": [
            {"name": "Win", "rp": 3}, {"name": "Tie", "rp": 1},
            {"name": "AUTO", "rp": 1, "threshold": "all robots leave and one CORAL scored"},
            {"name": "CORAL", "rp": 1, "threshold": "5 CORAL on each of enough levels"},
            {"name": "BARGE", "rp": 1, "threshold": "14 barge points"},
        ],
        "rules": {
            "perimeter_in": 120.0, "perimeter_rule": "R104",
            "start_height_in": 42.0, "height_rule": "R104",
            "extension_in": 18.0, "extension_rule": "R105",
            "weight_lb": 115.0, "weight_rule": "R103",
            "weight_with_bumpers_lb": 135.0, "bumper_weight_rule": "R408",
            "bumper_zone_in": [2.5, 5.75], "bumper_rule": "R405",
            "propulsion_motors": 4, "propulsion_rule": "R502",
        },
        "frame": (28.0, 28.0),
        "starting_height_in": 41.0,
        "target_weight_lb": 105,
        "default_subsystems": ["intake", "elevator", "arm", "climber"],
        "archetypes": [
            {"name": "L4 coral specialist",
             "shape": "station intake → multi-stage elevator → wristed end effector → deep climb",
             "why": "L4 is the highest-value repeatable score; a robot that owns it sets the match pace.",
             "against": "A six-foot elevator is heavy, tippy and the single hardest thing on the robot."},
            {"name": "Ground-intake cycler",
             "shape": "floor intake → short lift → arm → L2/L3 placement → shallow climb",
             "why": "Picking off the floor removes the queue at the station and keeps cycles short.",
             "against": "Lower point value per piece; needs volume to beat an L4 robot."},
            {"name": "Algae processor bot",
             "shape": "wide algae intake → low delivery chute → processor scoring → deep climb",
             "why": "Processor algae is worth more than net algae and needs no height at all.",
             "against": "Depends on the alliance wanting algae played; easy to defend."},
        ],
        "verify": ("Figures are the published 2025 REEFSCAPE field and rule values, kept so a "
                   "team can compare an off-season design against last year's game. Check the "
                   "2025 manual for anything you intend to build to."),
    },
    "2024-crescendo": {
        "key": "2024-crescendo",
        "year": 2024,
        "game": "CRESCENDO",
        "label": "2024 CRESCENDO",
        "selectable": False,
        "summary": "NOTE shooting into the SPEAKER and AMP, with a chain climb and a trap.",
        "gamepiece": {"name": "note", "form": "foam ring", "diameter_in": 14.0,
                      "mass_lb": [0.5, 0.5],
                      "handling": "A 14 in ring is caught and thrown flat; it tumbles if the two "
                                  "sides of a shooter are not matched."},
        "field": {"carpet_in": [323.0, 651.0], "elements": [
            {"name": "SPEAKER", "kind": "goal", "opening_front_edge_in": 78.0,
             "note": "Opening starts around 6 ft 6 in with a deep hood above it."},
            {"name": "CHAIN", "kind": "climb", "rungs_in": {"chain": 28.0},
             "note": "A hanging chain at roughly 28 in, which swings while you hook it."},
        ]},
        "match": {"auto_s": 15, "teleop_s": 135, "note": ""},
        "scoring": [{"phase": "teleop", "action": "NOTE in the SPEAKER", "points": 2},
                    {"phase": "teleop", "action": "NOTE in the AMP", "points": 1},
                    {"phase": "endgame", "action": "Onstage climb", "points": 3}],
        "ranking": [{"name": "Win", "rp": 2}],
        "rules": {"perimeter_in": 120.0, "perimeter_rule": "R104",
                  "start_height_in": 48.0, "height_rule": "R104",
                  "extension_in": 12.0, "extension_rule": "R105",
                  "weight_lb": 125.0, "weight_rule": "R103",
                  "weight_with_bumpers_lb": 145.0, "bumper_weight_rule": "R408",
                  "bumper_zone_in": [1.0, 7.5], "bumper_rule": "R405",
                  "propulsion_motors": 6, "propulsion_rule": "R502"},
        "frame": (28.0, 28.0),
        "starting_height_in": 47.5,
        "target_weight_lb": 110,
        "default_subsystems": ["intake", "shooter", "arm", "climber"],
        "archetypes": [],
        "verify": "Retained for historical comparison only; build to the current manual.",
    },
    "offseason": {
        "key": "offseason",
        "year": 0,
        "game": "off-season",
        "label": "Off-season / reference",
        "selectable": True,
        "summary": ("No game. A reference chassis for training, prototyping and driver practice, "
                    "sized to the most restrictive recent envelope so it stays useful."),
        "gamepiece": {"name": "generic", "form": "unspecified", "diameter_in": 6.0,
                      "mass_lb": [0.4, 0.6],
                      "handling": "Size mechanisms for the gamepiece you actually have in hand."},
        "field": {"carpet_in": [317.0, 651.0], "elements": []},
        "match": {"auto_s": 15, "teleop_s": 135, "note": ""},
        "scoring": [],
        "ranking": [],
        "rules": {"perimeter_in": 110.0, "perimeter_rule": "R104",
                  "start_height_in": 30.0, "height_rule": "R104",
                  "extension_in": 12.0, "extension_rule": "R105",
                  "weight_lb": 115.0, "weight_rule": "R103",
                  "weight_with_bumpers_lb": 135.0, "bumper_weight_rule": "R408",
                  "bumper_zone_in": [2.5, 5.75], "bumper_rule": "R405",
                  "propulsion_motors": 4, "propulsion_rule": "R502"},
        "frame": (27.0, 27.0),
        "starting_height_in": 29.0,
        "target_weight_lb": 105,
        "default_subsystems": ["intake", "shooter", "arm", "climber"],
        "archetypes": [],
        "verify": "No game rules apply; build to whatever manual you will be inspected against.",
    },
}

# Legacy profile ids used by designs and corpora generated before seasons existed.
ALIASES: dict[str, str] = {
    "2026-low-profile": "2026-rebuilt",
    "2025-reefscape": "2025-reefscape",
    "2024-note-shooter": "2024-crescendo",
    "reference-bare-swerve": "offseason",
    "2026": "2026-rebuilt", "2025": "2025-reefscape", "2024": "2024-crescendo",
    "rebuilt": "2026-rebuilt", "reefscape": "2025-reefscape", "crescendo": "2024-crescendo",
    "auto": "", "": "",
}

SELECTABLE = [key for key, season in SEASONS.items() if season["selectable"]]
DEFAULT_SEASON = "2026-rebuilt"


def get_season(key: str) -> dict[str, Any]:
    """The season for a key or a legacy alias; the current season if neither matches."""
    resolved = ALIASES.get((key or "").strip().lower(), (key or "").strip().lower())
    return SEASONS.get(resolved or DEFAULT_SEASON, SEASONS[DEFAULT_SEASON])


# Prompt words that name a season when the caller did not select one.  Deliberately
# narrow: "shooter" is not evidence of a season, it is evidence of a mechanism.
_PROMPT_WORDS: list[tuple[str, tuple[str, ...]]] = [
    ("2026-rebuilt", ("2026", "rebuilt", "fuel", "spindexer", "hub", "tower climb", "trench")),
    ("2025-reefscape", ("2025", "reefscape", "coral", "algae", "reef", "processor", "barge", "cage")),
    ("2024-crescendo", ("2024", "crescendo", "note", "speaker", "amp")),
]


def resolve_season(prompt: str = "", requested: str = "") -> tuple[str, dict[str, Any], str]:
    """Pick the season. Returns (key, season, how it was decided).

    Authority order, and the reason for it: an explicit selection is a statement of fact from
    the team and always wins; failing that, an unambiguous season word in the prompt is a
    reasonable inference; failing that, the current season is the only sensible default.
    Every design records which of the three happened, so a surprising robot can be traced to
    the season that produced it rather than argued about.
    """
    chosen = ALIASES.get((requested or "").strip().lower(), (requested or "").strip().lower())
    if chosen in SEASONS:
        return chosen, SEASONS[chosen], "selected by the team"
    text = (prompt or "").lower()
    for key, words in _PROMPT_WORDS:
        if any(word in text for word in words):
            return key, SEASONS[key], f"inferred from the request ({key})"
    return DEFAULT_SEASON, SEASONS[DEFAULT_SEASON], "default (current season)"


# ─────────────────────────────────────────────────────────────────────────────
# Derived design targets — computed from the field, never stored
# ─────────────────────────────────────────────────────────────────────────────
def element(season: dict[str, Any], name: str) -> dict[str, Any]:
    for item in season["field"]["elements"]:
        if item["name"] == name:
            return item
    return {}


def frame_budget(season: dict[str, Any], width_in: float, length_in: float) -> dict[str, Any]:
    """Check a frame against the season's perimeter budget and shrink it if it does not fit.

    This is the constraint that silently changes between seasons and quietly fails a robot at
    inspection.  2025 allowed 120 in of perimeter, so 28 × 28 (112 in) was comfortable; 2026
    allows 110, so the same frame is 2 in over and the design is illegal before anything is
    built on it.  Scaling both sides keeps the aspect ratio the team asked for.
    """
    limit = season["rules"]["perimeter_in"]
    perimeter = 2 * (width_in + length_in)
    if perimeter <= limit + 1e-6:
        return {"width_in": width_in, "length_in": length_in, "perimeter_in": round(perimeter, 1),
                "limit_in": limit, "fits": True, "scaled": False, "note": ""}
    scale = limit / perimeter
    new_w, new_l = math.floor(width_in * scale * 4) / 4, math.floor(length_in * scale * 4) / 4
    return {
        "width_in": new_w, "length_in": new_l,
        "perimeter_in": round(2 * (new_w + new_l), 1), "limit_in": limit,
        "fits": False, "scaled": True,
        "note": (f"{width_in:g} × {length_in:g} in is {round(perimeter, 1):g} in of perimeter, over "
                 f"the {limit:g} in {season['rules']['perimeter_rule']} budget for "
                 f"{season['label']}. Scaled to {new_w:g} × {new_l:g} in "
                 f"({round(2 * (new_w + new_l), 1):g} in) — restate the frame explicitly if you "
                 f"want a different shape inside the budget."),
    }


def max_square_frame(season: dict[str, Any]) -> float:
    """The largest square frame inside the perimeter budget, to the quarter inch."""
    return math.floor(season["rules"]["perimeter_in"] / 4 * 4) / 4


def shot_solution(exit_fps: float, angle_deg: float, release_height_in: float,
                  target_height_in: float) -> dict[str, Any]:
    """Where a shot lands, from the flywheel exit velocity and the release geometry.

    Flat-earth projectile with no drag — for a foam ball at FRC speeds that overestimates
    range by a noticeable margin, which is exactly why the answer carries the caveat rather
    than pretending to be a firing solution.  What it is good for is the question a design
    review actually asks: can this shooter reach the goal *at all*, and does the ball arrive
    descending or flat?

    A shot that arrives descending drops into an opening; a shot arriving flat hits the rim.
    """
    v = max(exit_fps, 0.1)
    theta = math.radians(max(1.0, min(89.0, angle_deg)))
    rise_ft = (target_height_in - release_height_in) / 12.0
    vy, vx = v * math.sin(theta), v * math.cos(theta)
    apex_ft = release_height_in / 12.0 + vy * vy / (2 * _G_FPS2)
    discriminant = vy * vy - 2 * _G_FPS2 * rise_ft
    if discriminant <= 0:
        return {"reaches": False, "range_ft": 0.0, "apex_in": round(apex_ft * 12, 1),
                "entry_angle_deg": 0.0, "flight_s": 0.0,
                "note": (f"Never reaches {target_height_in:g} in — the apex is "
                         f"{apex_ft * 12:.0f} in. Raise the exit velocity or the release point.")}
    vy_at_target = math.sqrt(discriminant)
    flight = (vy + vy_at_target) / _G_FPS2
    return {
        "reaches": True,
        "range_ft": round(vx * flight, 1),
        "apex_in": round(apex_ft * 12, 1),
        "entry_angle_deg": round(math.degrees(math.atan2(vy_at_target, vx)), 1),
        "flight_s": round(flight, 2),
        "note": ("Vacuum trajectory: no drag, no spin, no Magnus. A foam ball loses real range "
                 "to air, so treat this as the optimistic ceiling and tune on the field."),
    }


def required_exit_fps(angle_deg: float, release_height_in: float, target_height_in: float,
                      range_ft: float) -> float:
    """The exit velocity that puts a shot at a given range and height — the sizing direction.

    `shot_solution` answers "what does this shooter do"; this answers "what does the shooter
    have to be", which is the question you ask before choosing a flywheel and a reduction.
    """
    theta = math.radians(max(1.0, min(89.0, angle_deg)))
    rise_ft = (target_height_in - release_height_in) / 12.0
    denominator = 2 * math.cos(theta) ** 2 * (range_ft * math.tan(theta) - rise_ft)
    if denominator <= 0:
        return 0.0
    return round(math.sqrt(_G_FPS2 * range_ft * range_ft / denominator), 1)


def optimal_launch_angle(release_height_in: float, target_height_in: float,
                         range_ft: float) -> float:
    """The launch angle that reaches a target with the least exit velocity.

    For a projectile thrown to a point Δh above the release at horizontal distance d, the
    minimum-energy angle is 45° + ½·atan(Δh/d).  It is worth deriving rather than guessing:
    it tells you the *cheapest* shot, which is the one that needs the smallest flywheel, the
    least current and the shortest spin-up — and it is what a hood range should be centred on.
    """
    rise_ft = (target_height_in - release_height_in) / 12.0
    if range_ft <= 0:
        return 75.0
    return round(45.0 + 0.5 * math.degrees(math.atan2(rise_ft, range_ft)), 1)


def surface_speed_for_exit(exit_fps: float, *, counter_rotating: bool) -> float:
    """The flywheel surface speed a target exit velocity needs.

    A gamepiece squeezed between one spinning wheel and a stationary hood leaves at roughly
    the mean of the two surfaces — half the wheel's surface speed. Two counter-rotating wheels
    move both surfaces, so the piece leaves at close to the full surface speed. Teams size
    shooters as if one wheel gave all of it and then wonder why the range is short.
    """
    return round(exit_fps if counter_rotating else exit_fps * 2, 1)


def design_targets(season: dict[str, Any], *, robot_height_in: float = 0.0,
                   release_height_in: float = 0.0) -> list[dict[str, Any]]:
    """The numeric targets this season sets for a robot, each with what it is derived from.

    Every row states the target, the field or rule figure it came from and what it means for
    the design.  A model reading these learns the derivation, not the answer.
    """
    rules = season["rules"]
    rows: list[dict[str, Any]] = [
        {"target": "Frame perimeter", "value": f"≤ {rules['perimeter_in']:g} in",
         "from": f"{rules['perimeter_rule']} starting configuration",
         "means": (f"A square frame is at most {max_square_frame(season):g} in on a side. "
                   f"Pick the rectangle deliberately — a wider frame buys intake width, a "
                   f"longer one buys mechanism room, and the budget spends on one or the other.")},
        {"target": "Starting height", "value": f"≤ {rules['start_height_in']:g} in",
         "from": f"{rules['height_rule']} starting configuration",
         "means": "Everything, climber included, has to stow inside this before the match starts."},
    ]
    if rules.get("max_height_in"):
        rows.append({
            "target": "Height during the match", "value": f"≤ {rules['max_height_in']:g} in, always",
            "from": f"{rules['max_height_rule']} vertical extension limit",
            "means": ("This is not a starting-configuration limit — nothing may extend above it "
                      "at any point in the match. It rules out a tall elevator and a telescoping "
                      "climb mast outright. Measured perpendicular to the robot perimeter as if "
                      "the robot were on a flat floor, which is why hanging partway up the "
                      "TOWER is still legal: the robot's own height has not changed."),
        })
    rows += [
        {"target": "Horizontal extension", "value": f"≤ {rules['extension_in']:g} in beyond the perimeter",
         "from": rules["extension_rule"],
         "means": ("Sets how far an intake or a turret may reach outside the frame. A turret "
                   "whose shooter sweeps past the perimeter is the classic way to fail this.")},
        {"target": "Weight", "value": f"≤ {rules['weight_lb']:g} lb ({rules['weight_with_bumpers_lb']:g} lb with bumpers)",
         "from": f"{rules['weight_rule']} / {rules['bumper_weight_rule']}",
         "means": "Budget it per subsystem at design time; a weight problem found in week six is a redesign."},
        {"target": "Propulsion motors", "value": f"≤ {rules['propulsion_motors']}",
         "from": rules["propulsion_rule"],
         "means": ("Motors that drive the robot across the carpet. Swerve steering motors do not "
                   "count, so four modules are fine — but a six-motor west-coast drivebase is not."
                   if rules["propulsion_motors"] < 6 else
                   "Enough for a six-motor west-coast drivebase.")},
    ]

    hub = element(season, "HUB")
    if hub:
        release = release_height_in or min(robot_height_in or rules["start_height_in"],
                                           rules["start_height_in"]) * 0.65
        rim = hub["opening_front_edge_in"]
        rows.append({
            "target": "Shot height", "value": f"clear {rim:g} in, descending",
            "from": f"{hub['name']} opening front edge at {rim:g} in",
            "means": (f"From a release around {release:.0f} in the ball has to gain "
                      f"{rim - release:.0f} in and still be falling when it arrives. A flat shot "
                      f"at rim height hits the edge; aim for an entry angle past 40°."),
        })
        rows.append({
            "target": "Aim window", "value": f"{hub['opening_across_in']:g} in opening",
            "from": f"{hub['name']} hexagonal opening",
            "means": ("A 41.7 in target is forgiving in azimuth and unforgiving in range — "
                      "distance error, not heading error, is what misses."),
        })

    tower = element(season, "TOWER")
    if tower:
        rungs = tower["rungs_in"]
        criteria = tower.get("level_criteria") or {}
        bumper_top = rules["bumper_zone_in"][1]
        if criteria:
            # The climb is a hoist, not a reach. Scoring asks where the robot's own bumpers end
            # up, and R107 forbids extending a mast to the rung, so the number that matters is
            # how far the whole robot has to rise — not how high a hook can get.
            rows.append({
                "target": "Climb rise (whole robot)",
                "value": (f"L2 ≥ {rungs['L2'] - bumper_top:.0f} in of rise, "
                          f"L3 ≥ {rungs['L3'] - bumper_top:.0f} in"),
                "from": f"{tower['name']} scoring criteria + {rules['bumper_rule']} bumper zone",
                "means": (f"Levels are scored on the ROBOT's position, not its reach: "
                          f"L2 needs the bumper covers entirely above the {rungs['L2']:g} in "
                          f"rung and L3 entirely above the {rungs['L3']:g} in rung, while "
                          f"contacting a rung or upright. With the bumper top at "
                          f"{bumper_top:g} in on the ground, that is the whole robot rising "
                          f"{rungs['L3'] - bumper_top:.0f}+ in for L3. Rungs are 18 in apart, "
                          f"so a mechanism that can re-grip one rung higher earns one more "
                          f"level."),
            })
            rows.append({
                "target": "Rung grip", "value": f"{tower.get('rung_od_in', 1.66):g} in OD pipe",
                "from": f"{tower['name']} rung stock",
                "means": (f"1-1/4 in Sch 40 pipe, overhanging {tower.get('rung_overhang_in', 0):g} in "
                          f"past each upright face. Size the hook throat to that OD, and expect "
                          f"the support structure crossing between "
                          f"{tower.get('support_structure_in', [0, 0])[0]:g} and "
                          f"{tower.get('support_structure_in', [0, 0])[1]:g} in to be in the way "
                          f"on the way up."),
            })
        else:
            rows.append({
                "target": "Climb reach",
                "value": ", ".join(f"{k} {v:g} in" for k, v in rungs.items()),
                "from": f"{tower['name']} rung centres",
                "means": "Rung centre heights above the carpet.",
            })

    trench = element(season, "TRENCH")
    if trench:
        w, h = trench["underpass_in"]
        rows.append({
            "target": "Trench envelope", "value": f"≤ {h:g} in tall, ≤ {w:g} in wide",
            "from": f"{trench['name']} underpass",
            "means": (f"A robot under {h:g} in gets a second route between zones. That is a "
                      f"whole-robot decision made at architecture time, not a trim you apply "
                      f"later — the hopper, the shooter and the climber all have to live in it."),
        })

    bump = element(season, "BUMP")
    if bump:
        rows.append({
            "target": "Bump clearance", "value": f"{bump['size_in'][2]:g} in crest, {bump['ramp_deg']:g}° ramps",
            "from": bump["name"],
            "means": ("Breakover, not ground clearance, is what beaches a robot: a long "
                      "wheelbase lands its belly on the crest. Check the diagonal, and expect "
                      "a loaded hopper to spill going over."),
        })

    reef = element(season, "REEF")
    if reef:
        levels = reef["levels_in"]
        rows.append({
            "target": "Scoring reach", "value": ", ".join(f"{k} {v:g} in" for k, v in levels.items()),
            "from": f"{reef['name']} branch heights",
            "means": (f"L4 at {levels['L4']:g} in is what decides whether the robot needs a "
                      f"multi-stage elevator; L1–L3 are reachable with an arm on a short lift."),
        })

    cage = element(season, "CAGE")
    if cage:
        rows.append({
            "target": "Climb reach", "value": f"shallow {cage['shallow_bottom_in']:g} in, deep {cage['deep_bottom_in']:g} in",
            "from": f"{cage['name']} bottom heights",
            "means": "Deep pays double and needs a mechanism that reaches down and lifts the robot.",
        })
    return rows


def scoring_math(season: dict[str, Any]) -> list[str]:
    """Turn the scoring table into the handful of arithmetic facts a strategy answer needs."""
    lines: list[str] = []
    if season["key"] == "2026-rebuilt":
        lines += [
            "An L3 climb is 30 points — the same as 30 FUEL, which at a realistic 4–6 FUEL per "
            "second of *active* HUB time is roughly six seconds of perfect scoring. A climb that "
            "takes longer than that and costs you the last active window is not free.",
            "An auto L1 climb is 15 points for two robots, so an alliance that climbs in auto "
            "banks 30 points before teleop and only needs 20 more TOWER points for TRAVERSAL.",
            "TRAVERSAL needs 50 TOWER points: L2 + L3 (50) does it, as does auto L1 ×2 (30) plus "
            "any single teleop climb.",
            "ENERGIZED at 100 FUEL is an alliance total — roughly 33 per robot, which a "
            "competent cycler clears. SUPERCHARGED at 360 is 3.6× that and is an alliance "
            "strategy, not a robot specification.",
            "FUEL in an inactive HUB is worth zero. Capacity you cannot deliver during an active "
            "window is dead weight, which is the argument for passing and for storing.",
        ]
    elif season["key"] == "2025-reefscape":
        lines += [
            "Teleop CORAL is 2/3/4/5 by level, so an L4 piece is 2.5× an L1 piece. A robot that "
            "cycles L4 at half the rate of an L1 robot still out-scores it.",
            "A deep CAGE is 12 points — about 2.4 teleop L4 CORAL. It is worth building for, and "
            "worth practising until it is reliable rather than occasional.",
            "PROCESSOR ALGAE at 6 beats BARGE ALGAE at 4 and needs no height, which makes it the "
            "cheapest points on the field for a robot with no elevator.",
        ]
    else:
        lines.append("No scoring table for this season; score the design against whatever "
                     "manual you will be inspected under.")
    return lines


def rule_findings(spec: dict[str, Any], season: dict[str, Any]) -> list[dict[str, Any]]:
    """Check a finished spec against the season's construction rules.

    Deliberately mechanical and deliberately incomplete.  It catches the four things a
    synthesised robot can actually get wrong — perimeter, stowed height, propulsion motor
    count and weight — and says so plainly.  It is not an inspection, and the wording never
    claims it is: passing every check here means nothing was caught, not that the robot is legal.
    """
    rules = season["rules"]
    frame = spec.get("frame") or {}
    findings: list[dict[str, Any]] = []

    width, length = frame.get("width_in", 0), frame.get("length_in", 0)
    perimeter = 2 * (width + length)
    findings.append({
        "check": "Frame perimeter", "rule": rules["perimeter_rule"],
        "ok": perimeter <= rules["perimeter_in"] + 1e-6,
        "detail": (f"{width:g} × {length:g} in = {perimeter:g} in against a "
                   f"{rules['perimeter_in']:g} in budget"),
        "fix": (f"Shrink to {max_square_frame(season):g} in square or any rectangle summing to "
                f"{rules['perimeter_in'] / 2:g} in per side pair."),
    })

    stowed = max([(spec.get(key) or {}).get("stowed_height_in", 0) for key in ("climber",)]
                 + [(spec.get("constraints") or {}).get("stowed_height_in", 0),
                    (spec.get("elevator") or {}).get("max_height_in", 0)
                    if (spec.get("elevator") or {}).get("included") else 0])
    if stowed:
        findings.append({
            "check": "Stowed height", "rule": rules["height_rule"],
            "ok": stowed <= rules["start_height_in"] + 1e-6,
            "detail": f"tallest stowed feature {stowed:g} in against {rules['start_height_in']:g} in",
            "fix": ("Fold, telescope or lower the tall subsystem into the starting envelope; "
                    "this is an inspection failure, not a preference."),
        })

    # In-match height. Separate from the stowed check on purpose: a design can stow legally and
    # still break the rule the moment it deploys, and in a season with a hard vertical cap that
    # is the more likely way to fail.
    if rules.get("max_height_in"):
        deployed = max([(spec.get("climber") or {}).get("extended_height_in", 0)
                        if (spec.get("climber") or {}).get("included") else 0,
                        (spec.get("elevator") or {}).get("max_height_in", 0)
                        if (spec.get("elevator") or {}).get("included") else 0,
                        stowed])
        if deployed:
            findings.append({
                "check": "Height during the match", "rule": rules["max_height_rule"],
                "ok": deployed <= rules["max_height_in"] + 1e-6,
                "detail": (f"tallest deployed feature {deployed:g} in against a "
                           f"{rules['max_height_in']:g} in limit that applies for the whole match"),
                "fix": ("Nothing may extend above this at any time — not a mast, not an "
                        "elevator, not a climb hook. A climber has to raise the robot's own "
                        "body up the structure instead of reaching up to it."),
            })

    drivetrain = spec.get("drivetrain") or {}
    propulsion = int(drivetrain.get("drive_motors", 0) or 0)
    if propulsion:
        findings.append({
            "check": "Propulsion motors", "rule": rules["propulsion_rule"],
            "ok": propulsion <= rules["propulsion_motors"],
            "detail": (f"{propulsion} propulsion motors against a limit of "
                       f"{rules['propulsion_motors']}"
                       + (" (swerve steering motors do not count)"
                          if drivetrain.get("type", "").startswith("swerve") else "")),
            "fix": (f"Drop to {rules['propulsion_motors']} driven motors — for a west-coast "
                    f"drivebase that means {rules['propulsion_motors']} motors across both "
                    f"gearboxes, not six."),
        })

    mass = spec.get("mass_estimate") or {}
    if mass.get("counted_lb"):
        findings.append({
            "check": "Weight (estimate)", "rule": rules["weight_rule"],
            "ok": mass["counted_lb"] <= rules["weight_lb"],
            "detail": f"first-order estimate {mass['counted_lb']:g} lb against {rules['weight_lb']:g} lb",
            "fix": "Estimates are for trade studies. Weigh the real robot before you rely on this.",
        })
    return findings


def prompt_block(season: dict[str, Any]) -> str:
    """The season, compressed to what a design decision actually needs.

    This string is built once and used by the Design Studio, the training corpus and the
    evaluation harness, for the same reason the mechanism vocabulary is: if the model is
    trained with the field in front of it and asked at inference without it, the two tasks are
    not the same task.  Keeping it short matters — it is prepended to every design request, so
    it has to earn its tokens.  Only figures a mechanism is sized against are here.
    """
    rules = season["rules"]
    lines = [f"Season: {season['label']} — {season['summary']}",
             f"Gamepiece: {season['gamepiece']['name']}, "
             f"{season['gamepiece']['diameter_in']:g} in {season['gamepiece']['form']}."]
    for item in season["field"]["elements"]:
        if item["kind"] == "goal":
            if item.get("opening_front_edge_in"):
                lines.append(f"{item['name']}: opening front edge {item['opening_front_edge_in']:g} in "
                             f"off the carpet, {item.get('opening_across_in', 0):g} in across.")
            elif item.get("levels_in"):
                lines.append(f"{item['name']}: scoring heights "
                             + ", ".join(f"{k} {v:g} in" for k, v in item["levels_in"].items()) + ".")
        elif item["kind"] == "climb":
            if item.get("rungs_in"):
                lines.append(f"{item['name']}: rungs at "
                             + ", ".join(f"{k} {v:g} in" for k, v in item["rungs_in"].items()) + ".")
            elif item.get("deep_bottom_in"):
                lines.append(f"{item['name']}: shallow {item['shallow_bottom_in']:g} in, "
                             f"deep {item['deep_bottom_in']:g} in.")
        elif item["kind"] == "obstacle":
            if item.get("underpass_in"):
                lines.append(f"{item['name']}: underpass {item['underpass_in'][0]:g} in wide × "
                             f"{item['underpass_in'][1]:g} in tall.")
            else:
                lines.append(f"{item['name']}: {item['size_in'][2]:g} in crest on "
                             f"{item.get('ramp_deg', 0):g}° ramps.")
    lines.append(f"Rules: perimeter ≤ {rules['perimeter_in']:g} in ({rules['perimeter_rule']}), "
                 f"starting height ≤ {rules['start_height_in']:g} in, extension ≤ "
                 f"{rules['extension_in']:g} in ({rules['extension_rule']}), weight ≤ "
                 f"{rules['weight_lb']:g} lb, ≤ {rules['propulsion_motors']} propulsion motors "
                 f"({rules['propulsion_rule']}).")
    return "\n".join(lines)


def season_digest(season: dict[str, Any]) -> dict[str, Any]:
    """Small machine-readable summary carried in specs, dossiers and training manifests."""
    return {
        "key": season["key"], "label": season["label"], "game": season["game"],
        "year": season["year"], "season_version": SEASON_VERSION,
        "gamepiece": season["gamepiece"]["name"],
        "gamepiece_diameter_in": season["gamepiece"]["diameter_in"],
        "perimeter_in": season["rules"]["perimeter_in"],
        "start_height_in": season["rules"]["start_height_in"],
        "weight_lb": season["rules"]["weight_lb"],
    }


def season_options() -> list[dict[str, Any]]:
    """What the UI offers in the season selector."""
    return [{"key": key, "label": SEASONS[key]["label"], "game": SEASONS[key]["game"],
             "year": SEASONS[key]["year"], "summary": SEASONS[key]["summary"],
             "gamepiece": SEASONS[key]["gamepiece"]["name"],
             "frame_in": list(SEASONS[key]["frame"]),
             "perimeter_in": SEASONS[key]["rules"]["perimeter_in"],
             "default_subsystems": SEASONS[key]["default_subsystems"]}
            for key in SELECTABLE]
