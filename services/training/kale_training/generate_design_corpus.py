"""Generate Kale's synthetic design-synthesis SFT corpus.

The corpus contains no copied CAD or third-party prose.  It turns internal engineering
templates into detailed prompt/JSON pairs with explicit assumptions, calculations, CAD
structure, manufacturing intent, verification steps, and uncertainty.  FRC examples are
deliberately overrepresented while the same response contract covers broader hardware.

Output uses the MLX-LM chat JSONL format and includes a content-hashed provenance manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SYSTEM = (
    "You are Kale Forge Design Model, a precise hardware design copilot. Convert the user's request "
    "into one detailed JSON design plan. Use only stated facts and clearly marked assumptions. "
    "Include requirements, architecture, interfaces, first-order calculations, a parametric CAD "
    "plan, manufacturing intent, verification, risks, and questions. Never claim certification, "
    "rule compliance, structural safety, or fabrication readiness without test evidence."
)

FRC_PROFILES = [
    {"name": "compact shooter", "frame": [28, 28], "height": 29.5, "systems": ["intake", "shooter", "climber"]},
    {"name": "elevator manipulator", "frame": [28, 28], "height": 47.0, "systems": ["intake", "elevator", "wrist", "climber"]},
    {"name": "pivoting arm robot", "frame": [27.5, 27.5], "height": 46.0, "systems": ["intake", "arm", "end_effector", "climber"]},
    {"name": "bare swerve-ready chassis", "frame": [28, 28], "height": 12.0, "systems": []},
]
INTAKES = ["coaxial slapdown", "four-bar over-bumper", "pivoting under-bumper", "side roller funnel"]
SHOOTERS = ["dual flywheel hooded", "single flywheel backspin", "adjustable two-wheel", "fixed-angle flywheel"]
ARMS = ["dead-axle shoulder pivot", "double-jointed arm", "four-bar linkage", "wristed elevator carriage"]
CLIMBERS = ["dual telescoping hooks", "single pivoting hook", "winch-driven elevator carriage"]
DRIVES = ["swerve-ready without modules", "four-module swerve", "six-wheel west-coast", "four-wheel tank"]

GENERAL_TYPES = [
    "desktop robotic arm", "precision belt gearbox", "lead-screw linear actuator", "inspection-camera enclosure",
    "battery-powered sensor node", "48 V motor controller PCB", "low-noise data-acquisition PCB",
    "small conveyor module", "pump and motor mounting skid", "aluminum electronics enclosure",
    "drone payload bracket", "thermal test fixture", "automated cable test fixture", "differential-drive mobile robot",
    "two-axis belt-driven inspection gantry", "passively cooled compute-module heat-sink bracket",
    "injection-molded handheld electronics enclosure", "sealed industrial controller enclosure",
    "camera positioning gimbal", "ball-screw lifting column", "vacuum end-effector",
]


def _round(value: float, places: int = 2) -> float:
    return round(value, places)


def _frc_example(rng: random.Random, index: int) -> tuple[str, dict[str, Any]]:
    profile = rng.choice(FRC_PROFILES)
    width, length = profile["frame"]
    drive = rng.choice(DRIVES)
    intake = rng.choice(INTAKES)
    shooter = rng.choice(SHOOTERS)
    arm = rng.choice(ARMS)
    climber = rng.choice(CLIMBERS)
    systems = list(profile["systems"])
    if "intake" in systems: systems[systems.index("intake")] = intake
    if "shooter" in systems: systems[systems.index("shooter")] = shooter
    if "arm" in systems: systems[systems.index("arm")] = arm
    if "climber" in systems: systems[systems.index("climber")] = climber
    target_mass = rng.choice([95, 100, 105, 110])
    wheel_diameter = rng.choice([3.0, 4.0, 6.0])
    motor_rpm = rng.choice([5800, 6000, 6784])
    reduction = rng.choice([1.0, 1.25, 1.5, 2.0])
    wheel_rpm = motor_rpm / reduction
    surface_speed = wheel_rpm * 3.1415926536 * wheel_diameter / 60.0
    prompt = (
        f"Design a detailed full-scale FRC {profile['name']} on a {width} × {length} inch frame. "
        f"Use a {drive} drivebase. Include {', '.join(systems) if systems else 'only the serviceable chassis and electronics packaging'}. "
        "Give me the subsystem breakdown, dimensions, interfaces, calculations, CAD feature tree, mates, BOM intent, and validation plan."
    )
    subassemblies = ["chassis", "bellypan", "electronics", "bumpers/interface"]
    if systems: subassemblies.extend(systems)
    output = {
        "design_type": "frc_robot",
        "title": f"Parametric {profile['name']}",
        "requirements": {
            "frame_envelope_in": {"width": width, "length": length, "starting_height_target": profile["height"]},
            "team_mass_target_lb": target_mass,
            "drivebase": drive,
            "requested_subsystems": systems,
            "constraints_to_confirm": ["current-season robot rules", "bumper geometry", "extension limits", "inspection requirements", "gamepiece geometry"],
        },
        "assumptions": [
            "Frame rails use 6061-T6 rectangular tube unless the team specifies another process.",
            "Dimensions are design targets, not a statement of current-season legality.",
            "Loads are preliminary and require measured mechanism mass, acceleration, and duty cycle.",
        ],
        "architecture": {
            "top_level_assemblies": subassemblies,
            "chassis": {"rail": "2 × 1 × 0.125 in tube", "crossmembers": 3, "bellypan": "0.090 in aluminum, riveted", "service_strategy": "removable electronics panels and unobstructed fastener access"},
            "mechanisms": {
                "intake": {"type": intake, "roller_width_in": _round(width - 5.0), "pivot_support": "two bearing-supported side plates", "included": any("intake" in s for s in systems)},
                "shooter": {"type": shooter, "wheel_diameter_in": wheel_diameter, "nominal_motor_rpm": motor_rpm, "reduction": reduction, "included": any("shooter" in s for s in systems)},
                "manipulator": {"type": arm, "dead_axle_preferred": True, "included": any(key in " ".join(systems) for key in ("arm", "wrist", "effector", "elevator"))},
                "climber": {"type": climber, "independent_left_right_structure": True, "included": any("climber" in s for s in systems)},
            },
        },
        "interfaces": [
            {"from": "drivebase", "to": "chassis", "definition": "corner/module keep-outs, rail datums, fastener pattern"},
            {"from": "mechanisms", "to": "frame", "definition": "master mounting sketches with hole-table coordinates"},
            {"from": "electronics", "to": "mechanisms", "definition": "connector, current estimate, sensor, cable path, and service loop per actuator"},
        ],
        "calculations": [
            {"name": "shooter wheel surface speed", "formula": "motor_rpm / reduction × π × wheel_diameter / 60", "result_in_per_s": _round(surface_speed), "status": "first-order; validate slip and gamepiece compression experimentally"},
            {"name": "climber design load", "formula": "team_mass_target × 2.0 dynamic factor", "result_lbf": target_mass * 2.0, "status": "apply to each credible load path and verify with proof testing"},
            {"name": "center-of-mass budget", "formula": "sum(component_mass × component_position) / total_mass", "result": "pending measured component masses", "status": "keep battery and drive motors low; update from CAD mass properties"},
        ],
        "cad_plan": {
            "master_variables": ["frame_width", "frame_length", "rail_thickness", "bumper_offset", "gamepiece_diameter", "pivot_axis", "service_clearance"],
            "part_studios": ["00_Master_Layout", "10_Chassis", "20_Intake", "30_Scorer", "40_Climber", "50_Electronics", "90_Manufacturing"],
            "feature_order": ["master layout sketches", "structural members", "bearing and shaft seats", "fastener holes", "lightening features", "manufacturing fillets/chamfers"],
            "assemblies": subassemblies,
            "mate_intent": ["fastened mates for rigid subassemblies", "revolute mates at pivots", "slider mates for elevator/telescope stages", "named mate connectors at every subsystem interface"],
            "collision_sets": ["mechanism-to-frame", "mechanism-to-bumper", "moving-stage-to-wiring", "service-tool access"],
        },
        "manufacturing": [
            "Use common datums and one setup where possible for paired side plates.",
            "Call out press/slip fits by actual bearing and shaft tolerances, not nominal size alone.",
            "Add tool clearance, wire pass-throughs, edge distance, and replaceable wear parts before release.",
            "Generate a cut list, drawing pack, hardware BOM, and revision-controlled assembly instructions.",
        ],
        "verification": [
            "Run full motion and interference checks at starting, scoring, intake, and climb configurations.",
            "Update mass properties from real vendor parts and verify center of mass in critical poses.",
            "Perform shaft, fastener, tube, and plate first-order load checks with documented assumptions.",
            "Prototype gamepiece contact geometry and measure acquisition/release consistency before final plates.",
            "Inspect against the current official manual; do not infer compliance from this plan.",
        ],
        "risks": [
            {"risk": "mechanism packaging conflict", "mitigation": "single master layout and enforced keep-out bodies"},
            {"risk": "underestimated impact or climb load", "mitigation": "dynamic factor, load-path review, and controlled proof test"},
            {"risk": "unserviceable assembly", "mitigation": "tool-access bodies and timed maintenance walkthrough"},
        ],
        "questions": ["What gamepiece and scoring locations define the mechanism geometry?", "Which motors, gearboxes, and fabrication processes are available?", "What are the current-season starting and extension constraints?"],
        "confidence": 0.78,
        "limitations": ["Concept-level engineering plan; requires current rules, detailed loads, tolerance analysis, simulation, prototype testing, and qualified review."],
        "example_id": f"frc-{index:04d}",
    }
    return prompt, output


def _general_example(rng: random.Random, index: int) -> tuple[str, dict[str, Any]]:
    kind = rng.choice(GENERAL_TYPES)
    envelope = [rng.choice([120, 180, 250, 400, 600]), rng.choice([80, 150, 240, 350]), rng.choice([40, 100, 180, 300])]
    environment = rng.choice(["indoor laboratory", "light industrial", "mobile outdoor", "electronics bench"])
    target_life = rng.choice([1000, 5000, 10000, 25000])
    prompt = (
        f"Create a precise, manufacturable concept for a {kind} used in a {environment} environment. "
        f"Keep the first packaging envelope near {envelope[0]} × {envelope[1]} × {envelope[2]} mm and target {target_life} operating cycles. "
        "Return requirements, architecture, interfaces, calculations, parametric CAD structure, manufacturing, and verification details."
    )
    is_pcb = "PCB" in kind or "sensor node" in kind
    is_thermal = "thermal" in kind or "heat-sink" in kind
    is_gantry = "gantry" in kind or "conveyor" in kind or "linear actuator" in kind or "lifting column" in kind
    is_enclosure = "enclosure" in kind
    if is_pcb:
        modules = ["protected power entry", "power conversion or switching", "controller and communications", "sensing", "connectors and test points", "board-to-enclosure interface"]
        domain_calculation = {"name": "copper and thermal budget", "formula": "I²R loss plus switching loss; temperature rise from measured or vendor thermal impedance", "result": "pending stackup, copper weight, airflow, switching frequency, and component loss data"}
        domain_parameters = ["board_x", "board_y", "layer_stack", "power_keepout", "creepage", "clearance", "mount_pattern", "connector_locations"]
        domain_test = "Bring up from a current-limited supply; verify rails, protections, communications, sensing calibration, load transients, faults, and temperature rise."
    elif is_thermal:
        modules = ["device interface", "thermal interface material", "conduction spreader", "finned or chassis heat path", "structural mounting", "instrumentation points"]
        domain_calculation = {"name": "thermal-resistance budget", "formula": "Rθ_total_allowed = (T_case_limit - T_ambient) / heat_load", "result": "pending allowed case temperature, ambient, contact resistance, orientation, airflow, and emissivity"}
        domain_parameters = ["device_footprint", "interface_flatness", "base_thickness", "fin_height", "fin_spacing", "mount_pattern", "contact_pressure"]
        domain_test = "Instrument device, interface, base, fins, and ambient; test worst-case power and orientation to steady state, then perform shock and vibration checks."
    elif is_gantry:
        modules = ["frame and guarding", "linear guides", "moving carriage", "belt or screw transmission", "motor and reduction", "homing and limits", "cable management", "controls"]
        domain_calculation = {"name": "axis force and drive sizing", "formula": "F = m × a + friction + process force; torque = F × drive_radius / efficiency", "result": "pending acceleration, friction, process force, duty cycle, and safety factor"}
        domain_parameters = ["travel_x", "travel_y", "payload", "rail_spacing", "carriage_span", "drive_pitch", "belt_width", "cable_bend_radius"]
        domain_test = "Map travel accuracy and repeatability under payload; verify homing, limit response, cable-chain motion, guarding, deflection, temperature, and endurance."
    elif is_enclosure:
        modules = ["PCB and component keep-outs", "front and rear housing", "display and connector openings", "bosses and fastening", "gasket or seam", "vents", "battery or service access"]
        domain_calculation = {"name": "enclosure tolerance stack", "formula": "worst-case and RSS stack across PCB datums, bosses, openings, seals, and mating shells", "result": "pending supplier tolerances, process capability, gasket compression, and cosmetic-gap targets"}
        domain_parameters = ["pcb_envelope", "component_keepouts", "wall_thickness", "draft_angle", "rib_ratio", "boss_diameter", "seam", "gasket_compression"]
        domain_test = "Inspect critical dimensions and assembly force; test connectors, buttons, thermal behavior, drop impact, seam or ingress target, service cycles, and cosmetic gaps."
    else:
        modules = ["primary structure", "actuation/power", "sensing/control", "interfaces", "guards/enclosure"]
        domain_calculation = {"name": "load and power budget", "formula": "peak load × speed / assumed efficiency", "result": "pending load, speed, and efficiency inputs"}
        domain_parameters = ["envelope_x", "envelope_y", "envelope_z", "mount_pattern", "wall_thickness", "service_clearance", "cable_bend_radius"]
        domain_test = "Test dimensional, structural, electrical, thermal, motion, and life requirements with recorded pass/fail evidence."
    output = {
        "design_type": "electromechanical" if not is_pcb else "electronic_assembly",
        "title": kind.title(),
        "requirements": {"target_envelope_mm": envelope, "environment": environment, "design_life_cycles": target_life, "maintainability": "replace wear and electrical interface parts without destroying primary structure"},
        "assumptions": ["Loads, duty cycle, ingress exposure, supply limits, and regulatory market are not fully specified.", "All component selections remain provisional until vendor data and availability are verified."],
        "architecture": {
            "modules": modules,
            "design_strategy": "separate load paths, alignment features, power/data interfaces, and replaceable wear components",
            "electrical_strategy": "protected input, explicit grounding, accessible test points, connector keying, and derated components" if is_pcb else "document motor, sensor, grounding, connector, and cable-chain interfaces",
        },
        "interfaces": [
            {"name": "mounting", "definition": "datum scheme, hole pattern, allowable reaction loads, installation access"},
            {"name": "power", "definition": "voltage range, peak/continuous current, protection, connector, grounding"},
            {"name": "control", "definition": "signal levels, protocol, update rate, fault state, connector pinout"},
            {"name": "environment", "definition": "temperature, debris/liquid exposure, vibration, access and guarding"},
        ],
        "calculations": [
            domain_calculation,
            {"name": "design-life check", "formula": "cycles per hour × operating hours × service life", "target_cycles": target_life, "result": "select bearings, relays, connectors, and flexing cables above target with margin"},
            {"name": "tolerance stack", "formula": "worst-case and RSS across locating features", "result": "pending supplier tolerances and functional clearance"},
        ],
        "cad_plan": {
            "master_parameters": domain_parameters,
            "subassemblies": ["layout/skeleton", "structure", "drive or power", "controls", "covers", "manufacturing aids"],
            "feature_order": ["functional datums", "load-bearing geometry", "locating interfaces", "fasteners", "cable/air paths", "guards", "manufacturing details"],
            "drawing_requirements": ["GD&T only on function-driving interfaces", "material and finish", "critical dimensions", "inspection method", "revision"],
        },
        "manufacturing": ["Choose processes from required tolerance, quantity, material, and available tooling.", "Avoid inaccessible fasteners and trapped replaceable parts.", "Use standard stock thicknesses, bearings, shafts, fasteners, connectors, and board stackups where practical.", "Create inspection fixtures for repeat-critical datums."],
        "verification": ["Requirements review with pass/fail acceptance criteria.", "Tolerance and interference analysis at environmental extremes.", "Prototype or coupon test for the highest-uncertainty interface.", domain_test, "Independent design review before production release."],
        "risks": [
            {"risk": "requirements ambiguity", "mitigation": "freeze measurable loads, environment, interfaces, and acceptance criteria before detailed CAD"},
            {"risk": "tolerance-driven assembly failure", "mitigation": "functional datum scheme and explicit tolerance stack"},
            {"risk": "component or process mismatch", "mitigation": "supplier verification and prototype the critical process"},
        ],
        "questions": ["What are the continuous and peak loads?", "What fabrication processes and inspection tools are available?", "What supply, interface, environmental, safety, and regulatory constraints apply?"],
        "confidence": 0.64,
        "limitations": ["The plan is intentionally incomplete until loads, interfaces, environment, and acceptance criteria are confirmed."],
        "example_id": f"general-{index:04d}",
    }
    return prompt, output


def _row(prompt: str, output: dict[str, Any], source_id: str) -> dict[str, Any]:
    training_output = {key: value for key, value in output.items() if key != "example_id"}
    return {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": json.dumps(training_output, separators=(",", ":"), sort_keys=True)},
        ],
        "metadata": {"id": source_id, "source": "internal-synthetic-template", "license": "internal", "reviewed": True},
    }


def generate(output_dir: Path, count: int = 260, frc_ratio: float = 0.7, seed: int = 42) -> dict[str, Any]:
    rng = random.Random(seed)
    rows = []
    frc_count = round(count * frc_ratio)
    for index in range(count):
        prompt, response = _frc_example(rng, index) if index < frc_count else _general_example(rng, index)
        rows.append(_row(prompt, response, response["example_id"]))
    rng.shuffle(rows)
    train_end = round(count * 0.80)
    valid_end = round(count * 0.90)
    splits = {"train": rows[:train_end], "valid": rows[train_end:valid_end], "test": rows[valid_end:]}
    output_dir.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, items in splits.items():
        path = output_dir / f"{name}.jsonl"
        payload = "".join(json.dumps(item, separators=(",", ":"), sort_keys=True) + "\n" for item in items)
        path.write_text(payload)
        hashes[name] = hashlib.sha256(payload.encode()).hexdigest()
    manifest = {
        "version": output_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "generator": "kale_training.generate_design_corpus",
        "seed": seed,
        "row_count": count,
        "frc_rows": frc_count,
        "general_design_rows": count - frc_count,
        "split_counts": {name: len(items) for name, items in splits.items()},
        "sha256": hashes,
        "license": "internal",
        "provenance": "Template-generated by Kale; no third-party prose, CAD, meshes, or user data copied.",
        "reference_policy": "Public manuals and design libraries inform topic coverage only; exact rules must be checked at use time.",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--count", type=int, default=260)
    parser.add_argument("--frc-ratio", type=float, default=0.70)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    manifest = generate(Path(args.out), args.count, args.frc_ratio, args.seed)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
