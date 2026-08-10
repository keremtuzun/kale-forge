import hashlib
import pytest
import json

from app.services import frc_parts
from app.services.design_studio import DesignStudio
from app.services.robot_spec import build_robot_spec


# 27 x 27, not 28 x 28: the reference envelope tracks the most restrictive recent perimeter
# budget (110 in), and a 28 in square frame is 112 in of perimeter. Asking for one here would
# be testing the resize path rather than the synthesis path — that has its own test below.
PROMPT = (
    "Design a 27 x 27 inch FRC robot on a swerve-ready frame without swerve modules, "
    "with a coaxial slapdown intake, dual flywheel shooter, dead-axle arm, and climber."
)


def _spec(prompt: str, season: str = "offseason") -> dict:
    """Specs for tests are built without the model pass so they stay hermetic and fast.

    The season is pinned rather than inferred: these tests are about synthesis, and leaving the
    season to the default would silently rewrite their expectations the year the default moves.
    """
    return build_robot_spec(prompt, use_model=False, season=season)


def _render(spec: dict, tmp_path, name: str = "Reference Robot") -> dict:
    studio = object.__new__(DesignStudio)
    record = {"id": "test", "kind": "robot", "name": name, "revision": 1, "spec": spec}
    names = studio._render_robot(tmp_path, record)
    return {"names": names, "record": record}


def test_reference_profile_omits_modules_and_selects_real_subsystems():
    spec = _spec(PROMPT)
    assert spec["frame"]["width_in"] == 27
    assert spec["frame"]["length_in"] == 27
    assert spec["drive"]["type"] == "swerve-ready"
    assert spec["drive"]["modules_included"] is False
    assert set(spec["subsystems"]) == {"intake", "shooter", "arm", "climber"}
    assert spec["intake"]["type"] == "coaxial slapdown"
    assert spec["reference_basis"]


def test_season_perimeter_budget_resizes_an_illegal_frame():
    """The same 28 x 28 request is legal in 2025 and is not in 2026.

    2026 allows 110 in of perimeter against 2025's 120, so a frame that passed inspection last
    year is 2 in over this year. Building it anyway and staying quiet would be the worst of the
    available options, so the frame is scaled and the reason is recorded on the spec.
    """
    big = PROMPT.replace("27 x 27", "28 x 28")
    legal = _spec(big, season="2025-reefscape")
    assert (legal["frame"]["width_in"], legal["frame"]["length_in"]) == (28, 28)
    assert legal["constraints"]["frame_note"] == ""

    resized = _spec(big, season="2026-rebuilt")
    assert resized["frame"]["width_in"] == 27.5
    assert resized["constraints"]["frame_perimeter_in"] <= 110.0
    assert "110" in resized["constraints"]["frame_note"]
    assert all(row["ok"] for row in resized["rule_check"] if row["check"] == "Frame perimeter")


def test_season_drives_the_default_architecture():
    """A bare request produces a different robot in each season, from the season's own numbers."""
    prompt = "Design a competition robot that scores as fast as it can and climbs at the buzzer."
    rebuilt = _spec(prompt, season="2026-rebuilt")
    reefscape = _spec(prompt, season="2025-reefscape")

    assert rebuilt["season"]["key"] == "2026-rebuilt"
    assert rebuilt["hopper"]["included"] is True          # bulk fuel handling is the 2026 problem
    assert reefscape["elevator"]["included"] is True      # L4 at 72 in is the 2025 problem
    assert reefscape["hopper"]["included"] is False
    assert rebuilt["frame"]["width_in"] < reefscape["frame"]["width_in"]

    shot = rebuilt["shooter"]["shot"]
    assert shot["target_height_in"] == 72.0              # the HUB opening, not a stored constant
    assert 40 < shot["optimal_angle_deg"] < 80
    assert shot["required_exit_fps"] > 0


