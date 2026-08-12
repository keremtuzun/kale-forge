"""The generalization: Kale Forge is no longer a robot-only compiler.

The acceptance test the whole change exists for is at the bottom of this file — typing
"Design a bearing block for a 1/2 inch hex shaft." must produce a bearing block. Not a robot,
not a season prompt, not an empty drivetrain. Everything above it is the machinery that has
to hold for that to keep being true.

Two failure modes are tested harder than anything else, because both are the kind of bug that
looks like a working product:

  * robot contamination — a part that quietly arrives with wheels, a battery and a PDH in it;
  * silent invention — a critical dimension chosen by the compiler and not reported.
"""
import pytest

from app.services import mech_primitives
from app.services.design_intent import classify, season_is_relevant
from app.services.design_router import DesignNotSupported, route


# ── intent classification ────────────────────────────────────────────────────
# One case per design type the spec named, plus the two that used to be misread.
@pytest.mark.parametrize("prompt,expected", [
    ("Design a bearing block for a 1/2 in hex shaft using two 1.125 in OD bearings.",
     "mechanical_part"),
    ("Design a swerve drive robot for the 2026 season with a coral scorer.", "robot"),
    ("Design an elevator subsystem with two stages and a 1:12 reduction.", "subsystem"),
    ("Design a CAN sensor PCB with 4 CAN connectors and 12V input.", "pcb"),
    ("Design a 1/2 in hex shaft 8 inches long with two retaining ring grooves.",
     "mechanical_part"),
    ("Design a gearbox plate for two Kraken X60 motors.", "mechanical_part"),
])
def test_classification(prompt, expected):
    assert classify(prompt)["design_type"] == expected


def test_an_explicit_selection_beats_the_classifier():
    """The type selector is an instruction, not a hint — but the override is recorded."""
    out = classify("Design a bearing block for a 1/2 in hex shaft.", "robot")
    assert out["design_type"] == "robot"
    assert out["overridden"] is True


def test_auto_is_not_an_override():
    out = classify("Design a bearing block for a 1/2 in hex shaft.", "auto")
    assert out["design_type"] == "mechanical_part"
    assert out["overridden"] is False


# ── the season only appears where a season means something ───────────────────
def test_a_bearing_housing_is_never_asked_about_a_season():
    assert season_is_relevant("Design a bearing housing.", "mechanical_part")["relevant"] is False


def test_a_whole_robot_is_governed_by_a_season():
    assert season_is_relevant("Design a swerve robot.", "robot")["relevant"] is True


def test_naming_a_season_makes_it_relevant_whatever_the_artifact():
    out = season_is_relevant("Design a bearing block for our 2026 Rebuilt robot.",
                             "mechanical_part")
    assert out["relevant"] is True


# ── no robot contamination ───────────────────────────────────────────────────
_ROBOT_ONLY_KEYS = ("drivetrain", "electrical", "frame", "constraints", "bumpers",
                    "power", "motors_total", "season")
_ROBOT_ONLY_FEATURES = ("wheel", "motor", "battery", "pdh", "tube", "swerve")


@pytest.mark.parametrize("prompt", [
    "Design a bearing block for a 1/2 in hex shaft.",
    "Design a 2 in aluminium spacer for a 3/8 in shaft.",
    "Design a 4 x 6 in aluminium plate 1/4 in thick.",
    "Design an L bracket with 2 in legs.",
])
def test_a_part_carries_no_robot(prompt):
    """The single most visible way this tool used to say 'I only make robots'."""
    out = route(prompt)
    assert out["designType"] == "mechanical_part"
    for key in _ROBOT_ONLY_KEYS:
        assert key not in out, f"{key!r} leaked into a part"
    kinds = set(out["cad"]["feature_counts"])
    # A motor plate legitimately shows reference motors; these prompts do not.
    assert not (kinds & set(_ROBOT_ONLY_FEATURES)), f"{kinds} contains robot geometry"


