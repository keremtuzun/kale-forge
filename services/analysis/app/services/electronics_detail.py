"""Real control-system geometry: every device modelled as its parts, and the harness drawn.

Until now the electrical assembly was one extruded box per device — a PDH was a 8.9×4.6×1.5
brick with a label. That is enough to reserve space and nothing else: you cannot see which
way the CAN port faces, whether the breaker bank is reachable with the bellypan in, or
whether the battery leads have anywhere to go.

Two things live here.

**Device detail.** `device_bodies` turns a catalog key and a placement into the parts the
device is actually made of — case, mounting flanges and their hole pattern, terminal banks,
breaker slots, connectors, status LEDs. Everything is derived from the catalog envelope in
`frc_parts.ELECTRONICS`, so a device stays consistent with the envelope the layout engine
packed against and there is one source of truth for its size.

**The harness.** `harness_cables` draws the wiring from the FRC control-system diagram: the
6 AWG battery loop through the SB50 and the main breaker, one power pair per PDH channel at
the gauge that channel's breaker calls for, the CAN bus daisy-chained through every device
that sits on it, the RSL and radio feeds, and the pneumatics run. Gauges and colours come
from the channel assignments the power analysis already computed, not from a guess.

Sub-parts of one device declare `allow` against their own case, so the geometry audit does
not report a CAN connector as a component buried inside the box it is mounted on. Device
CASES are deliberately left checkable against structure — a roboRIO inside a frame rail is
exactly the fault the audit is supposed to find.
"""
from __future__ import annotations

from typing import Any

from app.services.frc_parts import ELECTRONICS

# Wire colours from the diagram's legend. Red/black is power, yellow/green is CAN, and the
# low-current runs are the ones teams actually mix up, so they are named rather than numbered.
_CAN_COLOURS = ("yellow", "green")
_POWER_COLOURS = ("red", "black")

# Insulated OD in inches for the gauges the control system uses. A 6 AWG battery lead is
# nearly a quarter inch of copper plus jacket and it does not bend like a signal wire — the
# harness reads wrong at a uniform diameter.
_WIRE_OD_IN = {6: 0.36, 8: 0.29, 10: 0.23, 12: 0.19, 14: 0.16, 16: 0.13, 18: 0.11,
               20: 0.09, 22: 0.08}


def wire_od(awg: int) -> float:
    return _WIRE_OD_IN.get(int(awg), 0.12)


def _c(name: str, at: list[float], size: list[float], *, parent: str,
       note: str = "", kind: str = "", rot: list[float] | None = None) -> dict[str, Any]:
    """One sub-part of a device.

    Typed `component` so the viewer and the STEP worker draw it the way they already draw
    electronics, and given an `allow` naming its own case so the geometry audit reads a
    connector sitting proud of the box as the mounting it is, not as a buried part.
    """
    feature: dict[str, Any] = {"t": "component", "n": name, "at": at, "size": size,
                               "allow": [parent]}
    if note:
        feature["note"] = note
    if kind:
        feature["kind"] = kind
    if rot:
        feature["rot"] = rot
    return feature


def _row(name: str, parent: str, origin: list[float], count: int, step: list[float],
         size: list[float], note: str = "") -> list[dict[str, Any]]:
    """A bank of identical contacts — breaker slots, terminal pairs, header pins.

    Emitted as separate bodies rather than one long box because the count is the point: a
    20-channel PDH and a 16-channel PDP look different, and a team counting free channels off
    the model has to be able to count them.
    """
    out = []
    for i in range(count):
        at = [origin[k] + step[k] * i for k in range(3)]
        out.append(_c(f"{name} {i + 1}", at, list(size), parent=parent, note=note if i == 0 else ""))
    return out


def _env(key: str) -> tuple[float, float, float]:
    """Catalog envelope in inches, as (width X, depth Z, height Y) in world orientation."""
    box = (ELECTRONICS.get(key) or {}).get("envelope_in") or [2.0, 2.0, 1.0]
    return float(box[0]), float(box[1]), float(box[2])