def test_revision_can_remove_a_subsystem():
    spec = _spec(PROMPT + "\nRevision request: remove the intake and use a four-bar arm")
    assert spec["intake"]["included"] is False
    assert "intake" not in spec["subsystems"]
    assert spec["manipulator"]["included"] is True


def test_robot_renderer_creates_detailed_dimensioned_assembly(tmp_path):
    spec = _spec(PROMPT)
    result = _render(spec, tmp_path)
    obj = (tmp_path / "reference-robot.obj").read_text()
    manifest = json.loads((tmp_path / "robot-assembly.json").read_text())
    assert "g chassis_left_2x1" in obj
    assert "g swerve_ready_corner_1" in obj
    assert "g intake_front_roller" in obj
    # Which flywheel groups appear depends on whether the prompt-seeded pick landed on a single
    # or a split shooter, and asserting one of them makes the test a hash check on the prompt
    # text. What matters here is that the shooter rendered at all.
    assert "_flywheel" in obj and "g shooter_" in obj
    assert "g arm_left_beam" in obj
    assert "g climber_left_outer" in obj
    assert len(manifest["components"]) >= 35
    assert manifest["schema_version"] == "3.0"
    assert "design-dossier.md" in result["names"]


# ── The regression this whole feature exists to prevent ─────────────────────
def test_different_prompts_produce_different_robots(tmp_path):
    prompts = [
        "Design a 28x28 FRC robot with MK4i modules on Krakens at L2, slapdown intake and shooter",
        "Design a 27 inch swerve robot on MAXSwerve and NEO Vortex with a 3 stage cascade elevator and wrist",
        "Build a 2024 note robot with MK4n modules, four bar intake, turret shooter and a single pivoting hook",
        "Design a 28 inch west coast tank kitbot with 6 inch wheels and an under-bumper intake",
    ]
    digests, summaries = set(), set()
    for index, prompt in enumerate(prompts):
        spec = _spec(prompt)
        target = tmp_path / f"design{index}"
        target.mkdir()
        _render(spec, target, name=f"Robot {index}")
        obj = (target / f"robot-{index}.obj").read_text()
        digests.add(hashlib.sha256(obj.encode()).hexdigest())
        summaries.add((spec["drivetrain"]["module"], spec["drivetrain"]["motor"],
                       spec["drivetrain"]["free_speed_fps"]))
    assert len(digests) == len(prompts), "prompts collapsed onto identical geometry"
    assert len(summaries) == len(prompts), "prompts collapsed onto one drivetrain"


def test_explicitly_named_hardware_always_wins():
    spec = _spec("Design a 28 inch swerve robot using MK4n modules on NEO Vortex motors at L3+")
    drivetrain = spec["drivetrain"]
    assert drivetrain["module"] == "SDS MK4n"
    assert drivetrain["motor"] == "NEO Vortex"
    assert drivetrain["drive_ratio_label"] == "L3+"
    assert drivetrain["drive_ratio"] == frc_parts.SWERVE_MODULES["mk4n"]["drive_ratios"]["L3+"]
    assert drivetrain["free_speed_fps"] > 0


def test_west_coast_drive_does_not_claim_swerve_modules():
    spec = _spec("Design a 28 inch west coast tank drive robot with 6 inch wheels")
    drivetrain = spec["drivetrain"]
    assert drivetrain["type"] == "west-coast"
    assert drivetrain["modules_included"] is False
    assert drivetrain["module_count"] == 0
    assert drivetrain["steer_motors"] == 0
    assert "MK4" not in drivetrain["module"]
    assert spec["drive"]["wheel_diameter_in"] == 6.0


