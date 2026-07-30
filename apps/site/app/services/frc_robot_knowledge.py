"""Versioned FRC mechanism knowledge used to ground robot synthesis.

This is intentionally small and auditable.  Kale does not copy meshes from these
documents; it uses their public assembly patterns and engineering guidance to choose
real subsystem structure instead of hallucinating a robot-shaped collection of boxes.

The season *profiles* below are derived from `frc_season.SEASONS` rather than typed here, so
the frame size, gamepiece and default mechanisms a profile hands to the synthesiser can never
disagree with the field and rule figures the same season states elsewhere.  What lives here is
the part a season does not determine: the build techniques, and the public references whose
patterns the geometry is grounded in.
"""
from __future__ import annotations

from typing import Any

from app.services.frc_season import ALIASES, SEASONS, resolve_season

KNOWLEDGE_VERSION = "frc-exemplars-2026.09"

REFERENCES: list[dict[str, str]] = [
    {
        "name": "2025 Serpentheim CAD Release",
        "kind": "full_robot",
        "url": "https://cad.onshape.com/documents/9b4847a461c2a185e6ff320a/w/6df8cee50adeaba604998544/e/ed071581b5e32ec2f2371cae",
        "lesson": "2x1 tube chassis; subsystem hierarchy; rigid assemblies and explicit mates",
    },
    {
        "name": "6328 Crescendo shooter / robot",
        "kind": "shooter",
        "url": "https://cad.onshape.com/documents/9835aac8853d62a08e3c07ca/w/f30aeffcca5908da5dfd5c99/e/f183765154c242ac19e7644d",
        "lesson": "split flywheel sides, feeder path, serviceable side plates",
    },
    {
        "name": "2910 Rapid React robot",
        "kind": "shooter_climber",
        "url": "https://cad.onshape.com/documents/688a490dcb5a1fe1ee9d29b7/w/8c825c0c0ba4e183e6f824fa/e/cb595133c87d2eee9f83ca38",
        "lesson": "hood, feeder and climber as separate top-level assemblies",
    },
    {
        "name": "FRCDesign coaxial slapdown intake",
        "kind": "intake",
        "url": "https://cad.onshape.com/documents/17302d787e092ce11015f7ee/w/f7cf5c02c7655f0328a3a74a/e/f1456325e0175c4c081008c2",
        "lesson": "static gearbox, rigid arm, top-level revolute mate and powered roller path",
    },
    {
        "name": "FRCDesign roller-intake reference",
        "kind": "intake",
        "url": "https://frcdesign.org/mechanism-examples/",
        "lesson": ("roller center distance sets compression; series rollers hand off to an indexer; "
                   "surface speed geared above approach speed; compliant-wheel durometer matched "
                   "to the gamepiece; metal hard stops on the deploy arc"),
    },
    {
        "name": "FRCDesign 2-stage cascade elevator",
        "kind": "elevator",
        "url": "https://cad.onshape.com/documents/da5aef9e6bf6e869f4a51a45/w/5a0f4a3426876db0ba214277/e/f8fd8133abcb12800eacb5d1",
        "lesson": "static frame, independently rigid stages, carriage and slider mates",
    },
    {
        "name": "2025 REV ION Starter Bot",
        "kind": "full_robot",
        "url": "https://docs.revrobotics.com/frc-kickoff-concepts/2025/starter-bot",
        "lesson": "manufacturable single-stage elevator, wrist and intake packaging",
    },
    {
        "name": "FRCDesign mechanism library",
        "kind": "design_guidance",
        "url": "https://frcdesign.org/mechanism-examples/",
        "lesson": "layout sketches, dead axles, power transmission and subsystem boundaries",
    },
    {
        "name": "FRCDesign elevator and arm CAD walkthroughs",
        "kind": "elevator_arm",
        "url": "https://frcdesign.org/learning-course/",
        "lesson": ("stage overlap carries the tip moment; rigging terminations at every stage; "
                   "dead-axle joints in double shear; hard stops outside the software limits"),
    },
    {
        "name": "WCP / REV structural tube and gusset catalog",
        "kind": "design_guidance",
        "url": "https://wcproducts.com/collections/structure",
        "lesson": ("model to stock sections — 2x1x0.100, 1x1, 2x2x0.125 — so a cut list is "
                   "orderable; gussets in double shear at every bolted corner"),
    },
    {
        "name": "2026 FRC Game Manual",
        "kind": "rules",
        "url": "https://firstfrc.blob.core.windows.net/frc2026/Manual/HTML/2026GameManual.htm",
        "lesson": "current starting perimeter, height, bumper, safety and extension constraints",
    },
    {
        "name": "WCP GreyT turret",
        "kind": "shooter",
        "url": "https://wcproducts.com/products/greyt-turret",
        "lesson": ("a turret is one large-bore bearing plus plate bearing blocks and a driven "
                   "ring; the hard part is the wire path under the plate, not the rotation"),
    },
    {
        "name": "Open-alliance spindexer and hopper build threads",
        "kind": "hopper",
        "url": "https://www.chiefdelphi.com/c/technical/open-alliance/",
        "lesson": ("bulk ball indexing converges on a rotating floor that feeds one lane: "
                   "raise the driven wheels slightly above the floor, funnel many lanes into "
                   "one, and gate the last piece with a sensor rather than a timer"),
    },
    {
        "name": "Public team CAD and technical binder releases",
        "kind": "design_guidance",
        "url": "https://www.chiefdelphi.com/c/technical/robot-showcase/",
        "lesson": ("a technical binder states, per subsystem: the requirement, the options "
                   "considered, the calculation that chose between them, the test that "
                   "validated it and the failure mode still open — write the design that way "
                   "and the review takes minutes"),
    },
]

