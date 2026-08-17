import math

from app.services.cad_contract import structural_report, transmission_report
from app.services.frc_cad import BUMPER_FABRIC_IN, BUMPER_HEIGHT_IN, BUMPER_THICKNESS_IN
from app.services.robot_spec import build_robot_spec
from app.services.transmission_geom import transmission_issues


PROMPT = (
    "27 x 27 inch 2026 REBUILT robot on MK4i swerve with a dual-roller "
    "over-bumper intake, circular spindexer, turreted hooded shooter, and "
    "telescoping climber"
)


def _intake():
    spec = build_robot_spec(PROMPT, use_model=False, season="2026-rebuilt")
    assembly = next(a for a in spec["cad"]["assemblies"] if a["id"] == "intake")
    return spec, assembly, {f["n"]: f for f in assembly["features"]}


def test_week_four_intake_uses_dimensioned_plate_pulley_belt_layout():
    spec, _, parts = _intake()
    required = {
        "pivot dead axle", "intake arm left", "intake arm right",
        "roller 1 shaft", "roller 2 shaft", "roller 1 pulley", "roller 2 pulley",
        "intake drive plate", "intake gearbox", "intake motor",
        "gearbox output pulley", "reduction run", "roller 1→2 run",
    }
    assert required <= parts.keys()
    assert structural_report(spec["cad"])["ok"]
    assert transmission_report(spec["cad"])["ok"]

    output = parts["gearbox output pulley"]
    driven = parts["roller 2 pulley"]
    distance = math.dist(output["at"], driven["at"])
    assert distance >= (output["pd"] + driven["pd"]) / 2
    assert parts["reduction run"]["at"] == output["at"]
    assert parts["reduction run"]["to"] == driven["at"]


def test_motor_stacks_beside_gearbox_instead_of_inside_it():
    _, _, parts = _intake()
    gearbox = parts["intake gearbox"]
    motor = parts["intake motor"]
    separation = abs(motor["at"][0] - gearbox["at"][0])
    required = gearbox["size"][0] / 2 + motor["len"] / 2
    assert separation >= required


def test_pulley_interference_is_a_render_blocking_finding():
    assembly = {
        "id": "intake",
        "features": [
            {"t": "pulley", "n": "a", "at": [0, 0, 0], "rot": [0, 0, 90],
             "teeth": 36, "pitch_mm": 5.0, "pd": 2.256, "w": 0.45},
            {"t": "pulley", "n": "b", "at": [0, 0.6, 0], "rot": [0, 0, 90],
             "teeth": 18, "pitch_mm": 5.0, "pd": 1.128, "w": 0.45},
        ],
    }
    issues = transmission_issues([assembly])
    assert any(issue["type"] == "pulley_interference" for issue in issues)


def _rotate_x(point, pivot, degrees):
    angle = math.radians(degrees)
    x, y, z = point
    dy, dz = y - pivot[1], z - pivot[2]
    return [x, pivot[1] + dy * math.cos(angle) - dz * math.sin(angle),
            pivot[2] + dy * math.sin(angle) + dz * math.cos(angle)]


def _rotated_box_samples(feature):
    """Dense box samples are enough to catch a plate crossing the bumper slab."""
    if feature["t"] in {"plate", "polycarb", "gearbox"}:
        sx, sy, sz = feature["size"]
    elif feature["t"] == "motor":
        sx, sy, sz = feature["dia"], feature["len"], feature["dia"]
    elif feature["t"] == "wheel":
        sx, sy, sz = feature["dia"], feature["w"], feature["dia"]
    elif feature["t"] == "shaft":
        sx, sy, sz = feature["dia"], feature["len"], feature["dia"]
    elif feature["t"] in {"pulley", "sprocket"}:
        sx, sy, sz = feature["pd"], feature["w"], feature["pd"]
    elif feature["t"] == "gear":
        sx, sy, sz = feature["pd"], feature["face"], feature["pd"]
    elif feature["t"] == "standoff":
        sx, sy, sz = feature["dia"], feature["len"], feature["dia"]
    else:
        return [feature["at"]]
    rx, _, rz = [math.radians(v) for v in feature.get("rot", [0, 0, 0])]
    samples = []
    for x in (-sx / 2, 0, sx / 2):
        for y in (-sy / 2, 0, sy / 2):
            for z in (-sz / 2, 0, sz / 2):
                # Three.js uses XYZ Euler order. These intake primitives only require X/Z.
                xx, yy, zz = x, y, z
                yy, zz = (yy * math.cos(rx) - zz * math.sin(rx),
                          yy * math.sin(rx) + zz * math.cos(rx))
                xx, yy = (xx * math.cos(rz) - yy * math.sin(rz),
                          xx * math.sin(rz) + yy * math.cos(rz))
                samples.append([feature["at"][0] + xx, feature["at"][1] + yy,
                                feature["at"][2] + zz])
    return samples


def test_intake_solids_never_enter_the_front_bumper_through_the_deploy_arc():
    spec, assembly, _ = _intake()
    pivot = assembly["articulation"]["at"]
    static = set(assembly["articulation"]["static"])
    bumper_outer_z = -(BUMPER_THICKNESS_IN + BUMPER_FABRIC_IN)
    assert pivot[2] <= bumper_outer_z - 1.4

    moving = [f for f in assembly["features"] if f["n"] not in static]
    for degrees in range(0, 79, 6):
        for feature in moving:
            points = _rotated_box_samples(feature)
            if feature.get("to"):
                points.append(feature["to"])
            for point in points:
                _, y, z = _rotate_x(point, pivot, degrees)
                if -0.05 <= y <= BUMPER_HEIGHT_IN + 0.05:
                    assert z <= bumper_outer_z, (degrees, feature["n"], y, z)

    # The chassis-mounted handoff ramp crosses inward only after it is already above the
    # bumper. It must stay static while the roller carriage pivots below it.
    guide = next(f for f in assembly["features"] if f["n"] == "indexer guide")
    assert guide["n"] in static
    assert min(p[1] for p in _rotated_box_samples(guide)) > BUMPER_HEIGHT_IN


def test_swerve_spreader_plates_stay_inside_the_frame_faces():
    spec, _, _ = _intake()
    half_w, half_l = spec["frame"]["width_in"] / 2, spec["frame"]["length_in"] / 2
    modules = [a for a in spec["cad"]["assemblies"] if a["id"].startswith("swerve_")]
    for module in modules:
        ox, _, oz = module["origin"]
        for feature in module["features"]:
            if feature["t"] != "plate" or "module" not in feature["n"]:
                continue
            assert abs(ox + feature["at"][0]) + feature["size"][0] / 2 <= half_w
            assert abs(oz + feature["at"][2]) + feature["size"][2] / 2 <= half_l