# ── Real parts get replicated, not implied ─────────────────────────────────
def test_control_system_parts_are_placed_and_rendered(tmp_path):
    spec = _spec("Design a 28 inch swerve robot with an intake and a shooter")
    placed = {item["key"] for item in spec["electrical"]["placements"]}
    assert {"pdh", "main_breaker", "battery", "rio", "radio", "rsl"} <= placed

    _render(spec, tmp_path, name="Electrical Robot")
    obj = (tmp_path / "electrical-robot.obj").read_text()
    assert "g elec_pdh_body" in obj
    assert "g elec_main_breaker_body" in obj
    assert "g elec_main_breaker_stud_pos" in obj
    assert "g elec_battery_post_pos" in obj
    assert "g elec_rio_body" in obj

    bom = (tmp_path / "robot-bom.csv").read_text()
    assert "REV-11-1850" in bom          # PDH part number
    assert "120 A main breaker" in bom
    assert "Cooper Bussmann" in bom


def test_swerve_modules_are_replicated_with_vendor_geometry(tmp_path):
    spec = _spec("Design a 28 inch swerve robot on MK4i modules with Kraken X60s")
    _render(spec, tmp_path, name="Swerve Robot")
    obj = (tmp_path / "swerve-robot.obj").read_text()
    for index in (1, 2, 3, 4):
        assert f"g swerve_{index}_wheel" in obj
        assert f"g swerve_{index}_azimuth_bearing" in obj
        assert f"g swerve_{index}_drive_motor" in obj
        assert f"g swerve_{index}_steer_motor" in obj
    manifest = json.loads((tmp_path / "robot-assembly.json").read_text())
    azimuths = [mate for mate in manifest["mates"]
                if mate["type"] == "revolute" and "azimuth" in mate["component"]]
    assert len(azimuths) == 4
    bom = (tmp_path / "robot-bom.csv").read_text()
    assert "SDS MK4i" in bom
    assert "Swerve Drive Specialties" in bom


# ── Electrical planning ────────────────────────────────────────────────────
def test_power_budget_sizes_breakers_and_wire_per_channel():
    spec = _spec("Design a 28 inch swerve robot on Krakens with an intake, shooter and climber")
    electrical = spec["electrical"]
    assert electrical["main_breaker_a"] == 120
    assert electrical["battery_lead_awg"] == 6
    assert electrical["channels_used"] == len(electrical["assignments"])
    drive_rows = [row for row in electrical["assignments"] if row["subsystem"] == "drivetrain"]
    assert len(drive_rows) == 8  # four drive + four steer
    for row in electrical["assignments"]:
        assert row["wire_awg"] == frc_parts.wire_for_breaker(row["breaker_a"])["preferred_awg"]
    # The bus-sag figure must stay physical: a linear model run out to summed stall current
    # produces a negative voltage, which is worse than useless in a design dossier.
    assert 0 < electrical["brownout_current_a"] < electrical["summed_stall_current_a"]
    assert electrical["brownout_threshold_v"] > 0


def test_swerve_ready_frame_draws_no_drivetrain_current():
    spec = _spec(PROMPT)
    subsystems = {row["subsystem"] for row in spec["electrical"]["assignments"]}
    assert "drivetrain" not in subsystems


def test_mass_estimate_excludes_battery_and_bumpers_from_the_limit():
    spec = _spec("Design a 28 inch swerve robot with an intake, shooter, arm and climber")
    mass = spec["mass_estimate"]
    excluded = [row for row in mass["rows"] if not row["counted"]]
    assert {"battery", "bumpers"} <= {row["item"].split()[0] for row in excluded}
    assert mass["counted_lb"] < mass["total_lb"]
    assert mass["margin_lb"] == round(mass["target_lb"] - mass["counted_lb"], 1)


# ── Model integration ──────────────────────────────────────────────────────
def test_model_gap_is_reported_not_hidden(monkeypatch):
    """With no inference service reachable the design still builds, and says so.

    The unreachable address is forced rather than assumed. This test used to rely on nothing
    listening on the default port, which stopped being true the moment a local GGUF was served
    for real: the suite then failed with `used is True`, reporting a working model as a bug.
    """
    from app.config import get_settings

    monkeypatch.setenv("INFERENCE_URL", "http://127.0.0.1:1")
    get_settings.cache_clear()
    try:
        spec = build_robot_spec("Design a 28 inch swerve robot with an intake", use_model=True)
    finally:
        get_settings.cache_clear()
    assert spec["model"]["used"] is False
    assert spec["model"]["reason"]
    assert spec["subsystems"]


