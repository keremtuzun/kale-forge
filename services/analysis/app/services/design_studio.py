"""Prompt-driven PCB and FRC robot artifact generation.

The generators are deterministic by design: the local language model may interpret a
prompt, but every output is normalized against electrical/mechanical guardrails before
files are written.  Designs are revisioned JSON documents, independent from the review DB.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import secrets
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services import frc_parts
from app.services.robot_spec import build_robot_spec


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value[:48] or "kale-design"


def _number(prompt: str, pattern: str, default: float) -> float:
    matches = re.findall(pattern, prompt, re.I)
    if not matches:
        return default
    value = matches[-1]
    if isinstance(value, tuple):
        value = next((item for item in value if item), "")
    return float(value)


def _title(prompt: str, kind: str) -> str:
    cleaned = re.sub(r"\s+", " ", prompt).strip()
    words = cleaned.split()[:7]
    return (" ".join(words).title() if words else f"New {kind.title()} Design")[:72]


class _RobotMesh:
    """Small procedural OBJ builder for dimensioned FRC concept geometry."""

    def __init__(self) -> None:
        self.vertices: list[tuple[float, float, float]] = []
        self.groups: list[tuple[str, list[list[int]]]] = []
        self.components: list[dict[str, Any]] = []

    def _append(self, name: str, vertices: list[tuple[float, float, float]], faces: list[tuple[int, ...]],
                subsystem: str, role: str) -> None:
        base = len(self.vertices) + 1
        self.vertices.extend(vertices)
        self.groups.append((name, [[base + index for index in face] for face in faces]))
        xs, ys, zs = zip(*vertices)
        self.components.append({"name": name, "subsystem": subsystem, "role": role,
                                "bounds_mm": {"min": [min(xs), min(ys), min(zs)],
                                              "max": [max(xs), max(ys), max(zs)]}})

    def box(self, name: str, dx: float, dy: float, dz: float, cx: float, cy: float, cz: float,
            subsystem: str, role: str) -> None:
        vertices = [(cx + sx * dx / 2, cy + sy * dy / 2, cz + sz * dz / 2)
                    for sx, sy, sz in ((-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),
                                       (-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1))]
        self._append(name, vertices, [(0,1,2,3),(4,7,6,5),(0,4,5,1),(1,5,6,2),(2,6,7,3),(4,0,3,7)],
                     subsystem, role)

    def cylinder_x(self, name: str, length: float, diameter: float, cx: float, cy: float, cz: float,
                   subsystem: str, role: str, segments: int = 18) -> None:
        radius = diameter / 2
        vertices: list[tuple[float, float, float]] = []
        for x in (cx - length / 2, cx + length / 2):
            vertices.extend((x, cy + radius * math.cos(2 * math.pi * i / segments),
                             cz + radius * math.sin(2 * math.pi * i / segments)) for i in range(segments))
        faces: list[tuple[int, ...]] = [tuple(range(segments - 1, -1, -1)), tuple(range(segments, 2 * segments))]
        for i in range(segments):
            nxt = (i + 1) % segments
            faces.append((i, nxt, segments + nxt, segments + i))
        self._append(name, vertices, faces, subsystem, role)

    def cylinder_z(self, name: str, height: float, diameter: float, cx: float, cy: float, cz: float,
                   subsystem: str, role: str, segments: int = 18) -> None:
        radius = diameter / 2
        vertices: list[tuple[float, float, float]] = []
        for z in (cz - height / 2, cz + height / 2):
            vertices.extend((cx + radius * math.cos(2 * math.pi * i / segments),
                             cy + radius * math.sin(2 * math.pi * i / segments), z) for i in range(segments))
        faces: list[tuple[int, ...]] = [tuple(range(segments - 1, -1, -1)), tuple(range(segments, 2 * segments))]
        for i in range(segments):
            nxt = (i + 1) % segments
            faces.append((i, nxt, segments + nxt, segments + i))
        self._append(name, vertices, faces, subsystem, role)

    def beam_yz(self, name: str, width_x: float, thickness: float, x: float,
                y1: float, z1: float, y2: float, z2: float, subsystem: str, role: str) -> None:
        dy, dz = y2 - y1, z2 - z1
        length = max(math.hypot(dy, dz), 1e-6)
        py, pz = -dz / length * thickness / 2, dy / length * thickness / 2
        yz = [(y1 + py, z1 + pz), (y2 + py, z2 + pz), (y2 - py, z2 - pz), (y1 - py, z1 - pz)]
        vertices = [(x - width_x / 2, y, z) for y, z in yz] + [(x + width_x / 2, y, z) for y, z in yz]
        self._append(name, vertices, [(0,1,2,3),(4,7,6,5),(0,4,5,1),(1,5,6,2),(2,6,7,3),(3,7,4,0)],
                     subsystem, role)

    def obj(self, title: str, revision: int) -> str:
        lines = [f"# {title}, Kale Forge revision {revision}", "# Units = millimeters",
                 "# Procedural FRC assembly grounded in public mechanism references"]
        lines.extend(f"v {x:.3f} {y:.3f} {z:.3f}" for x, y, z in self.vertices)
        for name, faces in self.groups:
            lines.append(f"g {name}")
            lines.extend("f " + " ".join(map(str, face)) for face in faces)
        return "\n".join(lines) + "\n"


class DesignStudio:
    def __init__(self) -> None:
        root = Path(get_settings().storage_dir).resolve() / "designs"
        root.mkdir(parents=True, exist_ok=True)
        self.root = root

    def list(self, owner_id: str, include_legacy: bool = False) -> list[dict[str, Any]]:
        rows = []
        for path in self.root.glob("*/design.json"):
            try:
                row = json.loads(path.read_text())
                if row.get("owner_id") == owner_id or (include_legacy and not row.get("owner_id")):
                    rows.append(row)
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(rows, key=lambda row: row.get("updated_at", ""), reverse=True)

    def get(self, design_id: str, owner_id: str | None = None, include_legacy: bool = False) -> dict[str, Any]:
        path = self.root / design_id / "design.json"
        if not path.is_file():
            raise FileNotFoundError(design_id)
        record = json.loads(path.read_text())
        if owner_id is not None and record.get("owner_id") != owner_id and not (include_legacy and not record.get("owner_id")):
            raise FileNotFoundError(design_id)
        return record

    def artifact(self, design_id: str, name: str, owner_id: str | None = None, include_legacy: bool = False) -> Path:
        self.get(design_id, owner_id, include_legacy)
        safe = Path(name).name
        path = (self.root / design_id / safe).resolve()
        if path.parent != (self.root / design_id).resolve() or not path.is_file():
            raise FileNotFoundError(name)
        return path

    def delete(self, design_id: str, owner_id: str) -> None:
        self.get(design_id, owner_id)
        path = self.root / design_id
        if not path.is_dir():
            raise FileNotFoundError(design_id)
        shutil.rmtree(path)

    def set_onshape(self, design_id: str, owner_id: str, data: dict[str, Any]) -> dict[str, Any]:
        record = self.get(design_id, owner_id)
        record["onshape"] = {**data, "published_revision": record["revision"], "updated_at": _now()}
        record["updated_at"] = _now()
        (self.root / design_id / "design.json").write_text(json.dumps(record, indent=2))
        return record

    def create(self, kind: str, prompt: str, name: str = "", owner_id: str = "") -> dict[str, Any]:
        if kind not in {"pcb", "robot"}:
            raise ValueError("kind must be pcb or robot")
        design_id = secrets.token_hex(8)
        target = self.root / design_id
        target.mkdir()
        spec = self._pcb_spec(prompt) if kind == "pcb" else self._robot_spec(prompt)
        spec["name"] = name.strip()[:72] or _title(prompt, kind)
        record = {
            "id": design_id, "owner_id": owner_id, "kind": kind, "name": spec["name"], "status": "generated",
            "revision": 1, "prompt": prompt, "spec": spec, "history": [],
            "created_at": _now(), "updated_at": _now(), "artifacts": [],
            "warnings": self._warnings(kind),
        }
        self._render(record)
        return record

    def purge_untouched_example(self, owner_id: str) -> int:
        """Remove starter examples the owner never touched.

        Deliberately conservative: an example that was revised (revision > 1) or published to
        Onshape is the owner's work, not a starter, and is left alone. Returns how many were
        removed.
        """
        removed = 0
        for record in self.list(owner_id):
            if (record.get("is_example") and record.get("revision", 1) == 1
                    and not record.get("onshape") and not record.get("last_edit")):
                path = self.root / record["id"]
                if path.is_dir():
                    shutil.rmtree(path)
                    removed += 1
        return removed

    def create_worked_example(self, owner_id: str) -> dict[str, Any]:
        """Create the private, editable worked design, now opt-in, not automatic at signup."""
        prompt = (
            "Design a full-scale 28 × 28 inch FRC robot on a swerve-ready chassis without "
            "the swerve modules. Add a coaxial slapdown intake, dual flywheel hooded shooter, "
            "dead-axle arm, and dual telescoping climber."
        )
        record = self.create("robot", prompt, "Worked example, FRC robot", owner_id)
        record["is_example"] = True
        record["example_note"] = (
            "A worked example to show what the Design Studio produces. "
            "Revise it, delete it, or create your own PCB and robot designs, it is yours."
        )
        (self.root / record["id"] / "design.json").write_text(json.dumps(record, indent=2))
        return record

    def edit(self, design_id: str, prompt: str, owner_id: str) -> dict[str, Any]:
        record = self.get(design_id, owner_id)
        previous = {"revision": record["revision"], "prompt": record["prompt"], "spec": record["spec"],
                    "updated_at": record["updated_at"]}
        combined = f"{record['prompt']}\nRevision request: {prompt}"
        spec = self._pcb_spec(combined) if record["kind"] == "pcb" else self._robot_spec(combined)
        spec["name"] = record["name"]
        record["history"] = (record.get("history") or []) + [previous]
        record["revision"] += 1
        record["prompt"] = combined
        record["last_edit"] = prompt
        record["spec"] = spec
        record["updated_at"] = _now()
        self._render(record)
        return record

    def record_exact_copy(self, owner_id: str, name: str, onshape_data: dict[str, Any]) -> dict[str, Any]:
        design_id = secrets.token_hex(8); target = self.root / design_id; target.mkdir()
        record = {"id": design_id, "owner_id": owner_id, "kind": "robot", "name": name,
                  "status": "onshape", "revision": 1, "prompt": "Exact editable copy of the public 2025 Serpentheim CAD Release",
                  "spec": {"exact_copy": True, "instances": 44, "mates": 48, "source_document_id": "9b4847a461c2a185e6ff320a",
                           "subsystems": ["Chassis", "Elevator", "End Effector", "Funnel", "Climb", "Hardware"]},
                  "history": [], "created_at": _now(), "updated_at": _now(), "artifacts": [],
                  "warnings": ["Copied from the public release. Confirm the original team's license/attribution requirements before redistribution or competition use."],
                  "onshape": {**onshape_data, "published_revision": 1, "updated_at": _now()}}
        (target / "design.json").write_text(json.dumps(record, indent=2)); return record

    @staticmethod
    def _warnings(kind: str) -> list[str]:
        if kind == "pcb":
            return ["Run KiCad ERC/DRC and have a qualified engineer review power, isolation, and footprints before fabrication."]
        return ["Concept CAD: verify current FRC rules, loads, fasteners, wiring, collisions, and manufacturability before building."]

    def _render(self, record: dict[str, Any]) -> None:
        target = self.root / record["id"]
        for path in target.iterdir():
            if path.name != "design.json" and path.is_file():
                path.unlink()
        if record["kind"] == "pcb":
            names = self._render_pcb(target, record)
        else:
            names = self._render_robot(target, record)
        bundle = f"{_slug(record['name'])}-r{record['revision']}.zip"
        with zipfile.ZipFile(target / bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                archive.write(target / name, name)
        record["artifacts"] = names + [bundle]
        (target / "design.json").write_text(json.dumps(record, indent=2))

    @staticmethod
    def _pcb_spec(prompt: str) -> dict[str, Any]:
        p = prompt.lower()
        width = max(20, min(300, _number(p, r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*(?:wide|width|x)", 80)))
        height = max(20, min(300, _number(p, r"(?:x|by|height)\s*(\d+(?:\.\d+)?)\s*mm", 55)))
        voltage = _number(p, r"(\d+(?:\.\d+)?)\s*v", 12)
        layers = int(_number(p, r"(\d+)\s*[- ]?layer", 4 if any(x in p for x in ("usb", "ethernet", "high speed")) else 2))
        layers = 4 if layers >= 4 else 2
        components = [
            {"ref": "J1", "value": f"INPUT_{voltage:g}V", "footprint": "TerminalBlock_1x02", "x": 12, "y": height / 2},
            {"ref": "F1", "value": "2A", "footprint": "Fuse_1206", "x": 25, "y": height / 2},
            {"ref": "U1", "value": "DC_DC_REG", "footprint": "SOIC-8", "x": width / 2, "y": height / 2},
            {"ref": "C1", "value": "10uF", "footprint": "C_0805", "x": width / 2 - 9, "y": height / 2 - 8},
            {"ref": "C2", "value": "100nF", "footprint": "C_0603", "x": width / 2 + 9, "y": height / 2 - 8},
            {"ref": "J2", "value": "OUTPUT", "footprint": "TerminalBlock_1x02", "x": width - 12, "y": height / 2},
        ]
        if "esp32" in p:
            components[2].update(value="ESP32-S3-WROOM", footprint="ESP32-S3-WROOM-1")
        elif "arduino" in p:
            components[2].update(value="ATMEGA328P-AU", footprint="TQFP-32")
        elif "led" in p:
            components += [{"ref": "D1", "value": "STATUS_LED", "footprint": "LED_0603", "x": width-24, "y": height/2-10},
                           {"ref": "R1", "value": "1k", "footprint": "R_0603", "x": width-34, "y": height/2-10}]
        return {"width_mm": width, "height_mm": height, "layers": layers, "input_voltage_v": voltage,
                "design_rules": {"signal_trace_mm": 0.25, "power_trace_mm": 1.0, "clearance_mm": 0.2,
                                 "via_mm": 0.8, "via_drill_mm": 0.4},
                "components": components, "nets": ["GND", "VIN", "VOUT", "SIGNAL"],
                "intent": prompt[-1000:]}

    @staticmethod
    def _robot_spec(prompt: str) -> dict[str, Any]:
        """Synthesize a robot spec: explicit prompt facts, then the self-hosted model, then
        prompt-seeded defaults. See services/robot_spec.py for the layering rules."""
        return build_robot_spec(prompt)

    def _render_pcb(self, target: Path, record: dict[str, Any]) -> list[str]:
        spec = record["spec"]
        stem = _slug(record["name"])
        pcb_name, bom_name, preview_name, readme_name = f"{stem}.kicad_pcb", "bom.csv", "preview.svg", "README.md"
        nets = {name: i + 1 for i, name in enumerate(spec["nets"])}
        lines = ["(kicad_pcb (version 20240108) (generator kale_ai)", '  (general (thickness 1.6))',
                 '  (paper "A4")', '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (36 "B.SilkS" user "b.silkscreen") (37 "F.SilkS" user "f.silkscreen") (44 "Edge.Cuts" user))',
                 '  (setup (pad_to_mask_clearance 0))']
        for name, num in nets.items(): lines.append(f'  (net {num} "{name}")')
        w, h = spec["width_mm"], spec["height_mm"]
        for x1, y1, x2, y2 in ((0,0,w,0),(w,0,w,h),(w,h,0,h),(0,h,0,0)):
            lines.append(f'  (gr_line (start {x1} {y1}) (end {x2} {y2}) (stroke (width 0.1) (type default)) (layer "Edge.Cuts"))')
        for idx, comp in enumerate(spec["components"]):
            net_a = nets["GND"]; net_b = nets["VIN"] if idx < 3 else nets["VOUT"]
            x, y = round(comp["x"], 2), round(comp["y"], 2)
            lines += [f'  (footprint "Kale:{comp["footprint"]}" (layer "F.Cu") (at {x} {y})',
                      f'    (property "Reference" "{comp["ref"]}" (at 0 -3 0) (layer "F.SilkS"))',
                      f'    (property "Value" "{comp["value"]}" (at 0 3 0) (layer "F.Fab"))',
                      '    (fp_rect (start -3 -2) (end 3 2) (stroke (width 0.2) (type default)) (fill none) (layer "F.SilkS"))',
                      f'    (pad "1" thru_hole circle (at -1.27 0) (size 1.8 1.8) (drill 0.9) (layers "*.Cu" "*.Mask") (net {net_a} "GND"))',
                      f'    (pad "2" thru_hole circle (at 1.27 0) (size 1.8 1.8) (drill 0.9) (layers "*.Cu" "*.Mask") (net {net_b} "{spec["nets"][net_b-1]}")))']
        # A visible ground and power backbone makes the board useful as a starting layout.
        lines += [f'  (segment (start 10 {h-8}) (end {w-10} {h-8}) (width 1) (layer "F.Cu") (net {nets["GND"]}))',
                  f'  (segment (start 10 8) (end {w-10} 8) (width 1) (layer "F.Cu") (net {nets["VIN"]}))', ')']
        (target / pcb_name).write_text("\n".join(lines))
        with (target / bom_name).open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["Reference", "Value", "Footprint", "X_mm", "Y_mm"]); writer.writeheader()
            for c in spec["components"]: writer.writerow({"Reference":c["ref"],"Value":c["value"],"Footprint":c["footprint"],"X_mm":round(c["x"],2),"Y_mm":round(c["y"],2)})
        parts = "".join(f'<g transform="translate({c["x"]*8:.1f},{c["y"]*8:.1f})"><rect x="-20" y="-13" width="40" height="26" rx="4" fill="#173c31" stroke="#75e0af"/><text y="4" text-anchor="middle" fill="white" font-size="11">{c["ref"]}</text></g>' for c in spec["components"])
        (target / preview_name).write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-20 -20 {w*8+40} {h*8+40}"><rect width="{w*8}" height="{h*8}" rx="12" fill="#0d211a" stroke="#65dca8" stroke-width="4"/>{parts}</svg>')
        (target / readme_name).write_text(f"# {record['name']}\n\nGenerated by Kale Forge, revision {record['revision']}. Open `{pcb_name}` in KiCad 8+. Run ERC/DRC and verify every footprint and electrical rating before fabrication.\n")
        return [pcb_name, bom_name, preview_name, readme_name]

    # ── Replicated COTS geometry ────────────────────────────────────────────
    # These build the actual published envelopes of parts every FRC robot carries, so the
    # assembly reserves real volume instead of leaving the electronics as an afterthought.

    @staticmethod
    def _build_electrical(mesh: "_RobotMesh", spec: dict[str, Any]) -> None:
        """Replicate the control system: PDH/PDP, 120 A main breaker, battery, roboRIO, radio."""
        for placement in spec.get("electrical", {}).get("placements", []):
            key = placement["key"]
            dx, dy, dz = placement["size_mm"]
            cx, cy, cz = placement["center_mm"]
            role = f"{placement['name']} ({placement['sku']})" if placement["sku"] else placement["name"]
            mesh.box(f"elec_{key}_body", dx, dy, dz, cx, cy, cz, "electrical", role)

            if key in {"pdh", "pdp"}:
                # Two terminal rows down the long faces, the wiring keep-out that decides
                # whether a bellypan layout is actually serviceable.
                for side, sign in (("a", -1), ("b", 1)):
                    mesh.box(f"elec_{key}_terminals_{side}", dx * 0.92, dy * 0.16, dz * 0.5,
                             cx, cy + sign * dy * 0.42, cz + dz * 0.2,
                             "electrical", f"{placement['name']} terminal row + wire bend radius")
            elif key == "main_breaker":
                # Two 1/4-20 studs: the whole robot's current passes through these.
                for side, sign in (("pos", -1), ("neg", 1)):
                    mesh.cylinder_z(f"elec_main_breaker_stud_{side}", 14, 6.35,
                                    cx + sign * dx * 0.28, cy, cz + dz / 2 + 7,
                                    "electrical", "120 A main breaker stud (6 AWG ring terminal)")
                mesh.cylinder_z("elec_main_breaker_button", 8, 12, cx, cy, cz + dz / 2 + 4,
                                "electrical", "manual reset button, must stay reachable")
            elif key == "battery":
                for side, sign in (("pos", -1), ("neg", 1)):
                    mesh.cylinder_z(f"elec_battery_post_{side}", 18, 14,
                                    cx + sign * dx * 0.33, cy, cz + dz / 2 + 9,
                                    "electrical", "battery terminal post (6 AWG lead)")
                mesh.box("elec_battery_retention", dx + 18, dy + 18, 8, cx, cy, cz + dz / 2 + 20,
                         "electrical", "battery retention strap / bracket")
            elif key == "rio":
                mesh.box("elec_rio_connector_edge", dx, dy * 0.22, dz * 0.7,
                         cx, cy - dy * 0.5, cz, "electrical",
                         "roboRIO connector edge, keep clear for service")
            elif key == "radio":
                mesh.box("elec_radio_mast", 25.4, 25.4, cz - 70, cx, cy, (cz + 70) / 2,
                         "electrical", "radio standoff mast")
            elif key == "rsl":
                mesh.cylinder_z("elec_rsl_lens", dz * 0.6, dx * 0.8, cx, cy, cz + dz * 0.2,
                                "electrical", "RSL lens, all-round visibility required")

    @staticmethod
    def _build_swerve_module(mesh: "_RobotMesh", index: int, x: float, y: float,
                             module: dict[str, Any], drive_motor: dict[str, Any],
                             steer_motor: dict[str, Any], rail_bottom_z: float) -> None:
        """Replicate a swerve module envelope: plate, azimuth, wheel and both motors."""
        plate_w, plate_l = (value * frc_parts.IN for value in module["plate_in"])
        drop = module["drop_in"] * frc_parts.IN
        wheel_d = module["wheel_in"] * frc_parts.IN
        tag = f"swerve_{index}"
        vendor = module["name"]

        mesh.box(f"{tag}_mount_plate", plate_w, plate_l, 6.35, x, y, rail_bottom_z + 3.2,
                 "drivetrain", f"{vendor} mounting plate")
        # Azimuth bearing and the rotating fork below it.
        mesh.cylinder_z(f"{tag}_azimuth_bearing", 12, plate_w * 0.62, x, y, rail_bottom_z - 6,
                        "drivetrain", f"{vendor} azimuth bearing")
        for side, sign in (("left", -1), ("right", 1)):
            mesh.box(f"{tag}_fork_{side}", 6.35, plate_l * 0.86, drop * 0.62,
                     x + sign * plate_w * 0.36, y, rail_bottom_z - drop * 0.34,
                     "drivetrain", f"{vendor} fork side plate")
        mesh.cylinder_x(f"{tag}_wheel", plate_w * 0.44, wheel_d, x, y,
                        rail_bottom_z - drop + wheel_d / 2, "drivetrain",
                        f"{module['wheel_in']:g} in treaded wheel")
        # MK4i-style modules invert both motors above the plate; MK4/MAXSwerve stand upright.
        motor_z = rail_bottom_z + 6.35 + drive_motor["length_in"] * frc_parts.IN / 2
        mesh.cylinder_z(f"{tag}_drive_motor", drive_motor["length_in"] * frc_parts.IN,
                        drive_motor["diameter_in"] * frc_parts.IN,
                        x - plate_w * 0.24, y + plate_l * 0.10, motor_z,
                        "drivetrain", f"{drive_motor['name']} drive motor")
        mesh.cylinder_z(f"{tag}_steer_motor", steer_motor["length_in"] * frc_parts.IN,
                        steer_motor["diameter_in"] * frc_parts.IN,
                        x + plate_w * 0.24, y - plate_l * 0.10,
                        rail_bottom_z + 6.35 + steer_motor["length_in"] * frc_parts.IN / 2,
                        "drivetrain", f"{steer_motor['name']} steer motor")
        mesh.cylinder_z(f"{tag}_encoder", 8, 22, x, y - plate_l * 0.30, rail_bottom_z + 10,
                        "drivetrain", module["encoder"])

    def _render_robot(self, target: Path, record: dict[str, Any]) -> list[str]:
        spec = record["spec"]; stem = _slug(record["name"])
        obj_name, bom_name, preview_name, fs_name, manifest_name, readme_name, dossier_name = (
            f"{stem}.obj", "robot-bom.csv", "preview.svg", "KaleRobot.fs", "robot-assembly.json",
            "README.md", "design-dossier.md")
        frame = spec["frame"]; w, l = frame["width_in"] * 25.4, frame["length_in"] * 25.4
        mesh = _RobotMesh(); rail_z = 76.2; rail_bottom_z = rail_z - 12.7
        drivetrain = spec.get("drivetrain", {})

        # 2x1 tube chassis. Rail count follows the frame size, longer frames need the
        # extra crossmember to keep the bellypan from oil-canning.
        mesh.box("chassis_left_2x1", 50.8, l, 25.4, 25.4, l / 2, rail_z, "chassis", "2x1 frame rail")
        mesh.box("chassis_right_2x1", 50.8, l, 25.4, w - 25.4, l / 2, rail_z, "chassis", "2x1 frame rail")
        mesh.box("chassis_front_2x1", w - 101.6, 50.8, 25.4, w / 2, 25.4, rail_z, "chassis", "2x1 cross rail")
        mesh.box("chassis_rear_2x1", w - 101.6, 50.8, 25.4, w / 2, l - 25.4, rail_z, "chassis", "2x1 cross rail")
        mesh.box("chassis_bellypan", w - 110, l - 110, 3.2, w / 2, l / 2, 61, "chassis", "pocketed 0.090 in bellypan")
        crossmember_positions = (l * .34, l * .66) if frame["length_in"] <= 29 else (l * .28, l * .50, l * .72)
        for index, y in enumerate(crossmember_positions, 1):
            mesh.box(f"chassis_crossmember_{index}", w - 101.6, 25.4, 25.4, w / 2, y, rail_z,
                     "chassis", "mechanism crossmember")
        for index, (x, y) in enumerate(((30, 30), (w - 30, 30), (30, l - 30), (w - 30, l - 30)), 1):
            mesh.box(f"chassis_corner_gusset_{index}", 60, 60, 3.2, x, y, rail_z + 14.5,
                     "chassis", "corner gusset (loaded in shear)")

        # Drivetrain, a real module envelope, not a placeholder block.
        module_key = drivetrain.get("module_key", "mk4i")
        module = frc_parts.SWERVE_MODULES.get(module_key, frc_parts.SWERVE_MODULES["mk4i"])
        drive_motor = frc_parts.MOTORS.get(drivetrain.get("motor_key", "kraken_x60"), frc_parts.MOTORS["kraken_x60"])
        steer_motor = frc_parts.MOTORS.get(drivetrain.get("steer_motor_key", "kraken_x60"), drive_motor)
        corner_inset = max(70.0, module["plate_in"][0] * frc_parts.IN * 0.62 + 25.4)
        corners = ((corner_inset, corner_inset), (w - corner_inset, corner_inset),
                   (corner_inset, l - corner_inset), (w - corner_inset, l - corner_inset))
        if spec["drive"]["modules_included"] and drivetrain.get("type") != "west-coast":
            for index, (x, y) in enumerate(corners, 1):
                self._build_swerve_module(mesh, index, x, y, module, drive_motor, steer_motor, rail_bottom_z)
        elif drivetrain.get("type") == "west-coast":
            wheel_d = spec["drive"]["wheel_diameter_in"] * 25.4
            for side, x in (("left", 40), ("right", w - 40)):
                mesh.box(f"wcd_{side}_rail", 25.4, l - 80, 50.8, x, l / 2, rail_z - 25,
                         "drivetrain", "west-coast drop-centre rail")
                for index, y in enumerate((l * .18, l * .5, l * .82), 1):
                    drop = 6.0 if index == 2 else 0.0  # centre wheel dropped for turning
                    mesh.cylinder_x(f"wcd_{side}_wheel_{index}", 38.1, wheel_d, x, y,
                                    rail_bottom_z - wheel_d / 2 + drop, "drivetrain",
                                    f"{spec['drive']['wheel_diameter_in']:g} in traction wheel")
                mesh.box(f"wcd_{side}_gearbox", 76.2, 120, 90, x, l / 2, rail_z + 60,
                         "drivetrain", "single-speed drive gearbox")
        else:
            for index, (x, y) in enumerate(corners, 1):
                plate_w, plate_l = (value * frc_parts.IN for value in module["plate_in"])
                mesh.box(f"swerve_ready_corner_{index}", plate_w + 6, plate_l + 6, 6.35, x, y,
                         rail_bottom_z + 3.2, "drivetrain",
                         f"bare {module['name']} clearance plate (modules not installed)")

        self._build_electrical(mesh, spec)

        if spec["intake"]["included"]:
            bias = spec["intake"].get("position_bias", 0.10)
            pivot_y, pivot_z = l * (0.3 + bias * 2), 145
            tip_y, tip_z = -105 + bias * 120, 70
            roller_d = spec["intake"].get("roller_diameter_in", 2.0) * 25.4
            for side, x in (("left", 72), ("right", w - 72)):
                mesh.beam_yz(f"intake_{side}_arm", 12.7, 31.75, x, pivot_y, pivot_z, tip_y, tip_z,
                             "intake", "pivoting arm plate")
                if spec["intake"]["type"].startswith("four-bar"):
                    mesh.beam_yz(f"intake_{side}_fourbar_link", 9.5, 19.05, x, pivot_y + 48, pivot_z + 42,
                                 tip_y + 35, tip_z + 58, "intake", "four-bar deployment link")
                mesh.cylinder_x(f"intake_{side}_pivot", 28, 38, x, pivot_y, pivot_z, "intake", "dead axle pivot")
                mesh.box(f"intake_{side}_hardstop", 20, 20, 20, x, pivot_y - 40, pivot_z - 30,
                         "intake", "machined hard stop (not a software limit)")
            roller_width = min(spec["intake"]["width_in"] * 25.4, w - 120)
            mesh.cylinder_x("intake_front_roller", roller_width, roller_d, w / 2, tip_y, tip_z,
                            "intake", f"{spec['intake']['roller_diameter_in']:g} in compliant roller")
            mesh.cylinder_x("intake_rear_roller", roller_width, roller_d * 0.75, w / 2, tip_y + 75, tip_z + 25,
                            "intake", "centering roller")
            mesh.cylinder_x("intake_pivot_shaft", w - 110, 19.05, w / 2, pivot_y, pivot_z,
                            "intake", "coaxial power shaft")
            intake_motor = frc_parts.MOTORS.get(spec["intake"].get("motor_key", "neo"), frc_parts.MOTORS["neo"])
            mesh.cylinder_x("intake_gearbox_motor", intake_motor["length_in"] * frc_parts.IN,
                            intake_motor["diameter_in"] * frc_parts.IN, 40, pivot_y, pivot_z + 20,
                            "intake", f"{intake_motor['name']} on the static gearbox")

        if spec["shooter"]["included"]:
            bias = spec["shooter"].get("position_bias", 0.66)
            flywheel_d = spec["shooter"].get("flywheel_diameter_in", 4.0) * 25.4
            shooter_y = l * bias
            shooter_z = min(425, spec["climber"]["stowed_height_in"] * 25.4 * .62)
            mesh.box("shooter_left_sideplate", 9.5, 250, 230, 105, shooter_y, shooter_z, "shooter", "pocketed side plate")
            mesh.box("shooter_right_sideplate", 9.5, 250, 230, w - 105, shooter_y, shooter_z, "shooter", "pocketed side plate")
            flywheel_width = w - 190
            single = spec["shooter"]["type"].startswith("single")
            offsets = ((0, "single"),) if single else ((-62, "lower"), (62, "upper"))
            for offset, label in offsets:
                mesh.cylinder_x(f"shooter_{label}_flywheel", flywheel_width, flywheel_d, w / 2,
                                shooter_y + offset, shooter_z + 42, "shooter",
                                f"{spec['shooter']['flywheel_diameter_in']:g} in flywheel")
                mesh.cylinder_x(f"shooter_{label}_shaft", w - 170, 12.7, w / 2,
                                shooter_y + offset, shooter_z + 42, "shooter", "1/2 in hex shaft")
            for index, angle in enumerate((-45, -20, 5, 30), 1):
                radians = math.radians(angle)
                mesh.cylinder_x(f"shooter_hood_roller_{index}", flywheel_width - 30, 31.75, w / 2,
                                shooter_y + math.cos(radians) * 145, shooter_z + 42 + math.sin(radians) * 145,
                                "shooter", "hood/back roller")
            shooter_motor = frc_parts.MOTORS.get(spec["shooter"].get("motor_key", "kraken_x60"), frc_parts.MOTORS["kraken_x60"])
            for side, x in (("left", 72), ("right", w - 72)):
                mesh.cylinder_x(f"shooter_{side}_motor", shooter_motor["length_in"] * frc_parts.IN,
                                shooter_motor["diameter_in"] * frc_parts.IN, x,
                                shooter_y + (-62 if side == "left" else 62), shooter_z + 42,
                                "shooter", f"{shooter_motor['name']} flywheel motor")
            if spec["shooter"]["type"].startswith("turreted"):
                mesh.cylinder_z("shooter_turret_ring", 20, w * 0.52, w / 2, shooter_y, shooter_z - 130,
                                "shooter", "turret slew ring")
            for index, y in enumerate((shooter_y - 150, shooter_y - 105), 1):
                mesh.cylinder_x(f"feeder_roller_{index}", flywheel_width - 50, 50.8, w / 2, y, shooter_z - 75,
                                "shooter", "indexed feeder roller")

        if spec["elevator"]["included"]:
            max_h = spec["elevator"]["max_height_in"] * 25.4
            base_y = l * spec["elevator"].get("position_bias", 0.58)
            stages = max(1, int(spec["elevator"].get("stages", 2)))
            for side, x in (("left", w * .38), ("right", w * .62)):
                mesh.box(f"elevator_{side}_static", 50.8, 25.4, max_h * .62, x, base_y, max_h * .31 + 90,
                         "elevator", "static 2x1 upright")
                for stage in range(1, stages):
                    fraction = stage / stages
                    mesh.box(f"elevator_{side}_stage{stage}", 38.1 - stage * 3, 25.4, max_h * (.58 - fraction * .10),
                             x, base_y - 7 * stage, max_h * (.48 + fraction * .14) + 90,
                             "elevator", f"moving stage {stage} tube")
                mesh.box(f"elevator_{side}_carriage", 25.4, 25.4, max_h * .28, x, base_y - 7 * stages,
                         max_h * .70 + 90, "elevator", f"{spec['elevator']['architecture']} carriage")
            mesh.box("elevator_bottom_crossbar", w * .30, 38.1, 38.1, w / 2, base_y, 125, "elevator", "lower brace")
            mesh.box("elevator_carriage_crossbar", w * .30, 38.1, 38.1, w / 2, base_y - 7 * stages, max_h * .82,
                     "elevator", "carriage brace")
            mesh.cylinder_x("elevator_winch", w * .22, 50.8, w / 2, base_y + 35, 145, "elevator", "winch drum")

        if spec["manipulator"]["included"]:
            pivot_y = l * spec["manipulator"].get("position_bias", 0.54)
            pivot_z = 210
            arm_length = min(spec["manipulator"]["reach_in"] * 25.4, 650)
            angle = math.radians(52)
            end_y, end_z = pivot_y - math.cos(angle) * arm_length, pivot_z + math.sin(angle) * arm_length
            for side, x in (("left", w * .34), ("right", w * .66)):
                mesh.beam_yz(f"arm_{side}_beam", 25.4, 50.8, x, pivot_y, pivot_z, end_y, end_z,
                             "arm", "2x1 shoulder arm")
                mesh.cylinder_x(f"arm_{side}_pivot", 38, 76.2, x, pivot_y, pivot_z, "arm", "dead axle bearing block")
            if spec["manipulator"]["type"].startswith("double-jointed"):
                elbow_y, elbow_z = end_y, end_z
                second = arm_length * 0.62
                for side, x in (("left", w * .34), ("right", w * .66)):
                    mesh.beam_yz(f"arm_{side}_forearm", 19.05, 38.1, x, elbow_y, elbow_z,
                                 elbow_y - second * 0.8, elbow_z + second * 0.3, "arm", "forearm segment")
                end_y, end_z = elbow_y - second * 0.8, elbow_z + second * 0.3
            mesh.cylinder_x("arm_cross_shaft", w * .38, 31.75, w / 2, pivot_y, pivot_z,
                            "arm", f"{spec['manipulator'].get('shaft', '1/2 in hex')} shoulder shaft")
            mesh.cylinder_x("arm_end_effector_roller", w * .44, 50.8, w / 2, end_y, end_z, "arm", "roller end effector")
            mesh.box("arm_wrist_plate", w * .48, 12.7, 100, w / 2, end_y + 20, end_z - 15, "arm", "wrist structure")

        if spec["climber"]["included"]:
            top = spec["climber"]["stowed_height_in"] * 25.4
            single_hook = spec["climber"]["type"].startswith("single")
            sides = (("center", w * .5),) if single_hook else (("left", w * .27), ("right", w * .73))
            for side, x in sides:
                mesh.box(f"climber_{side}_outer", 50.8, 50.8, top - 125, x, l - 95, (top + 125) / 2,
                         "climber", "outer 2x2 telescoping tube")
                mesh.box(f"climber_{side}_inner", 38.1, 38.1, top - 180, x, l - 95, (top + 180) / 2,
                         "climber", "inner telescoping tube")
                mesh.beam_yz(f"climber_{side}_hook", 12.7, 25.4, x, l - 95, top - 18, l - 45, top + 18,
                             "climber", "rung hook")
            mesh.cylinder_x("climber_winch_drum", w * .52, 50.8, w / 2, l - 120, 150, "climber",
                            "winch drum with mechanical ratchet")

        if spec["pneumatics"]["included"]:
            mesh.cylinder_x("pneumatic_tank_1", 300, 60, w / 2, l * .40, 175, "pneumatics", "accumulator tank")
            mesh.cylinder_x("pneumatic_tank_2", 300, 60, w / 2, l * .40, 245, "pneumatics", "accumulator tank")

        (target / obj_name).write_text(mesh.obj(record["name"], record["revision"]))

        # ── BOM: real vendors and part numbers where the design names a real part ──
        with (target / bom_name).open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["Subsystem", "Item", "Qty", "Vendor", "Part number", "Specification / note"])
            counts: dict[tuple[str, str], int] = {}
            for component in mesh.components:
                if component["subsystem"] == "electrical":
                    continue
                key = (component["subsystem"], component["role"])
                counts[key] = counts.get(key, 0) + 1
            for (subsystem, role), qty in sorted(counts.items()):
                writer.writerow([subsystem.title(), role, qty, "", "",
                                 "Concept specification; validate vendor part and loads"])
            if drivetrain.get("modules_included"):
                writer.writerow(["Drivetrain", module["name"], drivetrain.get("module_count", 4) or 4,
                                 module["vendor"], module.get("sku", ""),
                                 f"{drivetrain.get('drive_ratio_label', '')} drive {drivetrain.get('drive_ratio', '')}:1, "
                                 f"steer {drivetrain.get('steer_ratio', '')}:1, {module['verify']}"])
            assignments = spec.get("electrical", {}).get("assignments", [])
            for placement in spec.get("electrical", {}).get("placements", []):
                writer.writerow(["Electrical", placement["name"], 1, placement["vendor"],
                                 placement["sku"], f"{placement['note']} {placement['verify']}"])
            for row in assignments:
                writer.writerow([row["subsystem"].title(), f"{row['load']} branch", 1, "", "",
                                 f"PDH/PDP channel {row['channel']} · {row['breaker_a']} A breaker · "
                                 f"{row['wire_awg']} AWG"])
            writer.writerow(["Electrical", "Battery lead set", 1, "", "",
                             f"{spec['electrical']['battery_lead_awg']} AWG to the "
                             f"{spec['electrical']['main_breaker_a']} A main breaker and SB50"])

        subsystem_labels = " · ".join(name.title() for name in spec["subsystems"]) or "Bare chassis"
        module_note = (f"{module['name']} {drivetrain.get('drive_ratio_label', '')}"
                       if drivetrain.get("modules_included") else "swerve-ready, modules omitted")
        speed_note = (f" · {drivetrain.get('free_speed_fps')} ft/s free"
                      if drivetrain.get("modules_included") else "")
        (target / preview_name).write_text(f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 500">
<defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="#06140f"/><stop offset="1" stop-color="#102b23"/></linearGradient></defs>
<rect width="760" height="500" rx="28" fill="url(#bg)"/><g transform="translate(75 38)">
<path d="M55 335 L405 335 L600 250 L250 250 Z M55 335 L55 395 L405 395 L405 335 M405 395 L600 310 L600 250" fill="#153d31" stroke="#65e0ac" stroke-width="6"/>
<path d="M90 326 L270 248 M370 326 L550 248" stroke="#9ff3cf" stroke-width="8" opacity=".8"/>
<g fill="#17221f" stroke="#a9c9bb" stroke-width="5"><circle cx="95" cy="383" r="34"/><circle cx="400" cy="383" r="34"/><circle cx="275" cy="296" r="27"/><circle cx="555" cy="275" r="27"/></g>
<g opacity=".95"><rect x="150" y="292" width="120" height="26" rx="4" fill="#0e2a22" stroke="#8ce8bd" stroke-width="3"/><rect x="286" y="286" width="52" height="20" rx="3" fill="#0e2a22" stroke="#8ce8bd" stroke-width="3"/><rect x="96" y="300" width="44" height="30" rx="3" fill="#0e2a22" stroke="#f0b429" stroke-width="3"/></g>
{f'<path d="M130 305 L60 195 M520 270 L450 125" stroke="#65e0ac" stroke-width="16"/><rect x="28" y="165" width="250" height="30" rx="15" fill="#77e7b7"/>' if spec['intake']['included'] else ''}
{f'<rect x="250" y="125" width="245" height="130" rx="18" fill="#183b31" stroke="#d7fff0" stroke-width="7"/><circle cx="315" cy="158" r="35" fill="#0b1713" stroke="#72e2b2" stroke-width="8"/><circle cx="430" cy="158" r="35" fill="#0b1713" stroke="#72e2b2" stroke-width="8"/>' if spec['shooter']['included'] else ''}
{f'<path d="M255 285 L355 62 M455 270 L355 62" stroke="#d7fff0" stroke-width="18"/><circle cx="355" cy="62" r="24" fill="#65e0ac"/>' if spec['manipulator']['included'] else ''}
{f'<path d="M220 315 L220 75 M465 275 L465 75" stroke="#c8eee0" stroke-width="15"/><path d="M206 80 l14 -28 l14 28 M451 80 l14 -28 l14 28" fill="none" stroke="#65e0ac" stroke-width="8"/>' if spec['climber']['included'] else ''}
</g><text x="40" y="55" fill="#72e2b2" font-family="sans-serif" font-weight="700" font-size="14">{spec['profile']['label'].upper()}</text>
<text x="40" y="455" fill="white" font-family="sans-serif" font-weight="700" font-size="24">{record['name'][:42]}</text>
<text x="40" y="482" fill="#a9c9bb" font-family="sans-serif" font-size="14">{frame['width_in']} × {frame['length_in']} in · {module_note}{speed_note} · {subsystem_labels}</text></svg>''')
        (target / fs_name).write_text(self._featurescript(record))

        mates = []
        for component in mesh.components:
            role, subsystem = component["role"], component["subsystem"]
            if subsystem in {"intake", "arm"} and "pivot" in role:
                mate_type = "revolute"
            elif subsystem in {"elevator", "climber"} and "tube" in role:
                mate_type = "slider"
            elif subsystem == "drivetrain" and "azimuth" in role:
                mate_type = "revolute"
            elif "wheel" in role or "flywheel" in role or "roller" in role or "drum" in role:
                mate_type = "revolute"
            else:
                mate_type = "fastened"
            mates.append({"type": mate_type, "component": component["name"], "parent": subsystem})
        manifest = {"schema_version": "3.0", "units": "millimeter",
                    "coordinate_system": "x width, y length, z height",
                    "profile": spec["profile"], "components": mesh.components, "mates": mates,
                    "parts_catalog": frc_parts.catalog_digest(), "spec": spec}
        (target / manifest_name).write_text(json.dumps(manifest, indent=2))

        (target / dossier_name).write_text(self._dossier(record, mesh))
        (target / readme_name).write_text(
            f"# {record['name']}\n\nFull-scale exemplar-grounded FRC concept, revision {record['revision']}.\n\n"
            f"- `{obj_name}`, spatial assembly mesh, including replicated COTS envelopes "
            f"(distribution hub, 120 A main breaker, battery, roboRIO, radio, swerve modules)\n"
            f"- `{fs_name}`, parametric Onshape starting feature\n"
            f"- `{manifest_name}`, components, mate intent and the full spec\n"
            f"- `{bom_name}`, BOM with vendors, part numbers, breaker channels and wire gauges\n"
            f"- `{dossier_name}`, reference basis, techniques applied, electrical plan\n\n"
            "Every dimension here is a nominal envelope for packaging and first-order sizing. "
            "Validate season rules, loads, fasteners, motion, collisions, wiring and fabrication "
            "drawings, and confirm each COTS part against its vendor drawing, before construction.\n")
        return [obj_name, bom_name, preview_name, fs_name, manifest_name, dossier_name, readme_name]

    @staticmethod
    def _dossier(record: dict[str, Any], mesh: "_RobotMesh") -> str:
        """The engineering explanation: what was chosen, why, and what still has to be checked."""
        spec = record["spec"]
        drivetrain = spec.get("drivetrain", {})
        electrical = spec.get("electrical", {})
        mass = spec.get("mass_estimate", {})
        model = spec.get("model", {})

        lines = [f"# Design dossier, {record['name']}", "",
                 f"Profile: **{spec['profile']['label']}**  ",
                 f"Knowledge set: `{spec['profile']['knowledge_version']}`  ",
                 f"Parts catalog: `{spec['profile'].get('parts_version', '')}`  "]
        if model.get("used"):
            lines.append(f"Architecture selected by the self-hosted model "
                         f"(`{model.get('model_version') or model.get('provider')}`).")
        else:
            lines.append(f"Architecture selected deterministically, {model.get('reason', 'model not used')}.")
        lines += ["", "## Subsystems", ""]
        lines += [f"- **{name.title()}**, {spec.get('manipulator' if name == 'arm' else name, {}).get('type', '')}"
                  for name in spec["subsystems"]] or ["- Bare chassis"]

        if drivetrain:
            lines += ["", "## Drivetrain", "",
                      f"- Module: **{drivetrain.get('module')}** ({drivetrain.get('vendor')}), "
                      f"{drivetrain.get('selection_reason', '')}",
                      f"- Drive motor: {drivetrain.get('motor')} at "
                      f"{drivetrain.get('drive_ratio_label')} = {drivetrain.get('drive_ratio')}:1",
                      f"- Steer motor: {drivetrain.get('steer_motor')} at {drivetrain.get('steer_ratio')}:1",
                      f"- Wheel: {drivetrain.get('wheel_diameter_in')} in → "
                      f"**{drivetrain.get('free_speed_fps')} ft/s** theoretical free speed",
                      f"- {drivetrain.get('caveat', '')}"]
            if drivetrain.get("note"):
                lines.append(f"- {drivetrain['note']}")

        if electrical:
            lines += ["", "## Electrical plan", "",
                      f"- Distribution: **{electrical.get('distributor')}**, "
                      f"{electrical.get('channels_used')} channels used, "
                      f"{electrical.get('high_current_free')} high-current and "
                      f"{electrical.get('low_current_free')} low-current channels free",
                      f"- Main breaker: {electrical.get('main_breaker_a')} A, "
                      f"battery leads {electrical.get('battery_lead_awg')} AWG", ""]
            lines += ["| Channel | Load | Breaker | Wire |", "| --- | --- | --- | --- |"]
            lines += [f"| {row['channel']} | {row['load']} | {row['breaker_a']} A | {row['wire_awg']} AWG |"
                      for row in electrical.get("assignments", [])]
            if electrical.get("unassigned"):
                lines.append("")
                lines.append(f"> **Over channel capacity:** {', '.join(electrical['unassigned'])} "
                             "could not be assigned. Move to a second distribution module or cut load.")
            lines += [""] + [f"- {note}" for note in electrical.get("notes", [])]

        if mass:
            lines += ["", "## Mass estimate", "", "| Item | lb |", "| --- | --- |"]
            lines += [f"| {row['item']} | {row['lb']} |" for row in mass.get("rows", [])]
            lines += [f"| **Counted toward the limit** | **{mass.get('counted_lb')}** |",
                      f"| Excluded (battery + bumpers) | {mass.get('excluded_lb')} |",
                      f"| All-up | {mass.get('total_lb')} |",
                      f"| Target | {mass.get('target_lb')} |",
                      f"| Margin | {mass.get('margin_lb')} |", "",
                      f"{mass.get('caveat', '')}"]
            if (mass.get("margin_lb") or 0) < 0:
                lines.append("")
                lines.append("> **Over the target weight.** Cut mass before detailing: pocket the "
                             "bellypan and plates, drop a subsystem, or lighten the climber tower.")

        if spec.get("techniques"):
            lines += ["", "## Techniques applied", ""]
            for item in spec["techniques"]:
                lines.append(f"- **{item['name']}** ({item['area']}), {item['why']}  ")
                lines.append(f"  *How:* {item['how']}  ")
                lines.append(f"  *Pitfall:* {item['pitfall']}")

        if spec.get("design_notes"):
            lines += ["", "## Model design notes", ""] + [f"- {note}" for note in spec["design_notes"]]
        if spec.get("risks"):
            lines += ["", "## Risks", ""] + [f"- {risk}" for risk in spec["risks"]]

        lines += ["", "## Public reference basis", ""]
        lines += [f"- [{item['name']}]({item['url']}): {item['lesson']}" for item in spec["reference_basis"]]
        lines += ["", f"Assembly contains {len(mesh.components)} dimensioned components.", "",
                  "Kale synthesizes new dimensioned geometry from these public patterns; it does not "
                  "embed or redistribute their source meshes. COTS envelopes are nominal published "
                  "outside dimensions, confirm each against the vendor drawing before machining.", ""]
        return "\n".join(lines)

    @staticmethod
    def _featurescript(record: dict[str, Any]) -> str:
        spec=record["spec"]; f=spec["frame"]; e=spec["elevator"]
        extras = []
        if spec["intake"]["included"]:
            extras.append('        fCuboid(context, id + "intakeEnvelope", { "corner1" : vector(2,-8,2) * inch, "corner2" : vector(definition.width/inch-2,6,8) * inch });')
        if spec["shooter"]["included"]:
            extras.append('        fCuboid(context, id + "shooterEnvelope", { "corner1" : vector(4,definition.length/inch*.48,7) * inch, "corner2" : vector(definition.width/inch-4,definition.length/inch*.88,18) * inch });')
        if e["included"]:
            extras += ['        fCuboid(context, id + "elevatorLeft", { "corner1" : vector(definition.width/inch*.34,definition.length/inch*.52,3) * inch, "corner2" : vector(definition.width/inch*.42,definition.length/inch*.60,definition.height/inch) * inch });',
                       '        fCuboid(context, id + "elevatorRight", { "corner1" : vector(definition.width/inch*.58,definition.length/inch*.52,3) * inch, "corner2" : vector(definition.width/inch*.66,definition.length/inch*.60,definition.height/inch) * inch });']
        extra_source = "\n".join(extras)
        return f'''FeatureScript 2500;\nimport(path : "onshape/std/common.fs", version : "2500.0");\nannotation {{ "Feature Type Name" : "Kale FRC Robot Layout" }}\nexport const kaleRobot = defineFeature(function(context is Context, id is Id, definition is map)\n    precondition {{ annotation {{ "Name" : "Frame width" }} definition.width is Length; annotation {{ "Name" : "Frame length" }} definition.length is Length; annotation {{ "Name" : "Layout height" }} definition.height is Length; }}\n    {{\n        fCuboid(context, id + "leftRail2x1", {{ "corner1" : vector(0,0,2.5) * inch, "corner2" : vector(2,definition.length/inch,3.5) * inch }});\n        fCuboid(context, id + "rightRail2x1", {{ "corner1" : vector(definition.width/inch-2,0,2.5) * inch, "corner2" : vector(definition.width/inch,definition.length/inch,3.5) * inch }});\n        fCuboid(context, id + "frontRail2x1", {{ "corner1" : vector(2,0,2.5) * inch, "corner2" : vector(definition.width/inch-2,2,3.5) * inch }});\n        fCuboid(context, id + "rearRail2x1", {{ "corner1" : vector(2,definition.length/inch-2,2.5) * inch, "corner2" : vector(definition.width/inch-2,definition.length/inch,3.5) * inch }});\n{extra_source}\n    }});\n// Exemplar-grounded defaults: {f["width_in"]} x {f["length_in"]} in; profile {spec["profile"]["id"]}; layout height {e["max_height_in"]} in.\n'''


_studio: DesignStudio | None = None


def get_design_studio() -> DesignStudio:
    global _studio
    if _studio is None:
        _studio = DesignStudio()
    return _studio