def test_a_part_never_receives_a_season():
    out = route("Design a bearing block for a 1/2 in hex shaft.", season="2026-rebuilt")
    assert out["seasonRelevant"]["relevant"] is False
    assert "season" not in out


# ── the ten generators the first release promises ────────────────────────────
_PROMPTS = {
    "bearing_block": "Design a bearing block for a 1/2 in hex shaft.",
    "bearing_housing": "Design a bearing housing for a 1.125 in OD bearing.",
    "shaft": "Design a 1/2 in hex shaft 8 in long.",
    "spacer": "Design a 0.75 in long spacer for a 3/8 in shaft.",
    "plate": "Design a 4 x 6 in aluminium plate 3/16 in thick.",
    "gusset": "Design a gusset for 2x1 tube.",
    "motor_plate": "Design a motor mounting plate for two Kraken X60 motors.",
    "gearbox_plate": "Design a gearbox plate for a 1/2 in hex output shaft.",
    "roller": "Design a 2 in diameter intake roller 14 in long on a 1/2 in hex shaft.",
    "pulley": "Design a 24 tooth HTD 5mm pulley for a 1/2 in hex shaft.",
    "bracket": "Design an L bracket with 2 in legs in 1/8 in aluminium.",
}


@pytest.mark.parametrize("part_type,prompt", sorted(_PROMPTS.items()))
def test_every_promised_generator_actually_builds(part_type, prompt):
    """Requirement: do not claim a generator exists unless it makes real geometry."""
    out = route(prompt)
    assert out["engine"] == "mechanical"
    assert out["cad"]["feature_total"] >= 1
    assert out["parameters"], "a part with no named parameters is not parametric"
    assert out["dimensions"], "a part with no dimensions is not a part"


def test_the_registry_and_the_promise_agree():
    assert set(mech_primitives.SUPPORTED_PART_TYPES) >= set(_PROMPTS) - {"plate"}


# ── compatibility: nothing already saved may stop working ────────────────────
# The studio owns no database. A saved design IS its prompt chain, and the design type is
# re-derived from that chain every time it loads — so the classifier is this change's
# migration, and a prompt it reads wrongly is a design taken away from its owner. These are
# the shapes of prompt teams actually wrote before any of this existed.
_LEGACY = [
    "A swerve drive robot that scores coral on the reef and climbs the cage",
    "coral scorer",
    "Build a robot for Rebuilt 2026 with a fuel cycler and a climber",
    "27x27 swerve chassis with MK4i modules and a two stage elevator",
    "tank drive robot with a ground intake",
    "an algae processor bot",
    "Kitbot with a shooter",
    "Everybot style robot for 2025 Reefscape",
    "robot with an elevator, intake, shooter and climber",
    "chassis only, no mechanisms",
    "a cone and cube robot",
    "note shooter bot",
]


@pytest.mark.parametrize("prompt", _LEGACY)
def test_a_saved_robot_design_still_opens(prompt):
    out = route(prompt, season="2026-rebuilt")
    assert out["designType"] in ("robot", "subsystem"), (
        f"{prompt!r} used to be a robot and must still be one")
    assert out["engine"] == "robot"
    assert out["cad"]["feature_total"] > 50


def test_a_request_with_no_robot_in_it_is_still_refused_honestly():
    """The compatibility guard must not turn every unsupported request into a robot."""
    with pytest.raises(DesignNotSupported):
        route("Design a helical compression spring with a 2 in free length.")


# ── honest failure ───────────────────────────────────────────────────────────
def test_an_unsupported_mechanism_says_so_instead_of_faking_one():
    with pytest.raises(DesignNotSupported) as excinfo:
        route("Design a four bar linkage with a 30 degree throw.",
              requested_type="mechanical_part")
    payload = excinfo.value.payload
    assert payload["state"] == "unsupported"
    assert payload["supported"], "a refusal has to say what IS supported"