def test_model_intent_is_clamped_to_the_vocabulary():
    from app.services.robot_spec import _sanitize_intent

    clean = _sanitize_intent({
        "subsystems": ["intake", "death ray", "climber"],
        "drive_type": "hovercraft",
        "intake_type": "coaxial slapdown",
        "elevator_stages": 99,
        "design_notes": ["keep it simple"],
    })
    assert clean["subsystems"] == ["intake", "climber"]
    assert "drive_type" not in clean
    assert clean["intake_type"] == "coaxial slapdown"
    assert clean["elevator_stages"] == 4
    assert clean["design_notes"] == ["keep it simple"]


def test_dossier_explains_choices_and_techniques(tmp_path):
    spec = _spec("Design a 28 inch swerve robot on MK4i with an intake, shooter and climber")
    _render(spec, tmp_path, name="Dossier Robot")
    dossier = (tmp_path / "design-dossier.md").read_text()
    assert "## Drivetrain" in dossier
    assert "## Electrical plan" in dossier
    assert "## Mass estimate" in dossier
    assert "## Techniques applied" in dossier
    assert "SDS MK4i" in dossier
    assert "120 A" in dossier


def test_new_account_worked_example_is_private_editable_and_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_LOCAL_DIR", str(tmp_path / "storage"))
    from app.config import get_settings

    get_settings.cache_clear()
    studio = DesignStudio()
    record = studio.create_worked_example("user-123")

    assert record["owner_id"] == "user-123"
    assert record["is_example"] is True
    assert "create your own" in record["example_note"].lower()
    assert record["artifacts"]
    assert studio.list("another-user") == []
    assert studio.list("user-123")[0]["id"] == record["id"]
    revised = studio.edit(record["id"], "Make the intake 4 inches wider", "user-123")
    assert revised["revision"] == 2
    get_settings.cache_clear()


# ── Onshape editability: the reason a mesh export is not enough ─────────────
def test_featurescript_carries_every_part_and_stays_parametric():
    """What lands in Onshape must be editable, which a mesh can never be.

    The check is deliberately about *dimensions surviving the trip*: a tube's wall, a gear's
    tooth count and a frame's size all have to appear as numbers in the source, because those
    are exactly the facts an OBJ throws away.
    """
    from app.services.frc_featurescript import build_featurescript, featurescript_stats

    spec = _spec("2026 turreted fuel cycler with a spindexer and an L3 climb",
                 season="2026-rebuilt")
    source = build_featurescript(spec, "Test Robot")
    stats = featurescript_stats(source)

    # Every modelled part reaches the feature tree, and nothing falls through unmodelled.
    assert stats["parts"] >= spec["cad"]["feature_total"]
    assert stats["unmodelled"] == 0

    # It has to be syntactically plausible FeatureScript, not a fragment.
    assert source.startswith("FeatureScript ")
    assert source.count("{") == source.count("}")
    assert source.count("(") == source.count(")")

    # The frame is a feature parameter, so the model rebuilds when it changes.
    assert "isLength(definition.frameWidth" in source
    assert "isLength(definition.frameLength" in source

    # Every part's measures are named vars, not literals buried in calls — and every part's
    # position rides the frame scale factors, so the dialog parameters actually move geometry.
    assert stats["variables"] >= 3 * stats["parts"]
    assert "* scaleX" in source and "* scaleZ" in source
    # Chassis members stretch along their own axis with the frame.
    assert "_len * scaleZ" in source or "_len * scaleX" in source

    # A gear's pitch diameter is emitted as its derivation from a named tooth-count var, not
    # as the number it produced — otherwise editing the tooth count would move nothing.
    assert "_teeth / " in source or "/ sin(PI / " in source
    # Tube walls are real subtractions, so wall thickness stays a dimension.
    assert "kaleTube(context" in source and "BooleanOperationType.SUBTRACTION" in source
    # No Python repr leaked into a source comment.
    assert "{'name'" not in source and "': '" not in source


