"""Dimensioned COTS parts catalog for FRC robot synthesis.

Every entry is a *nominal packaging envelope* plus the electrical/mechanical facts Kale needs
to place a part, size its wire and breaker, and compute first-order performance.  Envelopes
are good enough to reserve volume, check collisions and lay out a bellypan; they are NOT
vendor drawings.  Each record carries `verify` for exactly that reason, the renderer copies
it into the BOM so nobody machines a plate off these numbers.

No vendor CAD is embedded or redistributed.  Geometry here is procedurally rebuilt from
published outside dimensions and mounting patterns.
"""
from __future__ import annotations

import math
import re
from typing import Any

PARTS_VERSION = "frc-parts-2026.07.25"

IN = 25.4  # mm per inch

_VERIFY = "Nominal envelope, confirm against the current vendor drawing before machining."


# ─────────────────────────────────────────────────────────────────────────────
# Control system / electrical.  Envelopes are outside dimensions in inches
# (x = width, y = depth, z = height) in the part's own upright orientation.
# ─────────────────────────────────────────────────────────────────────────────
ELECTRONICS: dict[str, dict[str, Any]] = {
    "pdh": {
        "name": "REV Power Distribution Hub",
        "vendor": "REV Robotics", "sku": "REV-11-1850", "category": "power",
        "envelope_in": (8.90, 4.60, 1.50), "mass_lb": 0.99,
        "channels": {"high_current": 20, "high_current_max_a": 40,
                     "low_current": 4, "low_current_max_a": 15},
        "features": ["20 high-current channels on ATO breakers (40 A max each)",
                     "3 dedicated low-current channels (15 A mini fuse) plus 1 switchable channel",
                     "toolless latching WAGO terminals on every channel",
                     "per-channel current telemetry over CAN or USB-C", "LED voltage display"],
        "mount": "bolt-down through the bellypan on the four corner holes",
        "verify": _VERIFY,
    },
    "pdp2": {
        "name": "CTRE Power Distribution Panel 2.0",
        "vendor": "Cross The Road Electronics", "sku": "PDP 2.0", "category": "power",
        "envelope_in": (8.00, 4.60, 1.60), "mass_lb": 1.05,
        "channels": {"high_current": 24, "high_current_max_a": 40,
                     "low_current": 0, "low_current_max_a": 0},
        "features": ["24 channels, every one on an ATO breaker up to 40 A",
                     "small loads run on low-amperage ATO breakers in standard channels",
                     "CAN current telemetry"],
        "mount": "bolt-down through the bellypan on the four corner holes",
        "verify": _VERIFY,
    },
    "ato_breaker": {
        "name": "ATO snap-action auto-resetting breaker",
        "vendor": "REV / VEX (class)", "sku": "10/20/30/40 A ATO", "category": "power",
        "envelope_in": (0.75, 0.35, 1.20), "mass_lb": 0.02,
        "features": ["plugs into a PDH/PDP high-current channel; the breaker, not the motor, "
                     "defines the wire gauge for that branch",
                     "thermal snap-action: trips on sustained overload, auto-resets after cooling",
                     "carry spares, a breaker that has tripped repeatedly trips earlier each time"],
        "mount": "seated fully into the distributor channel slot",
        "verify": _VERIFY,
    },
    "pdp": {
        "name": "CTRE Power Distribution Panel",
        "vendor": "Cross The Road Electronics", "sku": "217-4244", "category": "power",
        "envelope_in": (7.40, 4.55, 1.55), "mass_lb": 1.10,
        "channels": {"high_current": 8, "high_current_max_a": 40,
                     "low_current": 8, "low_current_max_a": 30},
        "features": ["Wago lever terminals", "20 A / 10 A fused auxiliary rails", "CAN telemetry"],
        "mount": "bolt-down through the bellypan on the four corner holes",
        "verify": _VERIFY,
    },
    "main_breaker": {
        "name": "120 A main breaker",
        "vendor": "Cooper Bussmann", "sku": "18X-series (e.g. 185120F)", "category": "power",
        "envelope_in": (2.60, 1.15, 1.55), "mass_lb": 0.18,
        "rating_a": 120, "reset": "manual push-to-reset thermal breaker",
        "features": ["single point of disconnect between battery + and the PDH/PDP",
                     "must stay reachable and visible on the robot exterior"],
        "mount": "two 1/4-20 studs; bracket to a frame rail or bellypan edge",
        "wire": {"gauge_awg": 6, "terminal": "#10 ring terminal"},
        "verify": _VERIFY,
    },
    "battery": {
        "name": "12 V 18 Ah SLA robot battery",
        "vendor": "MK / Duracell (class)", "sku": "ES17-12 class", "category": "power",
        "envelope_in": (7.13, 3.03, 6.57), "mass_lb": 12.8,
        "nominal_v": 12.0, "capacity_ah": 18.0, "internal_resistance_mohm": 12.0,
        "features": ["dominant single mass, place low and near the frame centroid",
                     "must be positively retained against a 1 G impact"],
        "mount": "strapped or bracketed to the bellypan; retained on all six faces",
        "wire": {"gauge_awg": 6, "terminal": "Anderson SB50"},
        "verify": _VERIFY,
    },
    "sb50": {
        "name": "Anderson SB50 battery connector",
        "vendor": "Anderson Power Products", "sku": "SB50", "category": "power",
        "envelope_in": (2.05, 1.60, 1.20), "mass_lb": 0.15,
        "rating_a": 120, "mount": "retained so the mating force is not carried by the wire",
        "verify": _VERIFY,
    },
    "rio": {
        "name": "roboRIO 2.0",
        "vendor": "National Instruments", "sku": "roboRIO 2.0", "category": "control",
        "envelope_in": (6.90, 4.50, 1.50), "mass_lb": 0.77,
        "power": {"source": "dedicated PDH/PDP roboRIO connector", "gauge_awg": 18, "max_a": 10},
        "features": ["CAN, 2x USB, Ethernet, DIO/PWM/analog headers",
                     "leave connector clearance on the header edge"],
        "mount": "four standoffs; keep the SD card and USB edge serviceable",
        "verify": _VERIFY,
    },
    "radio": {
        "name": "VH-109 robot radio",
        "vendor": "Vivid-Hosting", "sku": "VH-109", "category": "control",
        "envelope_in": (4.35, 3.10, 1.05), "mass_lb": 0.35,
        "power": {"source": "PDH switchable radio channel or a regulated 12 V module",
                  "gauge_awg": 18, "max_a": 3},
        "features": ["mount high and away from motors, metal shadow and CAN bundles",
                     "keep the antennas clear of aluminium on both sides"],
        "mount": "high on a structural upright, antennas unobstructed",
        "verify": _VERIFY,
    },
    "rsl": {
        "name": "Robot Signal Light",
        "vendor": "Allen-Bradley (class)", "sku": "855PB-B12ME522", "category": "control",
        "envelope_in": (2.60, 2.60, 3.40), "mass_lb": 0.30,
        "power": {"source": "roboRIO RSL header", "gauge_awg": 18, "max_a": 1},
        "features": ["must be visible from all sides while the robot is enabled"],
        "mount": "highest practical point with an unobstructed sight line",
        "verify": _VERIFY,
    },
    "vrm": {
        "name": "Voltage Regulator Module",
        "vendor": "Cross The Road Electronics", "sku": "217-4245", "category": "power",
        "envelope_in": (3.00, 2.00, 0.90), "mass_lb": 0.20,
        "outputs": ["2x 12 V 2 A", "2x 5 V 2 A"],
        "power": {"source": "PDP 20 A fused rail", "gauge_awg": 18, "max_a": 10},
        "mount": "bolt-down near the radio it feeds", "verify": _VERIFY,
    },
    "canivore": {
        "name": "CANivore",
        "vendor": "Cross The Road Electronics", "sku": "CANivore", "category": "control",
        "envelope_in": (2.20, 1.70, 0.60), "mass_lb": 0.09,
        "features": ["second CAN FD bus, usually dedicated to the drivetrain",
                     "keeps drivetrain bus utilisation off the roboRIO bus"],
        "mount": "near the roboRIO, USB run kept short", "verify": _VERIFY,
    },
    "pigeon": {
        "name": "Pigeon 2.0 IMU",
        "vendor": "Cross The Road Electronics", "sku": "Pigeon 2.0", "category": "sensing",
        "envelope_in": (1.65, 1.65, 0.45), "mass_lb": 0.05,
        "features": ["mount rigidly, flat, near the rotation centre; away from motor flux"],
        "mount": "bellypan centre, flat and rigid", "verify": _VERIFY,
    },
    "spark_max": {
        "name": "SPARK MAX motor controller",
        "vendor": "REV Robotics", "sku": "REV-11-2158", "category": "controller",
        "envelope_in": (2.70, 1.80, 1.10), "mass_lb": 0.19,
        "drives": ["NEO", "NEO 550", "brushed"], "verify": _VERIFY,
        "mount": "airflow on the heat-sink face; keep the LED visible",
    },
    "spark_flex": {
        "name": "SPARK Flex motor controller",
        "vendor": "REV Robotics", "sku": "REV-11-2159", "category": "controller",
        "envelope_in": (2.95, 2.05, 1.20), "mass_lb": 0.23,
        "drives": ["NEO Vortex", "NEO", "brushed"], "verify": _VERIFY,
        "mount": "airflow on the heat-sink face; keep the LED visible",
    },
    "ph": {
        "name": "Pneumatic Hub",
        "vendor": "REV Robotics", "sku": "REV-11-1852", "category": "pneumatics",
        "envelope_in": (4.00, 3.00, 1.20), "mass_lb": 0.45,
        "features": ["16 solenoid channels", "analog pressure sensing"],
        "mount": "bolt-down near the manifold to keep tubing short", "verify": _VERIFY,
    },
    "compressor": {
        "name": "12 V pneumatic compressor",
        "vendor": "VIAIR (class)", "sku": "90C-class", "category": "pneumatics",
        "envelope_in": (7.50, 3.50, 4.50), "mass_lb": 3.10,
        "power": {"source": "PDH high-current channel", "gauge_awg": 12, "max_a": 20},
        "features": ["heavy and noisy, isolate it and budget the mass early"],
        "mount": "vibration-isolated on the bellypan", "verify": _VERIFY,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Motors.  Free speed RPM, stall torque N·m, stall current A at 12 V.
# ─────────────────────────────────────────────────────────────────────────────
MOTORS: dict[str, dict[str, Any]] = {
    "kraken_x60": {"name": "Kraken X60", "vendor": "WCP / CTRE", "free_rpm": 6000, "stall_nm": 7.09,
                   "stall_a": 366, "free_a": 2, "peak_w": 1108,
                   "foc": {"free_rpm": 5800, "stall_nm": 9.37, "stall_a": 483, "peak_w": 1405},
                   "mass_lb": 1.35, "controller": "integrated Talon FX",
                   "breaker_a": 40, "diameter_in": 2.25, "length_in": 3.70,
                   "shaft": "SplineXS with hex and keyed adapters",
                   "notes": ("trapezoidal: 6000 rpm / 7.09 N·m / 366 A stall / 1108 W peak; "
                             "FOC (licensed): 5800 rpm / 9.37 N·m / 483 A stall / 1405 W peak, "
                             "FOC trades ~200 rpm free speed for ~32% more stall torque")},
    "kraken_x44": {"name": "Kraken X44", "vendor": "WCP / CTRE", "free_rpm": 7530, "stall_nm": 4.05,
                   "stall_a": 275, "mass_lb": 0.85, "controller": "integrated Talon FX",
                   "breaker_a": 40, "diameter_in": 1.80, "length_in": 3.30,
                   "notes": "compact high-speed option for mechanisms, not usually drive"},
    "falcon_500": {"name": "Falcon 500", "vendor": "VEX / CTRE", "free_rpm": 6380, "stall_nm": 4.69,
                   "stall_a": 257, "mass_lb": 1.10, "controller": "integrated Talon FX",
                   "breaker_a": 40, "diameter_in": 2.25, "length_in": 3.60,
                   "notes": "legacy stock; no longer produced"},
    "neo_vortex": {"name": "NEO Vortex", "vendor": "REV Robotics", "free_rpm": 6784, "stall_nm": 3.60,
                   "stall_a": 211, "mass_lb": 0.95, "controller": "SPARK Flex",
                   "breaker_a": 40, "diameter_in": 2.20, "length_in": 3.60,
                   "notes": "pairs with MAXPlanetary through the direct-mount pinion"},
    "neo": {"name": "NEO v1.1", "vendor": "REV Robotics", "free_rpm": 5676, "stall_nm": 2.60,
            "stall_a": 105, "mass_lb": 0.94, "controller": "SPARK MAX",
            "breaker_a": 40, "diameter_in": 2.20, "length_in": 3.60,
            "notes": "forgiving thermal behaviour; low stall current"},
    "neo_550": {"name": "NEO 550", "vendor": "REV Robotics", "free_rpm": 11000, "stall_nm": 0.97,
                "stall_a": 100, "mass_lb": 0.31, "controller": "SPARK MAX",
                "breaker_a": 30, "diameter_in": 1.55, "length_in": 2.30,
                "notes": "burns out when stalled, never use it on a hard stop"},
    "minion": {"name": "Minion", "vendor": "REV Robotics", "free_rpm": 7200, "stall_nm": 3.10,
               "stall_a": 202, "mass_lb": 0.79, "controller": "SPARK Flex",
               "breaker_a": 40, "diameter_in": 1.90, "length_in": 3.40,
               "notes": "brushless mid-size; verify the current season's legality list"},
    "cim": {"name": "CIM", "vendor": "VEX", "free_rpm": 5330, "stall_nm": 2.41,
            "stall_a": 131, "mass_lb": 2.82, "controller": "Talon SRX / SPARK MAX",
            "breaker_a": 40, "diameter_in": 2.50, "length_in": 4.34,
            "notes": "heavy for its output; brushless is strictly better where legal"},
}

MOTOR_VERIFY = "Motor curves are the published 12 V figures; derate for real bus sag and duty cycle."


# ─────────────────────────────────────────────────────────────────────────────
# Swerve modules.  `plate_in` is the frame-facing footprint; `drop_in` is how far
# the module hangs below the mounting face.
# ─────────────────────────────────────────────────────────────────────────────
SWERVE_MODULES: dict[str, dict[str, Any]] = {
    "mk5i": {
        "name": "SDS MK5i", "vendor": "Swerve Drive Specialties", "sku": "MK5i",
        "wheel_in": 4.0, "plate_in": (4.10, 4.10), "drop_in": 4.60, "mass_lb": 4.10,
        "drive_ratios": {"R1": 7.03, "R2": 6.03, "R3": 5.27},
        "steer_ratio": 26.0, "motors": ("drive", "steer"), "motor_orientation": "both inverted above the plate",
        "encoder": "CANcoder, SRX Mag, Thrifty AME or Helium Canandmag on the azimuth",
        "bearing": "sealed main steering bearing",
        "notes": ("fully gear-driven steering (no azimuth belt), wider wheel for tread life, "
                  "quick-change drive pinion without opening the module, gears enclosed in a "
                  "glass-reinforced nylon housing, no 3D-printed parts; steer takes a 44 mm or "
                  "60 mm class motor (Kraken X44/X60, NEO, Vortex)"),
        "verify": _VERIFY,
    },
    "mk5n": {
        "name": "SDS MK5n", "vendor": "Swerve Drive Specialties", "sku": "MK5n",
        "wheel_in": 4.0, "plate_in": (3.85, 3.85), "drop_in": 4.40, "mass_lb": 3.90,
        "drive_ratios": {"R1": 7.03, "R2": 6.03, "R3": 5.27},
        "steer_ratio": 26.09, "motors": ("drive", "steer"), "motor_orientation": "both inverted above the plate",
        "encoder": "CANcoder, SRX Mag, Thrifty AME or Helium Canandmag on the azimuth",
        "bearing": "sealed main steering bearing",
        "notes": ("narrow-body MK5 variant, same gear-driven azimuth and quick-change pinion "
                  "as the MK5i in a tighter corner package"),
        "verify": _VERIFY,
    },
    "mk4i": {
        "name": "SDS MK4i", "vendor": "Swerve Drive Specialties", "sku": "MK4i",
        "wheel_in": 4.0, "plate_in": (4.10, 4.10), "drop_in": 4.60, "mass_lb": 2.70,
        "drive_ratios": {"L1": 8.14, "L2": 6.75, "L3": 6.12},
        "steer_ratio": 150 / 7, "motors": ("drive", "steer"), "motor_orientation": "both inverted above the plate",
        "encoder": "CANcoder on the azimuth", "bearing": "large-bore azimuth bearing",
        "notes": "inverted motors keep the top face clear; the classic default module",
        "verify": _VERIFY,
    },
    "mk4n": {
        "name": "SDS MK4n", "vendor": "Swerve Drive Specialties", "sku": "MK4n",
        "wheel_in": 4.0, "plate_in": (3.85, 3.85), "drop_in": 4.40, "mass_lb": 2.40,
        "drive_ratios": {"L1+": 7.13, "L2+": 5.90, "L3+": 5.36},
        "steer_ratio": 18.75, "motors": ("drive", "steer"), "motor_orientation": "both inverted above the plate",
        "encoder": "CANcoder on the azimuth", "bearing": "large-bore azimuth bearing",
        "notes": "narrower module and faster azimuth than MK4i; tighter corner packaging",
        "verify": _VERIFY,
    },
    "mk4": {
        "name": "SDS MK4", "vendor": "Swerve Drive Specialties", "sku": "MK4",
        "wheel_in": 4.0, "plate_in": (4.10, 4.10), "drop_in": 4.50, "mass_lb": 2.60,
        "drive_ratios": {"L1": 8.14, "L2": 6.75, "L3": 6.12, "L4": 5.14},
        "steer_ratio": 12.8, "motors": ("drive", "steer"), "motor_orientation": "upright above the plate",
        "encoder": "CANcoder on the azimuth", "bearing": "large-bore azimuth bearing",
        "notes": "upright motors, taller stack but a simpler belt path than MK4i",
        "verify": _VERIFY,
    },
    "maxswerve": {
        "name": "REV MAXSwerve", "vendor": "REV Robotics", "sku": "REV-21-3005",
        "wheel_in": 3.0, "plate_in": (3.60, 3.60), "drop_in": 3.90, "mass_lb": 2.10,
        "drive_ratios": {"12T": 5.50, "13T": 5.08, "14T": 4.71, "15T": 4.41, "16T": 4.14},
        "steer_ratio": 9424 / 203, "motors": ("drive", "steer"), "motor_orientation": "drive upright, NEO 550 steer",
        "encoder": "through-bore absolute encoder", "bearing": "MAXSwerve azimuth bearing",
        "notes": "3 inch wheel and small footprint; ratio set by the pinion, not a kit level",
        "verify": _VERIFY,
    },
    "wcp_x": {
        "name": "WCP X / Xs", "vendor": "West Coast Products", "sku": "X-series",
        "wheel_in": 4.0, "plate_in": (4.00, 4.00), "drop_in": 4.50, "mass_lb": 2.55,
        "drive_ratios": {"X1": 7.85, "X2": 6.55, "X3": 5.90},
        "steer_ratio": 13.4, "motors": ("drive", "steer"), "motor_orientation": "both above the plate",
        "encoder": "CANcoder or through-bore", "bearing": "large-bore azimuth bearing",
        "notes": "belt-driven azimuth; verify the exact ratio set for the kit you buy",
        "verify": _VERIFY,
    },
    "thrifty": {
        "name": "Thrifty Swerve", "vendor": "The Thrifty Bot", "sku": "Thrifty Swerve",
        "wheel_in": 4.0, "plate_in": (4.00, 4.00), "drop_in": 4.40, "mass_lb": 2.30,
        "drive_ratios": {"T1": 7.71, "T2": 6.48, "T3": 5.60},
        "steer_ratio": 25.0, "motors": ("drive", "steer"), "motor_orientation": "both above the plate",
        "encoder": "through-bore absolute encoder", "bearing": "large-bore azimuth bearing",
        "notes": "cost-optimised module; verify ratios against the current kit revision",
        "verify": _VERIFY,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Structure, transmission and fasteners, the vocabulary a real build uses.
# ─────────────────────────────────────────────────────────────────────────────
STRUCTURE: dict[str, dict[str, Any]] = {
    "tube_2x1": {"name": "2x1x0.100 in tube", "material": "6061-T6", "section_in": (2.0, 1.0),
                 "wall_in": 0.100, "mass_lb_per_ft": 0.66,
                 "use": "primary frame rails, elevator stages, arm beams"},
    "tube_1x1": {"name": "1x1x0.100 in tube", "material": "6061-T6", "section_in": (1.0, 1.0),
                 "wall_in": 0.100, "mass_lb_per_ft": 0.42,
                 "use": "light crossmembers, bumper backing, secondary structure"},
    "tube_2x2": {"name": "2x2x0.125 in tube", "material": "6061-T6", "section_in": (2.0, 2.0),
                 "wall_in": 0.125, "mass_lb_per_ft": 1.10,
                 "use": "climber towers and highly loaded uprights"},
    "maxtube": {"name": "MAXTube 2x1", "vendor": "REV Robotics", "section_in": (2.0, 1.0),
                "wall_in": 0.100, "use": "pre-patterned tube, cuts fabrication time on mechanisms"},
    # Nesting ladder for telescoping stages. A stage has to slide inside the member outboard
    # of it, so these are chosen for their outside dimension against the next tube's cavity —
    # 2x2x0.125 has a 1.75 in bore, which takes 1.5x1.5 plus a bearing block; 1.5x1.5x0.0625
    # has a 1.375 in bore, which takes 1x1. Never invent an intermediate size to make the
    # arithmetic work: if the ladder does not nest, change the stage count, not the catalog.
    "tube_1_5x1_5": {"name": "1.5x1.5x0.0625 in tube", "material": "6061-T6",
                     "section_in": (1.5, 1.5), "wall_in": 0.0625, "mass_lb_per_ft": 0.42,
                     "use": "first telescoping stage inside 2x2x0.125"},
    "tube_1x1_thin": {"name": "1x1x0.0625 in tube", "material": "6061-T6",
                      "section_in": (1.0, 1.0), "wall_in": 0.0625, "mass_lb_per_ft": 0.28,
                      "use": "second telescoping stage inside 1.5x1.5x0.0625"},
    "tube_1_5x0_5": {"name": "1.5x0.5x0.0625 in tube", "material": "6061-T6",
                     "section_in": (1.5, 0.5), "wall_in": 0.0625, "mass_lb_per_ft": 0.28,
                     "use": "single telescoping stage inside 2x1x0.100"},
    "gusset": {"name": "corner / mechanism gusset", "material": "6061-T6 plate 0.090–0.125 in",
               "use": "tie tube ends in shear rather than loading rivets in tension"},
    "bellypan": {"name": "pocketed bellypan", "material": "6061-T6 plate 0.090 in",
                 "use": "electronics mounting surface and chassis shear panel"},
    "bearing_hex": {"name": "1/2 in hex bore flanged bearing", "bore": "1/2 in hex",
                    "use": "standard shaft support in gearboxes and rollers"},
    "bearing_thunderhex": {"name": "3/8 in ThunderHex bearing", "bore": "3/8 in hex",
                           "use": "light rollers and intake shafts"},
    "shaft_hex": {"name": "1/2 in hex shaft", "material": "7075 or 1045", "use": "general power transmission"},
    "maxspline": {"name": "MAXSpline shaft", "vendor": "REV Robotics",
                  "use": "high-torque pivots, far better than hex under reversing load"},
    "compliant_wheel": {"name": "compliant intake wheel", "vendor": "AndyMark / WCP (class)",
                        "material": "30–40A urethane on a hex hub, 2–4 in OD",
                        "use": ("grip roller for intakes; pick durometer by gamepiece (softer grips harder "
                                "pieces) and set ~0.5 in compression from the roller center distance")},
    "roller_tube": {"name": "intake roller tube", "material": "1.5–2 in aluminium or polycarbonate "
                    "tube on a 1/2 in hex dead axle",
                    "use": ("powered intake/indexer roller; gear its surface speed above the robot's "
                            "approach speed so it pulls the gamepiece in instead of pushing it away")},
    "intake_gearbox": {"name": "static intake gearbox", "material": "6061 plates, 20 DP spur or HTD belt reduction",
                       "use": ("mounts to structure, not the moving arm, and belts power out to the roller "
                               "so belt tension does not change through the deploy arc")},
}

TRANSMISSION: dict[str, dict[str, Any]] = {
    "htd5": {"name": "HTD 5 mm belt", "pitch_mm": 5.0, "widths_mm": [9, 15],
             "use": "quiet, low-maintenance mechanism power; needs a fixed centre distance"},
    "gt2": {"name": "GT2 9 mm belt", "pitch_mm": 2.0, "widths_mm": [9],
            "use": "light, high-speed, low-torque paths"},
    "chain_25": {"name": "#25 roller chain", "pitch_in": 0.250,
                 "use": "compact chain runs; tensioner or slotted mount required"},
    "chain_35": {"name": "#35 roller chain", "pitch_in": 0.375,
                 "use": "high-torque drivetrain and climber runs"},
    "gears_20dp": {"name": "20 DP spur gears", "use": "high-load gearboxes"},
    "maxplanetary": {"name": "MAXPlanetary gearbox", "vendor": "REV Robotics",
                     "stages": [3, 4, 5, 9], "use": "stackable planetary reduction on brushless motors"},
    "versaplanetary": {"name": "VersaPlanetary", "vendor": "VEX",
                       "stages": [3, 4, 5, 7, 9, 10], "use": "modular reduction; respect the input torque limit"},
}

# Breaker → minimum wire gauge, per common FRC wiring practice.
WIRE_TABLE: list[dict[str, Any]] = [
    {"breaker_a": 40, "gauge_awg": 12, "preferred_awg": 10, "use": "brushless drive and mechanism motors"},
    {"breaker_a": 30, "gauge_awg": 14, "preferred_awg": 12, "use": "mid-size motors and the compressor"},
    {"breaker_a": 20, "gauge_awg": 16, "preferred_awg": 14, "use": "small motors and accessory rails"},
    {"breaker_a": 10, "gauge_awg": 18, "preferred_awg": 18, "use": "radio, RSL and sensor power"},
]
BATTERY_LEAD_AWG = 6


def wire_for_breaker(breaker_a: int) -> dict[str, Any]:
    for row in sorted(WIRE_TABLE, key=lambda r: r["breaker_a"], reverse=True):
        if breaker_a >= row["breaker_a"]:
            return row
    return WIRE_TABLE[-1]


# ─────────────────────────────────────────────────────────────────────────────
# Prompt-driven selection.  These read the *user's words*, so two different
# prompts pick genuinely different hardware instead of one canned loadout.
# ─────────────────────────────────────────────────────────────────────────────
_MODULE_ALIASES = [
    ("mk5n", (r"\bmk5n\b", r"\bmk\s*5n\b")),
    ("mk5i", (r"\bmk5i\b", r"\bmk\s*5i\b", r"\bmk\s*5\b")),
    ("mk4n", (r"\bmk4n\b", r"\bmk\s*4n\b")),
    ("mk4i", (r"\bmk4i\b", r"\bmk\s*4i\b")),
    ("maxswerve", (r"\bmax\s*swerve\b", r"\brev\s+swerve\b")),
    ("wcp_x", (r"\bwcp\b", r"\bx-?series\b", r"\bwest\s*coast\s+swerve\b")),
    ("thrifty", (r"\bthrifty\b", r"\bttb\b")),
    ("mk4", (r"\bmk4\b", r"\bmk\s*4\b")),
]

_MOTOR_ALIASES = [
    ("kraken_x60", (r"\bkraken\s*x?60\b", r"\bkraken\b")),
    ("kraken_x44", (r"\bkraken\s*x?44\b",)),
    ("neo_vortex", (r"\bvortex\b", r"\bneo\s*vortex\b")),
    ("neo_550", (r"\bneo\s*550\b", r"\b550\b")),
    ("neo", (r"\bneo\b",)),
    ("falcon_500", (r"\bfalcon\b",)),
    ("minion", (r"\bminion\b",)),
    ("cim", (r"\bcims?\b",)),
]


def _match(prompt: str, aliases) -> str | None:
    for key, patterns in aliases:
        if any(re.search(pattern, prompt, re.I) for pattern in patterns):
            return key
    return None


def select_module(prompt: str, wheel_in: float | None = None,
                  default_key: str | None = None) -> tuple[str, dict[str, Any]]:
    """Pick a swerve module from the prompt, falling back on the requested wheel size.

    `default_key` lets the caller vary the unstated-module fallback (e.g. seeded from the
    prompt hash) so different requests don't all land on the same module.
    """
    key = _match(prompt, _MODULE_ALIASES)
    if key is None:
        if wheel_in and wheel_in <= 3.25:
            key = "maxswerve"
        else:
            key = default_key if default_key in SWERVE_MODULES else "mk4i"
    return key, SWERVE_MODULES[key]


def select_motor(prompt: str, default: str = "kraken_x60") -> tuple[str, dict[str, Any]]:
    key = _match(prompt, _MOTOR_ALIASES) or default
    return key, MOTORS[key]


def select_drive_ratio(module: dict[str, Any], prompt: str) -> tuple[str, float]:
    """Honour an explicitly requested level/ratio, otherwise take the middle option."""
    ratios: dict[str, float] = module["drive_ratios"]
    # Lookarounds, not \b: a trailing \b can never match after the '+' in "L3+".
    explicit = re.search(r"(?<!\w)(l[1-4]\+?|x[1-3]|t[1-3]|r[1-3]|\d{2}t)(?!\w)", prompt, re.I)
    if explicit:
        token = explicit.group(1).upper()
        for label in ratios:
            if label.upper() == token:
                return label, ratios[label]
    numeric = re.search(r"(\d+(?:\.\d+)?)\s*:\s*1\s*(?:drive|gear|reduction)?", prompt, re.I)
    if numeric:
        wanted = float(numeric.group(1))
        label = min(ratios, key=lambda k: abs(ratios[k] - wanted))
        if abs(ratios[label] - wanted) < 1.5:
            return label, ratios[label]
    ordered = sorted(ratios.items(), key=lambda item: item[1])
    return ordered[len(ordered) // 2]


def free_speed_fps(motor: dict[str, Any], ratio: float, wheel_in: float) -> float:
    """Theoretical free speed, the honest ceiling, not an achievable number."""
    wheel_rpm = motor["free_rpm"] / ratio
    return wheel_rpm * math.pi * wheel_in / 12.0 / 60.0


def drivetrain_summary(module_key: str, module: dict[str, Any], motor_key: str,
                       motor: dict[str, Any], ratio_label: str, ratio: float,
                       module_count: int) -> dict[str, Any]:
    wheel_in = module["wheel_in"]
    speed = free_speed_fps(motor, ratio, wheel_in)
    # Torque at the wheel with every drive motor at stall, converted to a traction estimate.
    stall_wheel_nm = motor["stall_nm"] * ratio * module_count
    wheel_radius_m = wheel_in * 0.0254 / 2
    return {
        "module": module["name"], "module_key": module_key, "module_count": module_count,
        "motor": motor["name"], "motor_key": motor_key,
        "drive_ratio_label": ratio_label, "drive_ratio": round(ratio, 3),
        "steer_ratio": round(module["steer_ratio"], 3),
        "wheel_diameter_in": wheel_in,
        "free_speed_fps": round(speed, 2),
        "stall_thrust_lbf": round(stall_wheel_nm / wheel_radius_m * 0.2248, 1),
        "drive_motors": module_count, "steer_motors": module_count,
        "module_mass_lb": round(module["mass_lb"] * module_count, 1),
        "caveat": ("Free speed is the unloaded ceiling and stall thrust ignores traction limits; "
                   "both need a real current-limited simulation before you trust them."),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Electrical layout + power budget.  This is where Kale's electrical side earns
# its keep: real channels, real breakers, real wire gauges.
# ─────────────────────────────────────────────────────────────────────────────
def power_budget(loads: list[dict[str, Any]], distributor_key: str = "pdh") -> dict[str, Any]:
    """Assign every motor/accessory to a channel and size its breaker and wire."""
    distributor = ELECTRONICS[distributor_key]
    channels = distributor["channels"]
    high_left = channels["high_current"]
    low_left = channels["low_current"]
    rows: list[dict[str, Any]] = []
    overflow: list[str] = []
    channel_no = 0
    total_stall_a = 0.0

    for load in loads:
        breaker = int(load.get("breaker_a", 40))
        wire = wire_for_breaker(breaker)
        if breaker >= 20:
            if high_left <= 0:
                overflow.append(load["name"])
                continue
            high_left -= 1
        else:
            if low_left <= 0:
                overflow.append(load["name"])
                continue
            low_left -= 1
        rows.append({
            "channel": channel_no, "load": load["name"], "subsystem": load.get("subsystem", "controls"),
            "breaker_a": breaker, "wire_awg": wire["preferred_awg"],
            "stall_a": load.get("stall_a", 0),
        })
        total_stall_a += float(load.get("stall_a", 0) or 0)
        channel_no += 1

    # Don't extrapolate V = V0 - I·R out to the summed stall current: the battery cannot
    # source thousands of amps and the linear model returns physically meaningless (negative)
    # voltages. The useful number is the total draw at which the bus reaches the roboRIO's
    # brownout threshold, that is the budget the current limits have to stay inside.
    battery = ELECTRONICS["battery"]
    resistance_ohm = battery["internal_resistance_mohm"] / 1000.0
    brownout_v = 6.8
    brownout_current_a = round((battery["nominal_v"] - brownout_v) / resistance_ohm)
    headroom = round(brownout_current_a / total_stall_a * 100) if total_stall_a else 100
    return {
        "distributor": distributor["name"], "distributor_key": distributor_key,
        "channels_used": len(rows), "high_current_free": high_left, "low_current_free": low_left,
        "assignments": rows,
        "unassigned": overflow,
        "main_breaker_a": ELECTRONICS["main_breaker"]["rating_a"],
        "battery_lead_awg": BATTERY_LEAD_AWG,
        "summed_stall_current_a": round(total_stall_a),
        "brownout_threshold_v": brownout_v,
        "brownout_current_a": brownout_current_a,
        "stall_headroom_pct": headroom,
        "notes": [
            f"Summed stall current across every motor is {round(total_stall_a)} A. That is an "
            "arithmetic worst case, not a real operating point, no battery sources it and the "
            "robot never stalls everything at once.",
            f"The number to design against: on a nominal "
            f"{battery['internal_resistance_mohm']:g} mΩ battery the bus reaches the ~{brownout_v} V "
            f"brownout threshold at roughly {brownout_current_a} A total draw. Set per-motor "
            f"current limits so realistic simultaneous draw stays well under that.",
            f"The {ELECTRONICS['main_breaker']['rating_a']} A main breaker trips long before the "
            "summed stall figure, and every branch runs through it, nothing bypasses it except "
            "the battery lead itself.",
            "Battery internal resistance rises sharply as a battery ages; an old battery browns "
            "out at a current a fresh one handles.",
        ],
    }


def motor_loads(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Enumerate the electrical loads implied by a robot spec."""
    loads: list[dict[str, Any]] = []
    drive = spec.get("drivetrain") or {}
    motor_key = drive.get("motor_key", "kraken_x60")
    motor = MOTORS.get(motor_key, MOTORS["kraken_x60"])
    # `drive_motors`/`steer_motors` are what is installed; a swerve-ready frame reserves the
    # module envelope but draws no drivetrain current.
    for index in range(int(drive.get("drive_motors", 0) or 0)):
        loads.append({"name": f"{motor['name']} drive {index + 1}", "subsystem": "drivetrain",
                      "breaker_a": motor["breaker_a"], "stall_a": motor["stall_a"]})
    steer_key = drive.get("steer_motor_key", motor_key)
    steer = MOTORS.get(steer_key, motor)
    for index in range(int(drive.get("steer_motors", 0) or 0)):
        loads.append({"name": f"{steer['name']} steer {index + 1}", "subsystem": "drivetrain",
                      "breaker_a": steer["breaker_a"], "stall_a": steer["stall_a"]})

    for subsystem, key in (("intake", "intake"), ("shooter", "shooter"),
                           ("elevator", "elevator"), ("arm", "manipulator"), ("climber", "climber")):
        block = spec.get(key) or {}
        if not block.get("included"):
            continue
        chosen = MOTORS.get(block.get("motor_key", "neo"), MOTORS["neo"])
        for index in range(int(block.get("motor_count", 1))):
            loads.append({"name": f"{chosen['name']} {subsystem} {index + 1}", "subsystem": subsystem,
                          "breaker_a": chosen["breaker_a"], "stall_a": chosen["stall_a"]})
    if (spec.get("pneumatics") or {}).get("included"):
        loads.append({"name": ELECTRONICS["compressor"]["name"], "subsystem": "pneumatics",
                      "breaker_a": 20, "stall_a": 25})
    loads.append({"name": ELECTRONICS["radio"]["name"], "subsystem": "controls",
                  "breaker_a": 10, "stall_a": 3})
    return loads


def electrical_layout(frame_w_mm: float, frame_l_mm: float, spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Place the control system on the bellypan.

    Positions are driven by real packaging rules: battery near the centroid and low, the
    main breaker on an outside edge where a referee and a driver can reach it, the radio
    high and clear of metal, and the roboRIO with its connector edge unobstructed.
    """
    pan_z = 64.0  # top face of the bellypan, mm
    distributor_key = (spec.get("electrical") or {}).get("distributor_key", "pdh")
    placements: list[dict[str, Any]] = []

    def place(key: str, x: float, y: float, z: float, rot_deg: float = 0.0, note: str = "") -> None:
        part = ELECTRONICS[key]
        w, d, h = (dimension * IN for dimension in part["envelope_in"])
        if rot_deg == 90:
            w, d = d, w
        placements.append({"key": key, "name": part["name"], "vendor": part["vendor"],
                           "sku": part.get("sku", ""), "mass_lb": part["mass_lb"],
                           "size_mm": [round(w, 1), round(d, 1), round(h, 1)],
                           "center_mm": [round(x, 1), round(y, 1), round(z + h / 2, 1)],
                           "rotation_deg": rot_deg, "note": note or part.get("mount", ""),
                           "verify": part.get("verify", _VERIFY)})

    cx, cy = frame_w_mm / 2, frame_l_mm / 2
    battery_h = ELECTRONICS["battery"]["envelope_in"][2] * IN
    place("battery", cx, cy + frame_l_mm * 0.20, pan_z, 90,
          "Centred fore-aft-biased and low, it is the single largest mass on the robot.")
    place(distributor_key, cx, cy - frame_l_mm * 0.16, pan_z, 0,
          "Central so no branch run is long; keep the channel LEDs readable.")
    place("main_breaker", frame_w_mm - 70, cy + frame_l_mm * 0.30, pan_z, 0,
          "On an outside edge, visible and reachable without removing anything.")
    place("sb50", cx + 90, cy + frame_l_mm * 0.30, pan_z,
          0, "Retained so the mating force never loads the 6 AWG lead.")
    place("rio", cx - frame_w_mm * 0.24, cy - frame_l_mm * 0.02, pan_z, 90,
          "Connector edge faces outboard; SD card and USB stay serviceable.")
    place("pigeon", cx, cy, pan_z, 0, "Flat, rigid and near the rotation centre.")
    place("radio", cx + frame_w_mm * 0.26, cy - frame_l_mm * 0.05, pan_z + 220, 0,
          "Raised on a standoff mast, antennas clear of aluminium on both faces.")
    place("rsl", cx, cy - frame_l_mm * 0.34, pan_z + 300, 0,
          "Highest practical point with an all-round sight line.")
    if distributor_key == "pdp":
        place("vrm", cx - frame_w_mm * 0.06, cy - frame_l_mm * 0.30, pan_z, 0,
              "Feeds the radio from the PDP's fused rail.")
    if (spec.get("drivetrain") or {}).get("second_can_bus"):
        place("canivore", cx - frame_w_mm * 0.24, cy - frame_l_mm * 0.16, pan_z, 0,
              "Dedicated drivetrain CAN FD bus; keep the USB run short.")
    if (spec.get("pneumatics") or {}).get("included"):
        place("ph", cx + frame_w_mm * 0.22, cy - frame_l_mm * 0.28, pan_z, 0,
              "Close to the manifold so the tubing runs stay short.")
        place("compressor", cx - frame_w_mm * 0.18, cy + frame_l_mm * 0.30, pan_z, 0,
              "Vibration-isolated; budget its 3 lb early.")
    _ = battery_h
    return placements


def catalog_digest() -> dict[str, Any]:
    """Small machine-readable summary used in dossiers and training exports."""
    return {
        "parts_version": PARTS_VERSION,
        "counts": {"electronics": len(ELECTRONICS), "motors": len(MOTORS),
                   "swerve_modules": len(SWERVE_MODULES), "structure": len(STRUCTURE),
                   "transmission": len(TRANSMISSION)},
        "swerve_modules": sorted(SWERVE_MODULES),
        "motors": sorted(MOTORS),
        "disclaimer": ("Envelopes and ratios are nominal published figures for packaging and "
                       "first-order sizing. Confirm every one against the vendor drawing."),
    }