# ── provenance ───────────────────────────────────────────────────────────────
_SOURCES = {"VERIFIED", "USER_PROVIDED", "CALCULATED", "INFERRED", "ASSUMED", "UNRESOLVED"}


def test_every_dimension_says_where_it_came_from():
    out = route("Design a bearing block for a 1/2 in hex shaft.")
    assert out["dimensions"]
    for row in out["dimensions"]:
        assert row["source"] in _SOURCES, row


def test_a_chosen_dimension_is_reported_as_assumed_not_silently_used():
    """Nothing critical may be invented in silence. The wall thickness was never asked for."""
    out = route("Design a bearing block for a 1/2 in hex shaft.")
    assumed = {r["key"] for r in out["provenance"] if r["source"] == "ASSUMED"}
    assert "wall_in" in assumed
    assert any("wall" in r["detail"] for r in out["risks"])


def test_a_user_stated_dimension_is_attributed_to_the_user():
    """And is actually USED. A stated width that the generator ignores is the same defect
    whether the label is wrong or the number is."""
    out = route("Design a bearing block for a 1/2 in hex shaft, 3.5 in wide.")
    rows = {r["key"]: r for r in out["provenance"]}
    assert rows["block_width_in"]["source"] == "USER_PROVIDED"
    assert out["parameters"]["blockWidth"] == 3.5


# ── dimension binding, the half of this that is hard to get right ────────────
@pytest.mark.parametrize("prompt,part_type,expected", [
    # A roller is described by the shaft it runs on; it is still a roller.
    ("Design a 2 in diameter intake roller 14 in long on a 1/2 in hex shaft.",
     "roller", {"rollerDiameter": 2.0, "rollerWidth": 14.0, "shaftDiameter": 0.5}),
    # 5 mm is the tooth profile, not the bore.
    ("Design a 24 tooth HTD 5mm pulley for a 1/2 in hex shaft.",
     "pulley", {"teeth": 24, "boreDiameter": 0.5}),
    # "4 x 6" on a plate is the outline, not a tube section.
    ("Design a 4 x 6 in aluminium plate 3/16 in thick.",
     "plate", {"plateWidth": 4.0, "plateDepth": 6.0, "plateThickness": 0.1875}),
    # "1/8 in aluminium" is stock thickness, not a leg length.
    ("Design an L bracket with 2 in legs in 1/8 in aluminium.",
     "bracket", {"legA": 2.0, "bracketThickness": 0.125}),
    ("Design a 1/2 in hex shaft 8 in long.",
     "hex_shaft", {"shaftSize": 0.5, "shaftLength": 8.0}),
])
def test_numbers_bind_to_the_thing_they_dimension(prompt, part_type, expected):
    out = route(prompt)
    assert out["partType"] == part_type
    for key, value in expected.items():
        assert out["parameters"][key] == pytest.approx(value), key


# ── the CAD pipeline, at three scales ────────────────────────────────────────
def test_one_component_through_the_pipeline():
    out = route("Design a bearing block for a 1/2 in hex shaft.")
    cad = out["cad"]
    assert len(cad["assemblies"]) == 1
    body = cad["assemblies"][0]["features"][0]
    assert body["t"] == "plate"
    assert len(body["bores"]) >= 5, "pocket, shaft relief and four mounting holes"
    assert out["mass"]["value"] > 0


def test_a_multi_part_assembly_through_the_pipeline():
    out = route("Design a motor mounting plate for two Kraken X60 motors.")
    features = out["cad"]["assemblies"][0]["features"]
    assert len(features) >= 3, "the plate plus a reference motor per motor"
    assert {f["t"] for f in features} == {"plate", "motor"}


def test_a_complete_robot_still_compiles():
    """Regression: the robot path is the thing that must not have been broken."""
    out = route("Design a swerve drive robot for the 2026 season with a coral scorer.",
                season="2026-rebuilt")
    assert out["designType"] == "robot"
    assert out["engine"] == "robot"
    assert out["cad"]["feature_total"] > 100
    assert out["frame"]["width_in"] > 0