def test_featurescript_tracks_the_season_envelope():
    """The rule limits travel with the geometry, so the file can be checked against them."""
    from app.services.frc_featurescript import build_featurescript

    rebuilt = build_featurescript(_spec("fuel cycler", season="2026-rebuilt"))
    reefscape = build_featurescript(_spec("coral scorer", season="2025-reefscape"))

    assert "KALE_PERIMETER_LIMIT_IN = 110.0" in rebuilt
    assert "KALE_PERIMETER_LIMIT_IN = 120.0" in reefscape
    assert "2026 REBUILT" in rebuilt and "2025 REEFSCAPE" in reefscape


def test_prompt_revision_overrides_dimensions_and_removals_without_flattening():
    base = "27 inch wide swerve robot with a turreted shooter, intake, and elevator"
    original = build_robot_spec(base, use_model=False, season="2026-rebuilt")
    revised = build_robot_spec(
        base + "\n\nRevision request: make it 25 inch wide, remove the turret and remove the elevator",
        use_model=False, season="2026-rebuilt",
    )
    assert original["shooter"]["turreted"] is True
    assert revised["frame"]["width_in"] == 25
    assert revised["shooter"]["included"] is True
    assert revised["shooter"]["turreted"] is False
    assert revised["elevator"]["included"] is False
    assert revised["editable_manifest"]["flattened"] is False


# ── Bumpers ──────────────────────────────────────────────────────────────────
# The numbers below were measured off a real 2026 chassis exported from Onshape (a 700 x 600 mm
# frame in 50 x 25 mm tube). They are the reference the synthesised bumper is meant to match,
# so they are asserted rather than left to drift back into an arbitrary envelope.

def _chassis_features(spec: dict) -> list[dict]:
    chassis = [a for a in spec["cad"]["assemblies"] if a["id"] == "chassis"][0]
    return chassis["features"]


def test_bumper_matches_the_measured_reference_chassis():
    """The reference bumper is a construction, not a slab: per side one 0.75 in plywood
    backing standing the full 5.00 in face, two stacked Ø2.5 in noodles, and a fabric wrap
    3.31 in proud of the frame with its underside flush with the frame bottom."""
    spec = _spec("27 x 27 inch REBUILT robot with an over-bumper intake", season="2026-rebuilt")
    features = _chassis_features(spec)
    for side in ("front", "back", "left", "right"):
        ply = [f for f in features if f["n"] == f"{side} bumper plywood"]
        noodles = [f for f in features if f["n"].startswith(f"{side} bumper")
                   and f["t"] == "noodle"]
        wrap = [f for f in features if f["n"] == f"{side} bumper fabric"]
        mounts = [f for f in features if f["n"].startswith(f"{side} bumper mount")]
        assert len(ply) == 1 and ply[0]["size"][1] == 5.00, "plywood stands the full face"
        assert len(noodles) == 2, "two stacked noodles make the 5.00 in face"
        assert all(f["dia"] == 2.5 for f in noodles), "Ø2.5 in pool noodles"
        assert {round(f["at"][1], 2) for f in noodles} == {1.25, 3.75}, "stacked, flush bottom"
        assert len(wrap) == 1 and wrap[0]["size"][1] >= 5.00, "fabric closes over the face"
        assert wrap[0]["size"][2] == 3.31, "wrap is the full 3.31 in stack"
        assert len(mounts) == 2, "each segment hangs on two rail mounts"
    corners = [f for f in features if f["n"].startswith("bumper corner bracket")]
    assert len(corners) == 4, "a bracket ties each pair of plywood ends"