# ── per-device detail ────────────────────────────────────────────────────────
def _distributor(key: str, name: str, at: list[float], channels: int) -> list[dict[str, Any]]:
    """PDH or PDP: case, main lugs, the channel breaker bank, CAN and the status LED."""
    w, d, h = _env(key)
    x, y, z = at
    parts = [_c(f"{name} case", [x, y, z], [w, h, d], parent=name,
                note=f"{ELECTRONICS[key]['sku']}; {channels} channels")]
    # Main input lugs: the two studs the 6 AWG battery leads land on, at one end.
    for i, (side, colour) in enumerate(zip((-1, 1), _POWER_COLOURS)):
        parts.append(_c(f"{name} main lug {colour}", [x - w / 2 + 0.55, y + h / 2, z + side * 0.7],
                        [0.5, 0.42, 0.5], parent=name,
                        note="6 AWG battery lead lands here" if i == 0 else ""))
    # The breaker bank. Snap-action ATO breakers stand proud of the case; this is the row a
    # team reads channel numbers off, so it is modelled per channel.
    half = max(1, channels // 2)
    bw, bh, bd = _env("ato_breaker")
    span = w - 1.8
    step = span / max(half - 1, 1)
    for lane, side in enumerate((-1, 1)):
        parts += _row(f"{name} ch breaker {'AB'[lane]}", name,
                      [x - span / 2 + 0.4, y + h / 2 + bd / 2, z + side * (d / 2 - 0.75)],
                      half, [step, 0, 0], [bw, bd, bh],
                      note="snap-action ATO breaker, one per high-current channel")
    parts += [
        _c(f"{name} CAN terminal", [x + w / 2 - 0.5, y + h / 2, z - d / 2 + 0.45],
           [0.55, 0.3, 0.5], parent=name, note="yellow/green CAN pair"),
        _c(f"{name} switch port", [x + w / 2 - 0.5, y + h / 2, z + d / 2 - 0.45],
           [0.55, 0.3, 0.5], parent=name, note="radio / switched low-current output"),
        _c(f"{name} status LED", [x, y + h / 2, z], [0.28, 0.12, 0.28], parent=name),
    ]
    return parts


def _rio(name: str, at: list[float]) -> list[dict[str, Any]]:
    """roboRIO: case, the DIO/PWM and analog header strips, and every port on the two faces."""
    w, d, h = _env("rio")
    x, y, z = at
    parts = [_c(f"{name} case", [x, y, z], [w, h, d], parent=name, note="roboRIO 2.0")]
    # The two header strips down the long faces: 10 PWM/DIO one side, analog and relay the
    # other. These are the connectors every sensor in the diagram lands on.
    parts += _row(f"{name} DIO/PWM header", name,
                  [x - w / 2 + 0.8, y + h / 2, z - d / 2 + 0.28], 10, [0.42, 0, 0],
                  [0.34, 0.22, 0.5], note="PWM 0-9 / DIO, signal-power-ground")
    parts += _row(f"{name} analog header", name,
                  [x - w / 2 + 0.9, y + h / 2, z + d / 2 - 0.28], 4, [0.42, 0, 0],
                  [0.34, 0.22, 0.5], note="analog in 0-3")
    parts += [
        _c(f"{name} power connector", [x - w / 2 + 0.35, y, z + d / 2 - 0.5],
           [0.5, 0.5, 0.6], parent=name, note="dedicated PDH/PDP roboRIO feed, 10 AWG"),
        _c(f"{name} CAN terminal", [x - w / 2 + 0.35, y, z - d / 2 + 0.5],
           [0.5, 0.4, 0.55], parent=name, note="CAN high/low, weidmuller"),
        _c(f"{name} RSL port", [x - w / 2 + 0.35, y, z], [0.45, 0.35, 0.45], parent=name,
           note="Robot Signal Light drive"),
        _c(f"{name} ethernet", [x + w / 2 - 0.4, y, z - 0.55], [0.7, 0.6, 0.65], parent=name,
           note="to the radio"),
        _c(f"{name} USB host", [x + w / 2 - 0.4, y, z + 0.35], [0.6, 0.45, 0.55], parent=name,
           note="USB camera"),
        _c(f"{name} USB device", [x + w / 2 - 0.4, y, z + 1.05], [0.5, 0.4, 0.45], parent=name),
        _c(f"{name} MXP header", [x, y + h / 2, z], [1.9, 0.25, 0.45], parent=name),
    ]
    parts += _mount_holes(name, at, w, d, h, inset=0.35)
    return parts


def _mount_holes(name: str, at: list[float], w: float, d: float, h: float,
                 inset: float = 0.3) -> list[dict[str, Any]]:
    """The four bolts that actually hold the device to the bellypan or its plate.

    Typed `bolts` so they are exempt from interference the way every other fastener is — they
    are supposed to pass through the pan.
    """
    x, y, z = at
    out = []
    for sx in (-1, 1):
        for sz in (-1, 1):
            out.append({"t": "bolts", "n": f"{name} mount {'LR'[sx > 0]}{'FB'[sz > 0]}",
                        "at": [x + sx * (w / 2 - inset), y - h / 2 - 0.2, z + sz * (d / 2 - inset)],
                        "dia": 0.19, "len": 0.6, "rot": [90, 0, 0]})
    return out


def _battery(name: str, at: list[float]) -> list[dict[str, Any]]:
    w, d, h = _env("battery")
    x, y, z = at
    return [
        _c(f"{name} case", [x, y, z], [w, h, d], parent=name, note="12 V 18 Ah SLA"),
        _c(f"{name} terminal +", [x - w / 2 + 0.7, y + h / 2, z - d / 2 + 0.6],
           [0.55, 0.45, 0.55], parent=name, note="6 AWG to the main breaker"),
        _c(f"{name} terminal -", [x + w / 2 - 0.7, y + h / 2, z - d / 2 + 0.6],
           [0.55, 0.45, 0.55], parent=name, note="6 AWG to the distributor"),
        _c(f"{name} carry handle", [x, y + h / 2 + 0.15, z + d / 2 - 0.8],
           [w * 0.5, 0.3, 0.5], parent=name),
    ]


def _main_breaker(name: str, at: list[float]) -> list[dict[str, Any]]:
    w, d, h = _env("main_breaker")
    x, y, z = at
    return [
        _c(f"{name} body", [x, y, z], [w, h, d], parent=name, note="120 A, manually resettable"),
        _c(f"{name} stud in", [x - w / 2 + 0.3, y, z], [0.35, 0.35, 0.35], parent=name),
        _c(f"{name} stud out", [x + w / 2 - 0.3, y, z], [0.35, 0.35, 0.35], parent=name),
        _c(f"{name} reset button", [x, y + h / 2, z], [0.45, 0.22, 0.45], parent=name,
           note="must be reachable without removing the bellypan"),
    ]


def _radio(name: str, at: list[float]) -> list[dict[str, Any]]:
    w, d, h = _env("radio")
    x, y, z = at
    parts = [_c(f"{name} case", [x, y, z], [w, h, d], parent=name, note="VH-109")]
    parts += _row(f"{name} ethernet", name, [x - 0.5, y, z - d / 2 + 0.3], 2, [0.75, 0, 0],
                  [0.66, 0.6, 0.6], note="one to the roboRIO, one spare")
    parts += [
        _c(f"{name} power input", [x + w / 2 - 0.4, y, z - d / 2 + 0.3], [0.45, 0.4, 0.5],
           parent=name, note="passive PoE or PDH switched channel, 18 AWG"),
        _c(f"{name} antenna left", [x - w / 2 + 0.3, y + h / 2 + 0.9, z], [0.16, 1.8, 0.16],
           parent=name),
        _c(f"{name} antenna right", [x + w / 2 - 0.3, y + h / 2 + 0.9, z], [0.16, 1.8, 0.16],
           parent=name),
    ]
    return parts


def _rsl(name: str, at: list[float]) -> list[dict[str, Any]]:
    w, d, h = _env("rsl")
    x, y, z = at
    return [
        _c(f"{name} base", [x, y - h / 4, z], [w * 0.8, h / 2, d * 0.8], parent=name),
        _c(f"{name} lens", [x, y + h / 4, z], [w, h / 2, d], parent=name, kind="lens",
           note="must be visible from outside the robot for inspection"),
        _c(f"{name} mount flange", [x, y - h / 2 - 0.1, z], [w * 1.15, 0.2, d * 1.15],
           parent=name),
    ]


def _pneumatic_hub(name: str, at: list[float]) -> list[dict[str, Any]]:
    w, d, h = _env("ph")
    x, y, z = at
    parts = [_c(f"{name} case", [x, y, z], [w, h, d], parent=name, note="16 solenoid channels")]
    parts += _row(f"{name} solenoid port", name,
                  [x - w / 2 + 0.4, y, z + d / 2 - 0.25], 8, [(w - 0.8) / 7, 0, 0],
                  [0.3, 0.3, 0.45], note="solenoid valve outputs")
    parts += [
        _c(f"{name} CAN terminal", [x + w / 2 - 0.35, y, z - d / 2 + 0.35], [0.45, 0.3, 0.45],
           parent=name),
        _c(f"{name} compressor output", [x - w / 2 + 0.35, y, z - d / 2 + 0.35],
           [0.45, 0.35, 0.45], parent=name, note="12 V compressor feed"),
        _c(f"{name} pressure sensor port", [x, y, z - d / 2 + 0.3], [0.35, 0.3, 0.4],
           parent=name, note="analog pressure transducer, 0-120 psi"),
    ]
    return parts


def _compressor(name: str, at: list[float]) -> list[dict[str, Any]]:
    w, d, h = _env("compressor")
    x, y, z = at
    return [
        _c(f"{name} motor", [x - w * 0.18, y, z], [w * 0.55, h * 0.85, d], parent=name),
        _c(f"{name} head", [x + w * 0.3, y + h * 0.1, z], [w * 0.4, h * 0.7, d * 0.8],
           parent=name),
        _c(f"{name} outlet", [x + w / 2 - 0.2, y + h * 0.3, z], [0.4, 0.4, 0.4], parent=name,
           note="to the pneumatic hub / accumulator"),
        _c(f"{name} foot left", [x - w * 0.2, y - h / 2 - 0.15, z], [w * 0.3, 0.3, d * 1.05],
           parent=name, note="rubber isolators; the compressor is the loudest thing on the robot"),
        _c(f"{name} foot right", [x + w * 0.25, y - h / 2 - 0.15, z], [w * 0.3, 0.3, d * 1.05],
           parent=name),
    ]


def _simple(key: str, name: str, at: list[float], ports: tuple[str, ...] = ()
            ) -> list[dict[str, Any]]:
    """Case plus its named ports, for the small devices — VRM, CANivore, Pigeon, SB50."""
    w, d, h = _env(key)
    x, y, z = at
    parts = [_c(f"{name} case", [x, y, z], [w, h, d], parent=name,
                note=(ELECTRONICS.get(key) or {}).get("sku", ""))]
    for i, port in enumerate(ports):
        # Relative to the case, not absolute — getting this wrong put an SB50's contact eight
        # inches off its own body, where the contact audit correctly reported it as floating.
        offset = min(-d / 2 + 0.3 + (i * 0.5 if len(ports) > 1 else 0.0), d / 2 - 0.25)
        parts.append(_c(f"{name} {port}", [x + w / 2 - 0.3, y, z + offset],
                        [0.42, 0.3, 0.42], parent=name))
    parts += _mount_holes(name, at, w, d, h, inset=0.28)
    return parts


_DETAIL = {
    "rio": lambda name, at, ctx: _rio(name, at),
    "battery": lambda name, at, ctx: _battery(name, at),
    "main_breaker": lambda name, at, ctx: _main_breaker(name, at),
    "radio": lambda name, at, ctx: _radio(name, at),
    "rsl": lambda name, at, ctx: _rsl(name, at),
    "ph": lambda name, at, ctx: _pneumatic_hub(name, at),
    "compressor": lambda name, at, ctx: _compressor(name, at),
    "pdh": lambda name, at, ctx: _distributor("pdh", name, at, ctx.get("channels", 20)),
    "pdp": lambda name, at, ctx: _distributor("pdp", name, at, ctx.get("channels", 16)),
    "pdp2": lambda name, at, ctx: _distributor("pdp2", name, at, ctx.get("channels", 16)),
    "vrm": lambda name, at, ctx: _simple("vrm", name, at, ("12V out", "5V out")),
    "canivore": lambda name, at, ctx: _simple("canivore", name, at, ("USB", "CAN")),
    "pigeon": lambda name, at, ctx: _simple("pigeon", name, at, ("CAN",)),
    "sb50": lambda name, at, ctx: _simple("sb50", name, at, ("contact",)),
    "spark_max": lambda name, at, ctx: _simple("spark_max", name, at, ("CAN", "motor")),
    "spark_flex": lambda name, at, ctx: _simple("spark_flex", name, at, ("CAN", "motor")),
}


def device_bodies(key: str, name: str, at: list[float],
                  context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The parts a device is made of, or one honest box if it is not in the detail library."""
    builder = _DETAIL.get(key)
    if builder is None:
        w, d, h = _env(key)
        return [_c(f"{name} case", list(at), [w, h, d], parent=name)]
    return builder(name, list(at), context or {})


# ── the harness ──────────────────────────────────────────────────────────────
def _cable(name: str, frm: list[float], to: list[float], awg: int, colour: str,
           role: str, note: str = "") -> dict[str, Any]:
    feature: dict[str, Any] = {
        "t": "cable", "n": name, "at": list(frm), "to": list(to),
        "gauge": f"{awg} AWG", "colour": colour, "role": role, "dia": wire_od(awg),
    }
    if note:
        feature["note"] = note
    return feature


def harness_cables(by_key: dict[str, list[float]], assignments: list[dict[str, Any]],
                   motor_points: dict[str, list[float]] | None = None) -> list[dict[str, Any]]:
    """Every wire on the control-system diagram that this robot actually has.

    Power first, then CAN, then the low-current runs. Gauges come from the channel
    assignments the power analysis produced — the same numbers the wire legend on the diagram
    calls out — so a 40 A drive channel gets 10 AWG and a radio feed gets 18.
    """
    out: list[dict[str, Any]] = []
    dist = by_key.get("pdh") or by_key.get("pdp") or by_key.get("pdp2")
    battery, breaker, sb50 = by_key.get("battery"), by_key.get("main_breaker"), by_key.get("sb50")

    # ── main power loop: battery → SB50 → main breaker → distributor ──
    if battery and sb50:
        # Two conductors, two terminals — offset so they are two bodies rather than one line
        # drawn twice, which the CAD contract rejects as coincident and rightly so.
        od = wire_od(6)
        out.append(_cable("battery + to SB50", [battery[0] - od, battery[1], battery[2]],
                          [sb50[0] - od, sb50[1], sb50[2]], 6, "red", "power",
                          "the only unfused conductor on the robot; keep it short"))
        out.append(_cable("battery - to SB50", [battery[0] + od, battery[1], battery[2]],
                          [sb50[0] + od, sb50[1], sb50[2]], 6, "black", "power"))
    if sb50 and breaker:
        out.append(_cable("SB50 to main breaker", sb50, breaker, 6, "red", "power"))
    if breaker and dist:
        out.append(_cable("main breaker to distributor", breaker, dist, 6, "red", "power"))
    if sb50 and dist:
        out.append(_cable("SB50 - to distributor", sb50, dist, 6, "black", "power"))
    elif battery and dist:
        out.append(_cable("battery - to distributor", battery, dist, 6, "black", "power"))

    # ── one power pair per high-current channel, at that breaker's gauge ──
    #
    # Each wire leaves its OWN terminal. Channel N sits at a different position along the
    # breaker bank than channel N+1, and the + and − of a pair land on adjacent terminals —
    # so the runs are spread across the distributor face rather than all starting from its
    # centre. Without that they are coincident bodies and the CAD contract rejects the tree,
    # which is the contract being right: two conductors cannot occupy one line.
    if dist and motor_points:
        channels = [e for e in assignments if motor_points.get(str(e.get("load")))]
        span = 6.0
        step = span / max(len(channels) - 1, 1)
        for i, entry in enumerate(channels):
            point = motor_points[str(entry.get("load"))]
            awg = int(entry.get("wire_awg") or 12)
            channel = entry.get("channel")
            od = wire_od(awg)
            lane = -span / 2 + step * i
            plus = [dist[0] + lane, dist[1], dist[2] - od]
            minus = [dist[0] + lane, dist[1], dist[2] + od]
            end_plus = [point[0], point[1], point[2] - od]
            end_minus = [point[0], point[1], point[2] + od]
            out.append(_cable(f"ch{channel} + to {entry.get('load')}", plus, end_plus, awg,
                              "red", "power",
                              f"{entry.get('breaker_a')} A breaker, {entry.get('subsystem')}"))
            out.append(_cable(f"ch{channel} - from {entry.get('load')}", end_minus, minus, awg,
                              "black", "power"))

    # ── the CAN bus, daisy-chained in the order the diagram runs it ──
    chain = [k for k in ("pdh", "pdp", "pdp2", "rio", "canivore", "pigeon", "ph")
             if by_key.get(k)]
    for i in range(len(chain) - 1):
        a, b = chain[i], chain[i + 1]
        out.append(_cable(f"CAN {a} to {b}", by_key[a], by_key[b], 20, _CAN_COLOURS[0], "can",
                          "yellow/green twisted pair; the bus is terminated at both ends"
                          if i == 0 else ""))

    # ── low-current: the roboRIO feed, the radio, the RSL ──
    if dist and by_key.get("rio"):
        out.append(_cable("distributor to roboRIO", dist, by_key["rio"], 10, "red", "power",
                          "dedicated roboRIO connector, never a shared channel"))
    if by_key.get("rio") and by_key.get("rsl"):
        out.append(_cable("roboRIO to RSL", by_key["rio"], by_key["rsl"], 18, "white", "signal",
                          "the light has to flash for the robot to pass inspection"))
    if by_key.get("radio"):
        source = by_key.get("vrm") or dist
        if source:
            out.append(_cable("radio power", source, by_key["radio"], 18, "red", "power",
                              "switched/regulated 12 V — a browned-out radio loses the match"))
        if by_key.get("rio"):
            out.append(_cable("roboRIO to radio ethernet", by_key["rio"], by_key["radio"],
                              22, "blue", "ethernet"))

    # ── pneumatics ──
    if by_key.get("ph") and by_key.get("compressor"):
        out.append(_cable("hub to compressor", by_key["ph"], by_key["compressor"], 14, "red",
                          "power", "compressor runs off the hub, closed-loop on the switch"))
    if dist and by_key.get("ph"):
        out.append(_cable("distributor to pneumatic hub", dist, by_key["ph"], 12, "red", "power"))
    return out