def test_the_exports_treat_a_part_the_same_as_a_robot():
    from app.services.frc_featurescript import build_featurescript

    out = route("Design a bearing block for a 1/2 in hex shaft.")
    source = build_featurescript(out, out["name"])
    assert "kalePlate(context" in source, "the holes must be cut in the export too"
    # The driving dimensions are dialog parameters, so the exported part is editable.
    assert "isLength(definition.blockWidth" in source
    assert "definition.frameWidth" not in source, "a part has no frame"


def test_the_featurescript_holes_are_expressions_not_stranded_literals():
    from app.services.frc_featurescript import build_featurescript

    out = route("Design a bearing block for a 1/2 in hex shaft.")
    source = build_featurescript(out, out["name"])
    assert "(blockWidth / 2 - mountHoleInset)" in source


# ── PCB generation ───────────────────────────────────────────────────────────
def test_a_pcb_request_enters_the_pcb_workflow():
    out = route("Design a CAN sensor PCB with 4 CAN connectors and 12V input.")
    assert out["designType"] == "pcb"
    assert out["engine"] == "pcb"
    assert out["components"], "a board with no components is not a board"


def test_a_generated_board_never_invents_a_part_number():
    """The hardest rule in the spec: a fabricated MPN is worse than no MPN."""
    out = route("Design a CAN sensor PCB with 4 CAN connectors and 12V input.")
    for part in out["components"]:
        if part.get("mpn"):
            assert part["verified"] is True, f"{part['reference']} has an unverified MPN"
        else:
            assert part.get("requirement"), (
                f"{part['reference']} has neither a real part number nor a requirement")


_BOARDS = [
    "Design a CAN sensor PCB with 4 CAN connectors and 12V input.",
    "Design a 12V to 5V power distribution board with 6 outputs.",
    "Design an I2C sensor breakout board with a 3.3V regulator.",
    "Design a two layer encoder breakout with SPI and 5V in.",
    "Design a LED signalling PCB for a robot running from 12V.",
    "Design a 12V to 5V and 3.3V regulator board for a robot.",
]


@pytest.mark.parametrize("prompt", _BOARDS)
def test_the_first_board_generators_produce_connected_designs(prompt):
    """A schematic whose nets go nowhere is not a schematic. Every generated board has to
    pass its own connectivity check, or the completion claim above it is false."""
    out = route(prompt)
    assert out["engine"] == "pcb"
    assert out["nets"], "a board with no nets is not connected to anything"
    failed = [c for c in out["checks"] if not c["ok"]]
    assert not failed, f"{prompt!r} generated a board that fails its own checks: {failed}"


@pytest.mark.parametrize("prompt", _BOARDS)
def test_no_board_anywhere_invents_a_part_number(prompt):
    for part in route(prompt)["components"]:
        assert not part.get("mpn") or part["verified"], part["reference"]


def test_every_regulated_rail_has_a_way_off_the_board():
    out = route("Design a 12V to 5V and 3.3V regulator board for a robot.")
    rails = {f"+{r['voltage']:g}V" for r in out["electronics"]["outputs"]}
    for net in out["nets"]:
        if net["name"] in rails:
            assert net["node_count"] >= 2, f"{net['name']} has nowhere to go"


def test_a_generated_board_states_how_far_it_actually_got():
    out = route("Design a CAN sensor PCB with 4 CAN connectors and 12V input.")
    stages = {s["stage"]: s["done"] for s in out["completion"]["stages"]}
    assert stages["SCHEMATIC_COMPLETE"] is True
    assert stages["PLACEMENT_COMPLETE"] is True
    assert stages["ROUTING_COMPLETE"] is True
    assert stages["RULE_CHECK_COMPLETE"] is True
    # And there it stops. No gerbers exist, so the last rung stays false and the summary
    # says what is missing rather than rounding up to "done".
    assert stages["MANUFACTURING_READY"] is False
    assert not out["exports"]["gerbers"]
    assert not out["exports"]["kicad_pcb"]
    assert "not manufacturing ready" in out["completion"]["summary"].lower()