# Profiles are the synthesiser's view of a season: the handful of fields it needs to start a
# robot. They are generated from the season model so a frame size or gamepiece can never be
# stated twice and drift.
PROFILES: dict[str, dict[str, Any]] = {
    key: {
        "label": season["label"] if season["year"] else "Off-season reference frame",
        "season": season["label"],
        "season_key": key,
        "frame": season["frame"],
        "starting_height_in": season["starting_height_in"],
        "target_weight_lb": season["target_weight_lb"],
        "gamepiece": {"name": season["gamepiece"]["name"],
                      "diameter_in": season["gamepiece"]["diameter_in"]},
        "default_subsystems": list(season["default_subsystems"]),
    }
    for key, season in SEASONS.items()
}
# Ids that older designs and corpora were generated with still resolve.
PROFILES.update({legacy: PROFILES[target] for legacy, target in ALIASES.items()
                 if target in PROFILES and legacy not in PROFILES})


# Build techniques.  Each one is a decision a real team makes, with the reason it exists and
# the failure it prevents, this is what the model is asked to reason with, and what the
# dossier explains back to the user.
TECHNIQUES: dict[str, list[dict[str, str]]] = {
    "chassis": [
        {"name": "Belly pan as a shear panel",
         "why": "A four-rail rectangle racks under impact; a bolted plate makes it rigid.",
         "how": "Pocket a 0.090 in plate and bolt it to every rail, not just the corners.",
         "pitfall": "Pocketing away the bolt-line material and losing the shear path."},
        {"name": "Bolt-together over welded frames",
         "why": "A welded frame that bends is scrap; a bolted one is a replaced rail.",
         "how": "Gussets in double shear at every corner, rivets or 10-32 through both walls.",
         "pitfall": "Loading rivets in tension instead of shear."},
        {"name": "Reserve the module corners first",
         "why": "Swerve corners are non-negotiable volume; everything else moves around them.",
         "how": "Place the four module envelopes before any mechanism sketch exists.",
         "pitfall": "Routing a mechanism through a corner and discovering it at assembly."},
    ],
    "drivetrain": [
        {"name": "Current limits before gear ratios",
         "why": "The battery, not the motor curve, sets what the drivetrain can actually do.",
         "how": "Set a per-motor supply limit, then pick the ratio that hits your target speed.",
         "pitfall": "Choosing a fast ratio, browning out, and blaming the code."},
        {"name": "Dedicated drivetrain CAN bus",
         "why": "Eight modules of status frames will saturate a shared bus.",
         "how": "Put drive and steer controllers on a CANivore, leave mechanisms on the RIO bus.",
         "pitfall": "Debugging 'random' drivetrain stutter that is really bus utilisation."},
        {"name": "Azimuth zeroing procedure",
         "why": "A module that boots at the wrong angle drives into the wall on enable.",
         "how": "Absolute encoder offsets stored in code, with a documented physical zeroing jig.",
         "pitfall": "Re-zeroing by feel after every belt change."},
    ],
    "electrical": [
        {"name": "One main breaker, no bypasses",
         "why": "It is the only guaranteed way to kill the robot, and it is inspected.",
         "how": "Battery + goes to the main breaker, breaker to the distributor, nothing else.",
         "pitfall": "Tapping power upstream of the breaker for 'just one' accessory."},
        {"name": "Wire gauge follows the breaker, not the motor",
         "why": "The breaker decides how much current the wire will ever be asked to carry.",
         "how": "40 A channel → 10 AWG, 30 A → 12 AWG, 10 A accessories → 18 AWG.",
         "pitfall": "18 AWG on a 40 A channel because it was the spool on the table."},
        {"name": "Service loops and strain relief",
         "why": "Wires break at the crimp when a mechanism pulls on them.",
         "how": "Leave slack at every moving joint and anchor the harness on both sides of it.",
         "pitfall": "A taut wire across a pivot that survives testing and fails in a match."},
        {"name": "Radio placement is an RF decision",
         "why": "Aluminium shadows and motor noise cause 'random' disconnects.",
         "how": "Mount high, antennas clear on both faces, away from motors and CAN bundles.",
         "pitfall": "Bolting it flat to the bellypan under the battery."},
    ],
    "intake": [
        {"name": "Compression is the design variable",
         "why": "Too little and it never grabs; too much and it stalls or damages the gamepiece.",
         "how": "Start near 0.5 in of compression on compliant wheels and tune on a test rig.",
         "pitfall": "Setting compression by eye at 2 am and never measuring it."},
        {"name": "Coaxial pivot power",
         "why": "Running roller power through the pivot axis removes a whole tensioning problem.",
         "how": "Belt to a shaft on the pivot centreline, then out to the roller.",
         "pitfall": "Non-coaxial routing whose belt tension changes through the deploy arc."},
        {"name": "Hard stops in metal, not in software",
         "why": "Software limits fail when the encoder does.",
         "how": "A physical stop at each end of the deploy arc plus a current-limited hold.",
         "pitfall": "A 40 A motor grinding against a plastic stop."},
        {"name": "Surface speed must beat approach speed",
         "why": "A roller slower than the robot's floor speed shoves the gamepiece away instead of pulling it in.",
         "how": ("Gear the roller so its surface speed (roller RPM x pi x diameter) is 1.5-2x the "
                 "fastest intended approach speed; a 2 in roller off a NEO through ~4:1 clears this easily."),
         "pitfall": "A direct-driven roller that stalls the motor and still cannot out-run the drivebase."},
        {"name": "Center distance sets compression, not eyeballing",
         "why": "Compression is a dimension you cut into the plates, not a feel you dial in at 2 am.",
         "how": ("Fix the roller-to-bumper (or roller-to-roller) center distance so free wheel radius "
                 "minus gap equals ~0.5 in of squish on a compliant wheel; put it in the layout sketch."),
         "pitfall": "Slotted mounts 'adjusted to feel', so every rebuild grabs differently."},
        {"name": "Durometer picks the wheel, not diameter alone",
         "why": "A soft compliant wheel conforms and grips; a hard one skips over a rigid gamepiece.",
         "how": ("Softer (30-35A green compliant) for hard/heavy pieces, firmer (40A+) for light foam; "
                 "match the wheel to the gamepiece and keep spares — they wear."),
         "pitfall": "One durometer chosen for spin-up that never actually grabs the game piece."},
        {"name": "Two-roller handoff into an indexer",
         "why": "A single roller acquires; a second staged roller controls the piece into the feeder.",
         "how": ("Series rollers geared to increasing surface speed, then a positive indexer stage with "
                 "a beam-break so the shooter/elevator gets one piece at a known position."),
         "pitfall": "One fast roller that flings acquired pieces straight back out or jams two at once."},
        {"name": "Over-bumper vs under-bumper is a geometry decision",
         "why": "The two solve different floor-clearance and deploy-volume problems, not style.",
         "how": ("Over-bumper reaches down past the bumper on a pivot for tall/rolling pieces; "
                 "under-bumper stays low and fixed for flat pieces but needs a bumper gap cut."),
         "pitfall": "Copying a top team's over-bumper arm for a gamepiece that a fixed roller would grab."},
    ],
    "shooter": [
        {"name": "Recovery time over top speed",
         "why": "Cycle rate is set by how fast the wheel returns to setpoint after a shot.",
         "how": "Add flywheel inertia and headroom rather than chasing peak RPM.",
         "pitfall": "A fast, light wheel that needs two seconds between shots."},
        {"name": "Split-side flywheels",
         "why": "Independent sides let you spin the gamepiece and correct drift.",
         "how": "Separate motors per side, coupled only through the gamepiece.",
         "pitfall": "One shaft across both sides that makes spin impossible."},
        {"name": "Serviceable side plates",
         "why": "You will pull the flywheel more than once during competition.",
         "how": "Bolt-on plates, dowel-located, that come off without dropping the shaft.",
         "pitfall": "A shooter you have to disassemble the robot to reach."},
        {"name": "Exit velocity is half the surface speed, with one wheel",
         "why": ("A gamepiece squeezed between a spinning wheel and a stationary hood leaves at "
                 "roughly the mean of the two surfaces, so a single wheel gives half its own "
                 "surface speed — teams routinely size a shooter as if it gave all of it."),
         "how": ("Compute surface speed as free RPM ÷ reduction × π × wheel diameter, then halve "
                 "it for a hooded single wheel, or keep it for counter-rotating pairs."),
         "pitfall": "A shooter geared for a range it can only reach on paper."},
        {"name": "Compression sets the energy transfer",
         "why": ("Too little compression and the wheel slips on the gamepiece; too much and the "
                 "wheel stalls and the shot is short and inconsistent."),
         "how": ("Set the wheel-to-hood gap about half an inch under the gamepiece diameter and "
                 "make the hood adjustable so it can be tuned on the practice field."),
         "pitfall": "A welded-position hood that cannot be tuned once the game piece wears in."},
        {"name": "Stage a barrel shooter so each pair adds speed",
         "why": ("A staged barrel accelerates progressively instead of dumping all the energy in "
                 "one impulse, which is gentler on the gamepiece and easier on the motors."),
         "how": ("Each successive flywheel pair runs faster than the one before it and the pairs "
                 "sit one gamepiece diameter apart down the barrel."),
         "pitfall": "Running every stage at the same speed, so the later pairs just drag."},
        {"name": "Turret on one large-bore bearing",
         "why": "A turret carries moment loads, not just rotation; a stack of small bearings wobbles.",
         "how": ("One large-diameter X-contact or lazy-susan style bearing under the whole turret, "
                 "driven by a geared ring or a capstan drive for low backlash."),
         "pitfall": "A cantilevered turret on a single small bearing that shakes the shot loose."},
        {"name": "Turret wire management with hard stops",
         "why": "A turret that spins freely rips its own harness out mid-match.",
         "how": ("Limit rotation with physical hard stops and a wire-wrap service loop sized for "
                 "the full sweep, or fit a slip ring if continuous rotation is genuinely needed."),
         "pitfall": "Software-only rotation limits guarding a taut cable bundle."},
        {"name": "Stage the acceleration down a barrel",
         "why": ("One impulse from a single flywheel pair wastes energy in slip and scuffs the "
                 "gamepiece; several pairs in series each add a little speed."),
         "how": ("Run 2-3 flywheel pairs down a guided barrel at increasing surface speed, each "
                 "pair lightly compressed, so the piece leaves at the sum of the stages."),
         "pitfall": "Stages geared the same speed, so later pairs brake the piece instead of driving it."},
        {"name": "Guide the piece, don't just launch it",
         "why": "Exit angle repeatability comes from the guide path, not from the wheels.",
         "how": ("Constrain the piece through the whole barrel with polycarbonate guides and a "
                 "fixed exit lip; keep the last stage closest to the exit."),
         "pitfall": "An open path where the piece rattles and every shot leaves at a different angle."},
        {"name": "Zero the turret like a swerve azimuth",
         "why": "A turret that boots at the wrong angle aims at your own alliance wall.",
         "how": "Absolute encoder on the azimuth with stored offsets and a documented zeroing jig.",
         "pitfall": "Re-zeroing by eye in the pit and trusting it in the match."},
    ],
    "hopper": [
        {"name": "One lane out, however many lanes in",
         "why": ("A shooter can only take one gamepiece at a time. Every mechanism that tries "
                 "to hand it two at once jams, and the jam always happens under load in a match."),
         "how": ("Funnel the floor into a single exit lane, and make the last stage before the "
                 "shooter positive — a driven roller or belt that owns the piece — with a "
                 "beam-break telling the code exactly one is staged."),
         "pitfall": "A wide hopper that dumps straight into the feeder and wedges two pieces in the throat."},
        {"name": "Raise the driven wheels above the floor",
         "why": ("A rotating-floor indexer works by driving the gamepiece across a stationary "
                 "surface. If the wheels are flush the piece rides on the floor and slips; too "
                 "high and it climbs over."),
         "how": ("Set the wheel axis so roughly half an inch of wheel stands above the floor "
                 "plate for a 6 in ball, then test with a full hopper, not one piece."),
         "pitfall": "Tuning the indexer with three gamepieces and finding it stalls with twelve."},
        {"name": "Test the hopper full, and test it on the bump",
         "why": ("Throughput measured with a half-empty hopper is fiction: the pieces at the "
                 "bottom carry the weight of the ones above them, and driving over an obstacle "
                 "throws the whole mass at one wall."),
         "how": ("Fill it to capacity, drive the real field obstacles, and count pieces per "
                 "second out of the exit rather than watching it spin."),
         "pitfall": "A hopper that indexes beautifully on the bench and packs solid in a match."},
        {"name": "Compression is the jam knob",
         "why": ("Indexer compression trades throughput against current draw and jamming. Too "
                 "loose and pieces slip; too tight and the motor heats, the bus sags and the "
                 "pieces wedge."),
         "how": ("Set it as a dimension in the plates, then back it off in small steps while "
                 "watching supply current until throughput stops improving."),
         "pitfall": "Chasing jams by tightening compression, which is usually what caused them."},
        {"name": "Give the hopper a floor you can open",
         "why": "Every bulk handler jams eventually, and a jam you can clear in ten seconds between matches is a different problem from one that needs the shooter removed.",
         "how": "Make one wall or the top plate a thumbscrew panel, and keep the exit lane visible.",
         "pitfall": "A sealed hopper that has to come off the robot to clear one wedged piece."},
    ],
    "turret": [
        {"name": "One large-bore bearing carries the whole turret",
         "why": "A turret carries moment loads, not just rotation; a stack of small bearings wobbles and the shot walks.",
         "how": ("One large-diameter slew or X-contact bearing under the turret plate, driven "
                 "by a ring gear or a capstan for low backlash."),
         "pitfall": "A cantilevered turret on one small bearing that shakes the aim loose over a match."},
        {"name": "The wire path is the design problem",
         "why": ("The bearing is the easy part. Getting motor power, CAN and a camera across a "
                 "rotating joint without tearing a harness is what actually takes the time."),
         "how": ("An energy chain or a service loop sized for the full sweep, tensioned by a "
                 "constant-force spring so it never goes slack and snags, with physical rotation "
                 "limits at both ends. A slip ring only if continuous rotation is genuinely needed."),
         "pitfall": "Software-only rotation limits guarding a taut cable bundle."},
        {"name": "Keep the sweep inside the frame perimeter",
         "why": "A turret that swings a shooter past the perimeter is an extension-rule failure at inspection, not a packaging annoyance.",
         "how": ("Sweep the widest point of the turret through its full range in CAD against the "
                 "perimeter and the extension allowance before committing the plate size."),
         "pitfall": "Discovering at inspection that the hood clears the bumper at 45°."},
        {"name": "Zero the azimuth like a swerve module",
         "why": "A turret that boots at the wrong angle aims at your own alliance wall.",
         "how": "Absolute encoder on the azimuth with stored offsets and a documented zeroing jig.",
         "pitfall": "Re-zeroing by eye in the pit and trusting it in the match."},
        {"name": "Decide what the turret is for before building one",
         "why": ("A turret buys shoot-while-moving and passing without turning. If the strategy "
                 "does not use either, it is mass, complexity and a wire path for nothing."),
         "how": ("Write down the two or three match situations that justify it. If a fixed "
                 "shooter and a good driver cover them, build the fixed shooter."),
         "pitfall": "Building a turret because the top teams have one, then aiming with the drivebase anyway."},
    ],
    "elevator": [
        {"name": "Rigid stages, sliders between them",
         "why": "The CAD mate structure mirrors how the real thing moves and binds.",
         "how": "Each stage a rigid subassembly; one slider mate per interface.",
         "pitfall": "A soup of part-level mates that never solves cleanly."},
        {"name": "Preload the bearing blocks",
         "why": "Slop in a cascade multiplies at the carriage.",
         "how": "Opposed bearings taking load in both directions at every stage.",
         "pitfall": "A carriage that wobbles an inch at full extension."},
        {"name": "Rig the tower in belt or chain, sized on holding load",
         "why": ("A rigged tower carries the whole superstructure through one belt/chain path; "
                 "it fails at the weakest termination, not in the middle."),
         "how": ("HTD 5 mm belt or #25 chain with a positive termination at each stage, a "
                 "tensioner on the slack side, and the reduction sized on holding torque at full "
                 "extension, not on free speed."),
         "pitfall": "A tensioner added after the fact that changes the rigging length under load."},
        {"name": "Two uprights, not one mast",
         "why": "A single mast twists; a pair of uprights tied top and bottom resists the moment.",
         "how": ("Two 2x1 uprights tied by a top crossmember and into the bellypan in shear, with "
                 "the carriage riding bearing blocks on both."),
         "pitfall": "A cantilevered single tube that wobbles an inch at the top of the extension."},
        {"name": "Constant-force rigging check",
         "why": "Cascade rigging that is a stage out of sync tears itself apart.",
         "how": "Verify the rope path stage by stage before the first powered run.",
         "pitfall": "Powering it up to find out."},
        {"name": "Stage overlap is what carries the moment",
         "why": ("At full extension the only thing resisting the tip load is the overlap between "
                 "a stage and the one outboard of it; too little overlap and the tower folds."),
         "how": ("Keep at least 20% of the stage travel as remaining overlap at full extension, "
                 "with a bearing block at each end of that overlap so the pair works as a couple."),
         "pitfall": "Maximising travel by shortening the stages until the overlap disappears."},
        {"name": "Cascade multiplies travel and speed together",
         "why": ("An N-stage cascade moves the carriage N times the drum's own payout, so it is "
                 "also N times faster and N times harder to stop."),
         "how": ("Compute carriage travel as stage travel × stage count, then re-check the "
                 "reduction against the deceleration you actually want at the top."),
         "pitfall": "Sizing the gearbox on one stage and being surprised by the carriage speed."},
    ],
    "arm": [
        {"name": "Dead axle pivots",
         "why": "A fixed shaft in bearing blocks is stiffer and far easier to service.",
         "how": "Shaft bolted to structure, arm rotating on bearings around it.",
         "pitfall": "A live axle whose retaining collar walks loose under reversing load."},
        {"name": "Gravity compensation in the ratio",
         "why": "The worst case is holding the arm horizontal, not moving it.",
         "how": "Size the reduction on holding torque at full extension, then check speed.",
         "pitfall": "A reduction sized on free speed that cannot hold its own weight."},
        {"name": "MAXSpline or keyed shafts on high-torque pivots",
         "why": "Hex rounds out under reversing shock load.",
         "how": "Splined shaft at the shoulder where torque reverses every cycle.",
         "pitfall": "Rounded hex and a shoulder with 10 degrees of backlash."},
        {"name": "Split a long reach into two segments",
         "why": ("Holding torque grows with the square of reach for a single beam, so one long "
                 "arm needs a gearbox and a beam section nobody wants to build."),
         "how": ("Two segments of roughly 60/40 the total reach, each with its own reduction; "
                 "the elbow carries far less load than a single shoulder would."),
         "pitfall": "One 30 in beam on a 100:1 shoulder that deflects two inches under a game piece."},
        {"name": "Hard stops before software limits",
         "why": ("Software limits fail with a dead encoder; a metal stop does not, and the "
                 "shoulder is where a runaway does the most damage."),
         "how": ("A machined stop at each end of the shoulder arc, positioned just outside the "
                 "software limit so the stop is only ever reached by a fault."),
         "pitfall": "Relying on a soft limit and rebuilding the shoulder after one bad zero."},
        {"name": "Bearing blocks in pairs at every joint",
         "why": "A joint on one bearing is a cantilever and the shaft cocks under load.",
         "how": "A bearing in each side plate on the same bore, with the shaft in double shear.",
         "pitfall": "Supporting a pivot from one plate and calling the other side a spacer."},
    ],
    "climber": [
        {"name": "Ratchet or brake before you need it",
         "why": "A winch that back-drives drops the robot.",
         "how": "Mechanical ratchet on the drum, not just motor brake mode.",
         "pitfall": "Relying on brake mode after the match ends and power cuts."},
        {"name": "Load path into the frame",
         "why": "The climber carries the entire robot weight through whatever it is bolted to.",
         "how": "Tie the tower into two rails and the bellypan, in shear.",
         "pitfall": "A tower bolted to a single crossmember that folds."},
        {"name": "Size the winch on drum torque, not motor torque",
         "why": ("The drum radius is a lever working against you: rope tension times drum radius "
                 "is the torque the gearbox has to hold, and a bigger drum makes it worse."),
         "how": ("Tension is robot weight ÷ load paths; drum torque is tension × drum radius; the "
                 "reduction is that torque divided by what the motors give at a safe duty point, "
                 "not at stall."),
         "pitfall": "A 2 in drum chosen for rope capacity that doubles the required reduction."},
        {"name": "Two load paths beat one strong one",
         "why": ("Two hooks halve the tension in each rope and stop the robot swinging on a "
                 "single point, which is what usually pulls a hook out of the cage."),
         "how": "Dual telescoping hooks on a common shaft so they pay out together.",
         "pitfall": "Two independent winches that drift out of sync and cock the robot."},
        {"name": "Stow inside the starting volume",
         "why": "It is an inspection failure, not a design preference.",
         "how": "Check the stowed height against the current manual before committing.",
         "pitfall": "Discovering it at inspection on Thursday."},
        {"name": "Easy in, hard out cage engagement",
         "why": "A deep-cage or bar hook must enter with almost no resistance and resist exit under load.",
         "how": ("Barb or hook geometry that slips in freely, then bears on a groove or shoulder "
                 "when the winch loads it, pull-test the engagement before trusting it at the buzzer."),
         "pitfall": "A hook that binds on entry in the last ten seconds, or slips out under swing."},
        {"name": "Bearing blocks between telescoping stages",
         "why": "Metal-on-metal telescopes seize the moment a side load appears mid-climb.",
         "how": ("Rolling bearings or Delrin blocks at both ends of every stage overlap, taking "
                 "load in both directions."),
         "pitfall": "A telescope that extends freely on the bench and jams under the robot's weight."},
        {"name": "One-way bearing power take-off",
         "why": "Sharing one motor between a shooter and a winch saves a motor, a breaker and a channel.",
         "how": ("Winch pulley on a one-way bearing on the shooter shaft: forward free-wheels for "
                 "shooting, reverse locks the bearing and drives the winch."),
         "pitfall": "An unguarded PTO that engages the winch every time the shooter reverses to clear a jam."},
    ],
    "cad": [
        {"name": "Layout sketch first",
         "why": "Every downstream part references it, so changes stay cheap.",
         "how": "One master sketch per subsystem carrying the critical dimensions.",
         "pitfall": "Modelling parts directly and re-cutting them for every revision."},
        {"name": "Subassembly = one rigid body",
         "why": "It makes mates solve and mirrors how the robot is actually assembled.",
         "how": "Rigid-group everything that does not move relative to its neighbours.",
         "pitfall": "Top-level assemblies with hundreds of loose parts."},
        {"name": "Mate the way it bolts together",
         "why": "The assembly order in CAD should be the order on the build table.",
         "how": "Fastened mates for bolted joints, revolute for pivots, slider for stages.",
         "pitfall": "Position-only mates that hide an interference."},
        {"name": "Model to stock, not to shape",
         "why": "A part that is not a stock section or a plate outline cannot be made in a "
                "school shop, and you find that out at fabrication instead of at review.",
         "how": "Every structural member is a catalog section — 2x1x0.100, 1x1, 2x2x0.125 — "
                "cut to a length you can write on a cut list. Everything else is 2D plate.",
         "pitfall": "A 1.375 in square tube that exists in no supplier's catalog."},
        {"name": "One datum per mechanism",
         "why": "Bearings that are located off three different faces never line up, and the "
                "shaft binds no matter how carefully each part was made.",
         "how": "Pick one plate per mechanism as the datum and locate every bore from it; the "
                "opposite plate is a mirrored copy, not an independently dimensioned part.",
         "pitfall": "Dimensioning the second side plate from the first side plate's edge."},
        {"name": "Centre distances are the design, holes are the consequence",
         "why": "Belt and gear centre distances are set by the reduction and the pitch; if you "
                "place the holes first you get a ratio nobody can buy a belt for.",
         "how": "Solve the centre distance from tooth counts and pitch, then place the bores. "
                "For HTD 5 mm, pitch diameter is teeth × 5 / π mm.",
         "pitfall": "Rounding a centre distance to a nice number and tensioning the belt to death."},
        {"name": "Fastener access is geometry",
         "why": "A bolt you cannot get a wrench on is a bolt that never gets torqued, and the "
                "joint it holds is the one that fails at the first hard hit.",
         "how": "Check a 1/4 in hex driver's swept cylinder at every fastener before the design "
                "is closed; move the hole, not the tool.",
         "pitfall": "Discovering at assembly that the gearbox has to come off to reach a rail bolt."},
    ],
}


