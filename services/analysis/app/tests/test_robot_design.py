import hashlib
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
def test_model_gap_is_reported_not_hidden():
    """With no inference service reachable the design still builds, and says so."""
    spec = build_robot_spec("Design a 28 inch swerve robot with an intake", use_model=True)
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

    # A gear's pitch diameter is emitted as its derivation, not as the number it produced —
    # otherwise editing the tooth count would move nothing.
    assert "/ 20" in source or "/ 20.0" in source
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