def test_a_stage_cannot_be_reached_over_a_failed_one():
    """The ladder is walked in order. Routing without placement is not a thing."""
    from app.services.pcb_design import completion

    out = completion(placed=False, routed=True, checked=True)
    assert out["reached"] == "SCHEMATIC_COMPLETE"
    assert {s["stage"]: s["done"] for s in out["stages"]}["ROUTING_COMPLETE"] is False


# ── placement ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("prompt", _BOARDS)
def test_every_board_is_actually_placed(prompt):
    """PLACEMENT_COMPLETE is a claim, so it has to be earned per board."""
    out = route(prompt)
    layout = out["layout"]
    assert len(layout["placements"]) == len(out["components"]), "a part with no position"
    failed = [c for c in layout["checks"] if not c["ok"]]
    assert not failed, f"{prompt!r} placement fails its own checks: {failed}"
    stages = {s["stage"]: s["done"] for s in out["completion"]["stages"]}
    assert stages["PLACEMENT_COMPLETE"] is True


@pytest.mark.parametrize("prompt", _BOARDS)
def test_no_two_components_occupy_the_same_space(prompt):
    """Independent of the module's own overlap check — recomputed here from the output."""
    placed = route(prompt)["layout"]["placements"]
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            dx = abs(a["x"] - b["x"]) * 2
            dy = abs(a["y"] - b["y"]) * 2
            apart = (dx >= a["court_mm"][0] + b["court_mm"][0]
                     or dy >= a["court_mm"][1] + b["court_mm"][1])
            assert apart, f"{a['reference']} and {b['reference']} overlap"


def test_decoupling_sits_against_the_part_it_decouples():
    out = route("Design a 12V to 5V and 3.3V regulator board for a robot.")
    seats = {p["reference"]: p for p in out["layout"]["placements"]}
    pairs = [(c["reference"], c["decouples"]) for c in out["components"]
             if c.get("decouples")]
    assert pairs, "the regulators should have decoupling to place"
    for cap, owner in pairs:
        a, b = seats[cap], seats[owner]
        distance = ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5
        assert distance <= 5.0, f"{cap} is {distance:.1f} mm from {owner}"


# ── routing and DRC ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("prompt", _BOARDS)
def test_every_board_routes_and_passes_its_own_drc(prompt):
    """ROUTING_COMPLETE and RULE_CHECK_COMPLETE are claims, so they are earned per board."""
    out = route(prompt)
    routing = out["routing"]
    assert routing["complete"], f"unrouted: {routing['unrouted']}"
    assert routing["traces"], "a routed board has copper on it"
    assert not routing["unknown_pins"], routing["unknown_pins"]
    failed = [c for c in out["drc"] if not c["ok"]]
    assert not failed, f"{prompt!r} fails DRC: {failed}"
    stages = {s["stage"]: s["done"] for s in out["completion"]["stages"]}
    assert stages["ROUTING_COMPLETE"] is True
    assert stages["RULE_CHECK_COMPLETE"] is True
    assert stages["MANUFACTURING_READY"] is False, "no gerbers exist, so this stays false"


@pytest.mark.parametrize("prompt", _BOARDS)
def test_copper_clearance_recomputed_from_the_geometry(prompt):
    """Independent of the DRC in the module: the same rule, measured here from the output."""
    from app.services.pcb_route import CLEARANCE_MM, _segment_gap

    routing = route(prompt)["routing"]
    segments = [(t["net"], t["layer"], (a[0], a[1]), (b[0], b[1]), t["width_mm"])
                for t in routing["traces"] for a, b in zip(t["points"], t["points"][1:])]
    for i, (net_a, layer_a, a1, a2, w_a) in enumerate(segments):
        for net_b, layer_b, b1, b2, w_b in segments[i + 1:]:
            if net_a == net_b or layer_a != layer_b:
                continue
            gap = _segment_gap(a1, a2, b1, b2) - (w_a + w_b) / 2
            assert gap >= CLEARANCE_MM - 1e-6, f"{net_a} and {net_b} are {gap:.3f} mm apart"