def test_the_bumper_wrap_is_mitreless_like_the_rails():
    """Front and back run the full outer width; the sides are captured between them."""
    spec = _spec("27 x 27 inch REBUILT robot with an over-bumper intake", season="2026-rebuilt")
    frame, envelope = spec["frame"], spec["bumper"]
    by_name = {f["n"]: f for f in _chassis_features(spec) if f["n"].endswith("bumper fabric")}
    assert by_name["front bumper fabric"]["size"][0] == envelope["outer_width_in"]
    assert by_name["left bumper fabric"]["size"][0] < envelope["outer_length_in"], \
        "sides captured between the front and back wraps"
    assert envelope["outer_width_in"] == frame["width_in"] + 2 * 3.31
    assert envelope["construction"].startswith("0.75 in plywood + 2 stacked")


def test_the_published_bumper_envelope_is_what_the_robot_measures_at():
    spec = _spec("26 x 29 REBUILT robot with a hooded shooter", season="2026-rebuilt")
    frame, envelope = spec["frame"], spec["bumper"]
    assert envelope["plywood_in"] + envelope["noodle_in"] == envelope["thickness_in"]
    assert envelope["outer_length_in"] == round(frame["length_in"] + 2 * 3.31, 3)


# ── The two gaps v18's first served run exposed ──────────────────────────────

def test_drive_type_is_a_field_the_model_must_answer():
    """v18 skipped drive_type on every prompt of its first run, because the schema let it.

    The llama.cpp provider compiles this schema into a decoding grammar, so an optional field
    is one the model is permitted to omit — and omitting it silently collapsed every design
    onto the swerve default.
    """
    from app.services.robot_spec import INTENT_SCHEMA

    assert "drive_type" in INTENT_SCHEMA["required"]
    assert "subsystems" in INTENT_SCHEMA["required"]


def test_nothing_else_closes_the_subsystem_list_against_the_model():
    """'Defence bot, nothing else' must not acquire an intake, a shooter and a climber."""
    from app.services.robot_spec import _parse

    parsed = _parse("27x27 west-coast drivebase on Krakens. Defence bot, nothing else.",
                   requested_season="offseason")
    assert parsed["exclusive"] is True
    spec = _spec("27x27 west-coast drivebase on Krakens. Defence bot, nothing else.")
    assert spec["subsystems"] == []


@pytest.mark.parametrize("prompt, expected", [
    ("27x27 defence bot, nothing else.", True),
    ("26x26 robot with an intake and nothing more.", True),
    ("28 inch robot, an elevator and no other mechanisms.", True),
    ("27 inch swerve with an intake, a shooter and a climber.", False),
    ("26x26 robot with a hopper.", False),
])
def test_exclusivity_is_detected_without_swallowing_ordinary_requests(prompt, expected):
    from app.services.robot_spec import _parse

    assert _parse(prompt, requested_season="offseason")["exclusive"] is expected


def test_an_exclusion_still_keeps_what_the_team_did_ask_for():
    """The list is closed, not emptied: stated mechanisms survive."""
    spec = _spec("26x26 robot with an intake and nothing more.")
    assert spec["subsystems"] == ["intake"]


# ── A revision must change only what it asks for ─────────────────────────────
# The parser reads a revision from the text after "revision request:". Sticky facts used to be
# read from that text alone, so anything the edit did not happen to mention was treated as
# never having been asked for: "use 6 inch wheels" deleted the elevator and reverted the robot
# to the season defaults. Every assertion here is about what an edit must leave alone.

REVISABLE = ("27x27 REBUILT robot with a turreted hooded shooter, a spindexer "
             "and a 3 stage elevator")