def techniques_for(subsystems: list[str], *, drive: str = "", turreted: bool = False) -> list[dict[str, str]]:
    """Techniques relevant to this robot: always the fundamentals, plus per-subsystem craft."""
    keys = ["chassis", "electrical", "cad"]
    if drive and "swerve" in drive:
        keys.insert(1, "drivetrain")
    keys.extend(name for name in subsystems if name in TECHNIQUES)
    if turreted:
        keys.append("turret")
    seen: set[str] = set()
    ordered: list[dict[str, str]] = []
    for key in keys:
        for item in TECHNIQUES.get(key, []):
            if item["name"] in seen:
                continue
            seen.add(item["name"])
            ordered.append({**item, "area": key})
    return ordered


def choose_profile(prompt: str, requested_season: str = "") -> tuple[str, dict[str, Any], str]:
    """The profile for this design: the team's selected season, or one inferred from the prompt.

    Returns (key, profile, how it was decided). The third value is carried into the spec so a
    design records *why* it is a 2026 robot instead of leaving it to be argued about later.
    """
    key, _season, reason = resolve_season(prompt, requested_season)
    return key, PROFILES[key], reason


def references_for(subsystems: list[str], *, turreted: bool = False) -> list[dict[str, str]]:
    wanted = set(subsystems) | {"full_robot", "design_guidance", "rules"}
    if turreted:
        wanted.add("shooter")
    return [item for item in REFERENCES if item["kind"] in wanted or any(part in item["kind"] for part in subsystems)]