def test_a_power_rail_is_wider_than_a_signal():
    """Trace width comes from the current, not from a default."""
    out = route("Design a 12V to 5V power distribution board with 6 outputs.")
    widths = {t["net"]: t["width_mm"] for t in out["routing"]["traces"]}
    power = [w for net, w in widths.items() if net.startswith(("VIN", "+"))]
    assert power, "the board should have a power net in copper"
    assert max(power) > 0.25, "a 1 A rail cannot be a minimum-width signal trace"


def test_ground_is_a_pour_and_the_pour_is_one_piece():
    out = route("Design a CAN sensor PCB with 4 CAN connectors and 12V input.")
    pour = out["routing"]["ground"]
    assert pour["kind"] == "pour" and pour["layer"] == "bottom"
    assert pour["stitching_vias"], "every ground pad needs a via down to the pour"
    assert not pour["orphans"], f"stranded ground pads: {pour['orphans']}"
    assert pour["coverage"] >= 0.55


def test_an_unroutable_board_does_not_claim_routing():
    """The ladder has to stop where the tool stops, on a board it genuinely cannot finish."""
    out = route("Design a 12V to 5V and 3.3V and 24V regulator board with 12 sensor "
                "ports, CAN, I2C, SPI and UART.")
    stages = {s["stage"]: s["done"] for s in out["completion"]["stages"]}
    if not out["routing"]["complete"]:
        assert stages["ROUTING_COMPLETE"] is False
        assert stages["RULE_CHECK_COMPLETE"] is False
        assert out["routing"]["unrouted"], "a failed route has to name what it could not do"


def test_routed_copper_is_in_the_3d_model():
    out = route("Design a CAN sensor PCB with 4 CAN connectors and 12V input.")
    bodies = out["cad"]["assemblies"][0]["features"]
    copper = [f for f in bodies if "trace" in str(f.get("note") or "")]
    assert copper, "the traces should be visible bodies, not just numbers"
    assert len(bodies) > len(out["layout"]["placements"]) + 1


def test_a_placed_board_appears_in_the_3d_viewer():
    """The board goes through the same CAD document as a robot, so it draws for free."""
    out = route("Design a CAN sensor PCB with 4 CAN connectors and 12V input.")
    cad = out["cad"]
    features = cad["assemblies"][0]["features"]
    assert features[0]["t"] == "plate", "the substrate is a body in the scene"
    assert len(features[0]["bores"]) == 4, "four M3 mounting holes, bored"
    # One body per placed component, plus the substrate, plus copper.
    assert len(features) >= len(out["layout"]["placements"]) + 1


# ── the acceptance test the whole change exists for ──────────────────────────
def test_the_acceptance_case():
    """Type this sentence, get a bearing block. Nothing else."""
    out = route("Design a bearing block for a 1/2 inch hex shaft.")

    assert out["designType"] == "mechanical_part"
    assert out["partType"] == "bearing_block"
    assert out["seasonRelevant"]["relevant"] is False

    body = out["cad"]["assemblies"][0]["features"][0]
    assert body["t"] == "plate", "there is a body to look at in the viewer"
    assert any(b.get("note") == "bearing pocket" for b in body["bores"])

    assert out["dimensions"], "dimensions are shown"
    assert [r for r in out["provenance"] if r["source"] == "ASSUMED"], "assumptions are shown"
    assert out["editable_manifest"], "the design is editable"

    # And nothing about a robot came along with it.
    assert "drivetrain" not in out and "frame" not in out