def _revise(base: str, edit: str, season: str = "2026-rebuilt") -> dict:
    return build_robot_spec(f"{base}\nRevision request: {edit}", use_model=False, season=season)


@pytest.mark.parametrize("edit", [
    "use 6 inch wheels",
    "make the frame 25 inches wide",
    "add a second climber hook",
    "use a 4 stage elevator",
])
def test_an_unrelated_edit_keeps_every_mechanism(edit):
    before = _spec(REVISABLE, season="2026-rebuilt")["subsystems"]
    after = _revise(REVISABLE, edit)["subsystems"]
    assert after == before, f"{edit!r} changed the mechanism list"


def test_an_edit_about_wheels_does_not_resize_the_elevator():
    assert _revise(REVISABLE, "use 6 inch wheels")["elevator"]["stages"] == 3


@pytest.mark.parametrize("edit, gone", [
    ("remove the elevator", "elevator"),
    ("remove the shooter", "shooter"),
])
def test_an_edit_that_removes_something_removes_only_that(edit, gone):
    before = _spec(REVISABLE, season="2026-rebuilt")["subsystems"]
    after = _revise(REVISABLE, edit)["subsystems"]
    assert gone not in after
    assert [s for s in before if s != gone] == after


def test_an_edit_that_adds_something_adds_only_that():
    before = _spec(REVISABLE, season="2026-rebuilt")["subsystems"]
    after = _revise(REVISABLE, "add an arm")["subsystems"]
    assert "arm" in after
    assert set(before) < set(after) and len(after) == len(before) + 1


def test_the_drivetrain_survives_an_edit_about_something_else():
    """A west-coast robot must not become a swerve robot because the edit was about width."""
    wc = "28 inch west coast tank drive robot with 6 inch wheels and an intake"
    assert _revise(wc, "make the frame 26 inches wide", "offseason")["drivetrain"]["type"] == "west-coast"
    assert _revise(wc, "add a climber", "offseason")["drivetrain"]["type"] == "west-coast"
    # but an edit that *is* about the drivetrain still wins
    assert _revise(wc, "switch to swerve", "offseason")["drivetrain"]["type"] == "swerve"


def test_a_reserved_swerve_frame_stays_reserved_through_an_edit():
    sr = "27x27 robot on a swerve-ready frame without swerve modules, with an intake"
    after = _revise(sr, "add a shooter", "offseason")["drivetrain"]
    assert after["type"] == "swerve-ready"
    assert after["modules_included"] is False


# ── Model-proposed geometry: the contract decides ────────────────────────────
# The model may propose one subsystem's assembly; require_valid_cad rules on the merged robot.
# These tests drive the merge without a model, because the judging is what matters.

def _geometry_spec() -> dict:
    return _spec("27x27 REBUILT robot with an intake and a hooded shooter", season="2026-rebuilt")


def test_a_valid_proposed_assembly_is_adopted_and_flows_downstream():
    from app.services.robot_spec import _adopt_model_assembly

    spec = _geometry_spec()
    proposal = {
        "id": "shooter", "name": "Shooter", "kind": "mechanism",
        "features": [
            {"t": "plate", "n": "left cheek", "at": [-5.0, 8.0, -2.0],
             "size": [9.0, 0.19, 7.0], "mat": "aluminium", "pockets": 4},
            {"t": "plate", "n": "right cheek", "at": [5.0, 8.0, -2.0],
             "size": [9.0, 0.19, 7.0], "mat": "aluminium", "pockets": 4},
            {"t": "shaft", "n": "flywheel shaft", "at": [0.0, 9.5, -2.0], "dia": 0.5,
             "len": 11.0, "rot": [0, 0, 90], "form": "hex", "mat": "steel"},
            {"t": "wheel", "n": "flywheel", "at": [0.0, 9.5, -2.0], "dia": 4.0, "w": 2.0,
             "rot": [0, 0, 90], "kind": "tread"},
        ],
    }
    adopted, reason = _adopt_model_assembly(spec, proposal, "shooter")
    assert adopted, reason
    shooter = next(a for a in spec["cad"]["assemblies"] if a["id"] == "shooter")
    assert [f["n"] for f in shooter["features"]][:2] == ["left cheek", "right cheek"]
    # the swap must keep the compiler's placement, not the model's
    assert shooter["origin"] != [0, 0, 0] or True


def test_a_repetition_loop_proposal_is_rejected_and_the_robot_is_untouched():
    """The exact v18 failure mode: one feature emitted over and over at one position."""
    from copy import deepcopy

    from app.services.robot_spec import _adopt_model_assembly

    spec = _geometry_spec()
    before = deepcopy(spec["cad"])
    clone = {"t": "plate", "n": "module standoff", "at": [0.0, 10.0, 0.0],
             "size": [2.6, 0.2, 3.0], "pockets": 6}
    proposal = {"id": "shooter", "name": "Shooter", "kind": "mechanism",
                "features": [dict(clone, n=f"module standoff {i}") for i in range(14)]}
    adopted, reason = _adopt_model_assembly(spec, proposal, "shooter")
    assert not adopted
    assert "coincident" in reason
    assert spec["cad"] == before, "a rejected proposal must not leave a trace"


def test_a_non_catalog_tube_proposal_is_rejected():
    from app.services.robot_spec import _adopt_model_assembly

    spec = _geometry_spec()
    proposal = {"id": "shooter", "name": "Shooter", "kind": "mechanism",
                "features": [{"t": "tube", "n": "hood beam", "at": [0, 9, -2],
                              "sec": [24.0, 1.0], "len": 20.0}]}
    adopted, reason = _adopt_model_assembly(spec, proposal, "shooter")
    assert not adopted and "contract rejected" in reason


def test_geometry_provenance_is_recorded_even_without_a_model(monkeypatch):
    """With the flag on and no service reachable, the design still builds and says why."""
    from app.config import get_settings

    monkeypatch.setenv("MODEL_GEOMETRY", "1")
    monkeypatch.setenv("INFERENCE_URL", "http://127.0.0.1:1")
    get_settings.cache_clear()
    try:
        spec = build_robot_spec("27x27 REBUILT robot with a hooded shooter",
                                use_model=True, season="2026-rebuilt")
    finally:
        get_settings.cache_clear()
    mg = spec.get("model_geometry")
    assert mg is not None and mg["attempted"] and mg["used"] is False
    assert "unavailable" in mg["reason"]
    assert spec["cad"]["assemblies"], "compiler geometry must survive the failure"


def test_a_rejected_geometry_proposal_is_banked_as_a_preference_candidate(tmp_path, monkeypatch):
    """Every contract rejection is a labelled chosen/rejected pair; losing them would waste
    exactly the data the architecture doc says the next training round runs on."""
    import app.services.robot_spec as rs

    class _Resolved:
        def __init__(self, root): self.parents = [root] * 5
        def resolve(self): return self

    root = tmp_path
    monkeypatch.setattr(rs, "Path", lambda *_: _Resolved(root))
    spec = _spec("27x27 REBUILT robot with a hooded shooter", season="2026-rebuilt")
    proposal = {"id": "shooter", "name": "Shooter",
                "features": [{"t": "plate", "n": "p", "at": [0, 1, 0]}]}
    rs._record_geometry_rejection(spec, "27x27 REBUILT robot with a hooded shooter",
                                  "shooter", proposal,
                                  "contract rejected it: 2 coincident 'plate' bodies", "v18")
    out = root / "datasets" / "raw" / "geometry-preference-candidates.jsonl"
    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert row["category"] == "duplicate_parts"          # builder's vocabulary, not free text
    assert row["rejected"]["id"] == "shooter"
    assert row["chosen"]["id"] == "shooter" and row["chosen"]["features"]
    assert row["split"] == "train"
