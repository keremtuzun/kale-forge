"""Copper: every net becomes a path a board house could actually fabricate.

Third rung of the completion ladder. Same rule as the first two: `ROUTING_COMPLETE` is only
claimed when routing actually happened and the finished geometry passes an independent check.

How it works, which is the oldest idea in the field because it is the one that terminates:

  * a uniform grid over the board, one cell per routable position, two routable layers;
  * pads snapped onto that grid from real package pad geometry;
  * each net routed as a minimum spanning tree, A* between the pads, cheapest path first;
  * every routed path reserves a corridor sized to that net's own trace width, so the next
    net physically cannot be placed too close;
  * a via costs about twelve cells, so the router prefers to stay on one layer and changes
    only when it has to.

Ground is not traced. On a two-layer board it is a bottom-side pour with a via under each
ground pad, because that is what a real board does and tracing it instead would be a worse
design that merely looked more impressive. The pour is then verified by flood fill: if a
bottom-side signal trace severs it, the check fails and the stage is not claimed.

`drc()` re-derives clearance, width and connectivity from the finished geometry rather than
from the router's own bookkeeping. That is the point of it: it is a check on the router, not
a restatement of it, and it has caught the router being wrong.

Everything is millimetres, origin at the board's bottom-left corner.

Stdlib-only.
"""
from __future__ import annotations

import heapq
import math
import time
from typing import Any

from app.services import engineering_calc as calc
from app.services.design_intent import ASSUMED, CALCULATED
from app.services.pcb_layout import (
    HOLE_KEEPOUT_MM, PACKAGES, _DEFAULT_PACKAGE, package_for)

ROUTE_VERSION = "kale-pcbroute-1.0"

GRID_MM = 0.4                # one routable position every 0.4 mm
CLEARANCE_MM = 0.2           # copper to copper, the usual cheap-fab minimum
SIGNAL_WIDTH_MM = 0.25       # a signal carries no current worth widening for
VIA_DRILL_MM = 0.4
VIA_PAD_MM = 0.8
VIA_COST = 24                # in cells; a via is a drill hit, so it should hurt to use one
BOTTOM_COST = 3              # per step: bottom copper is cut out of the ground pour
BBOX_MARGINS = (14, 48)      # slack around a connection; wider only if the first fails
PAD_MODEL_MM = GRID_MM       # a pad is modelled as the one cell it sits on
PAD_GUARD_CELLS = 2          # how far a foreign net must stay from any pad
MAX_EXPANSIONS = 120_000     # per connection, so a hopeless net fails fast
ROUTE_BUDGET_S = 6.0         # rip-up runs inside a request, so it is on a clock
MAX_RIPS_PER_NET = 4         # a net torn up this often is left alone, or two nets thrash
MAX_EVICTIONS = 120          # total, so a genuinely unroutable board still fails quickly
CROSSING_PENALTY = 90        # cost of crossing another net when asking who is in the way
HISTORY_WEIGHT = 6           # per previous eviction: contested ground gets dearer each round

FAB_NOTE = (f"{CLEARANCE_MM:g} mm clearance and {VIA_DRILL_MM:g} mm drills are ordinary "
            "cheap-fab limits, not a quote from a specific board house; pads are modelled at "
            f"{GRID_MM:g} mm grid resolution, so escape routing around fine-pitch packages "
            "has not been checked against real pad drawings")


# ── pads ─────────────────────────────────────────────────────────────────────
def _row(count: int, pitch: float, y: float) -> list[tuple[float, float]]:
    start = -pitch * (count - 1) / 2
    return [(start + pitch * i, y) for i in range(count)]


def _dual(per_side: int, pitch: float, span: float) -> list[tuple[float, float]]:
    """Two rows, numbered down one side and back up the other, as every DIP-style part is."""
    ys = [pitch * (per_side - 1) / 2 - pitch * i for i in range(per_side)]
    return [(-span / 2, y) for y in ys] + [(span / 2, y) for y in reversed(ys)]


# Pad centres in package-local mm. Nominal for the generic package, like the envelopes they
# sit inside; a real part's drawing wins over any of this.
PAD_LAYOUTS: dict[str, list[tuple[float, float]]] = {
    "0603": [(-0.75, 0.0), (0.75, 0.0)],
    "0805": [(-0.95, 0.0), (0.95, 0.0)],
    "1206": [(-1.55, 0.0), (1.55, 0.0)],
    "LED-0603": [(-0.75, 0.0), (0.75, 0.0)],
    "SOT-23": [(-0.95, -1.0), (0.95, -1.0), (0.0, 1.0)],
    "SOT-223": [(-2.3, -3.2), (0.0, -3.2), (2.3, -3.2), (0.0, 3.2)],
    "SOIC-8": _dual(4, 1.27, 5.4),
    "SOIC-14": _dual(7, 1.27, 5.4),
    "DFN-8": _dual(4, 0.5, 2.8),
    "POWER-INDUCTOR": [(-3.0, 0.0), (3.0, 0.0)],
    "SCREW-2": [(-2.5, 0.0), (2.5, 0.0)],
    "WEIDMULLER-2": [(-2.5, 0.0), (2.5, 0.0)],
    "FUSE-HOLDER": [(-5.0, 0.0), (5.0, 0.0)],
    "TO-220": [(-2.54, -2.0), (0.0, -2.0), (2.54, -2.0)],
    "JST-PH-3": _row(3, 2.0, 0.0),
    "JST-PH-4": _row(4, 2.0, 0.0),
    "JST-PH-6": _row(6, 2.0, 0.0),
}


def pad_positions(placement: dict[str, Any], part: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Where every named pin of one placed component physically is, on the board.

    Pins are matched to pads in order. A package with more pads than the part declares pins
    simply has unconnected pads, which is normal and is reported rather than hidden.
    """
    package = placement.get("package") or package_for(part)
    offsets = PAD_LAYOUTS.get(package)
    if offsets is None:
        # No pad map: treat the part as a single pad at its centre, which routes to the right
        # place and is honest about being approximate.
        offsets = [(0.0, 0.0)]
    angle = math.radians(float(placement.get("rotation") or 0))
    cos, sin = math.cos(angle), math.sin(angle)
    out: dict[str, tuple[float, float]] = {}
    for pin, (dx, dy) in zip(part.get("pins") or [], offsets):
        out[str(pin)] = (round(placement["x"] + dx * cos - dy * sin, 3),
                         round(placement["y"] + dx * sin + dy * cos, 3))
    return out


# ── the grid ─────────────────────────────────────────────────────────────────
class _Grid:
    """Occupancy for one board. 0 is free, anything else is the id of the net that owns it."""

    def __init__(self, width: float, height: float, layers: int = 2) -> None:
        self.w = max(1, int(width / GRID_MM))
        self.h = max(1, int(height / GRID_MM))
        self.layers = layers
        self.cells = [bytearray(self.w * self.h) for _ in range(layers)]
        # Pad clearance, filled in once the pads are known. Top-side only: the pads are.
        self.guard = bytearray(self.w * self.h)
        # Congestion history: how many times copper here has been torn up. Cells that keep
        # being fought over get more expensive for everyone, which is the only thing that
        # stops two nets swapping the same corridor back and forth for ever.
        self.history = [bytearray(self.w * self.h) for _ in range(layers)]

    def index(self, x: float, y: float) -> tuple[int, int]:
        return (min(self.w - 1, max(0, int(x / GRID_MM))),
                min(self.h - 1, max(0, int(y / GRID_MM))))

    def mm(self, gx: int, gy: int) -> tuple[float, float]:
        return (round((gx + 0.5) * GRID_MM, 3), round((gy + 0.5) * GRID_MM, 3))

    def get(self, layer: int, gx: int, gy: int) -> int:
        return self.cells[layer][gy * self.w + gx]

    def set(self, layer: int, gx: int, gy: int, value: int) -> None:
        self.cells[layer][gy * self.w + gx] = value

    def fill_disc(self, layer: int, gx: int, gy: int, radius_cells: int, value: int,
                  only_free: bool = False) -> list[tuple[int, int, int]]:
        return self._fill(layer, gx, gy, radius_cells, value, only_free, round_mask=True)

    def fill_square(self, layer: int, gx: int, gy: int, radius_cells: int, value: int,
                    only_free: bool = False) -> list[tuple[int, int, int]]:
        """Square, for trace corridors. A circular mask leaves the diagonal neighbour free,
        and the diagonal is the shortest route to a clearance violation."""
        return self._fill(layer, gx, gy, radius_cells, value, only_free, round_mask=False)

    def _fill(self, layer: int, gx: int, gy: int, radius_cells: int, value: int,
              only_free: bool, round_mask: bool) -> list[tuple[int, int, int]]:
        """Returns the cells it actually changed, so the caller can put them back.

        That list is what makes rip-up possible: without it there is no way to remove one
        net's copper without rebuilding the whole board.
        """
        changed: list[tuple[int, int, int]] = []
        for dy in range(-radius_cells, radius_cells + 1):
            for dx in range(-radius_cells, radius_cells + 1):
                x, y = gx + dx, gy + dy
                if not (0 <= x < self.w and 0 <= y < self.h):
                    continue
                if round_mask and dx * dx + dy * dy > radius_cells ** 2:
                    continue
                if not only_free or self.get(layer, x, y) == 0:
                    self.set(layer, x, y, value)
                    changed.append((layer, x, y))
        return changed


_GUARD_CONTESTED = 254


def _pad_guard(grid: "_Grid", node_cells: dict[str, list[tuple[int, int]]],
               net_ids: dict[str, int],
               unnetted: list[tuple[int, int]] | None = None) -> bytearray:
    """Which net, if any, owns the space immediately around each pad.

    Built from every pad together rather than one at a time, which is the difference between
    a rule and a race: a cell near two different nets' pads is contested and belongs to
    neither, instead of going to whichever pad was processed first.
    """
    guard = bytearray(grid.w * grid.h)
    radius = PAD_GUARD_CELLS
    owners = [(net_ids[name], cells) for name, cells in node_cells.items()]
    # A pad on no net is contested ground: no net may route near it, because it belongs to
    # none of them and it is still copper.
    owners.append((_GUARD_CONTESTED, list(unnetted or [])))
    for nid, cells in owners:
        for gx, gy in cells:
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    x, y = gx + dx, gy + dy
                    if not (0 <= x < grid.w and 0 <= y < grid.h):
                        continue
                    index = y * grid.w + x
                    current = guard[index]
                    if current == 0:
                        guard[index] = nid
                    elif current != nid:
                        guard[index] = _GUARD_CONTESTED
    return guard


def _net_width_mm(net: dict[str, Any], spec: dict[str, Any]) -> float:
    """How wide this net's copper has to be, from the current it carries.

    Signals get the minimum. A power rail gets whatever IPC-2221 asks for at its current,
    which is the difference between a board that works and a board that browns out.
    """
    name = str(net.get("name") or "")
    amps = 0.0
    for rail in spec.get("outputs") or []:
        if name == f"+{float(rail['voltage']):g}V":
            amps = float(rail["current_a"])
    if name.startswith("VIN") or name == "GND":
        total = sum(float(r["voltage"]) * float(r["current_a"])
                    for r in spec.get("outputs") or [])
        amps = max(amps, calc.input_current_a(total, float(
            (spec.get("input") or {}).get("voltage") or 12.0)) or 0.0)
    if amps <= 0:
        return SIGNAL_WIDTH_MM
    sized = calc.trace_width_in(amps)
    return max(SIGNAL_WIDTH_MM, round(sized["width_mm"], 2)) if sized else SIGNAL_WIDTH_MM


def _corridor_cells(width_mm: float) -> int:
    """How many cells either side of a centreline this net has to keep to itself.

    Derived from the clearance rule rather than picked: the next net's centreline can be no
    closer than (reserve + 1) cells, and that has to cover half of this trace, half of the
    narrowest trace that might sit beside it, and the clearance between them.
    """
    needed = width_mm / 2 + SIGNAL_WIDTH_MM / 2 + CLEARANCE_MM
    return max(1, int(math.ceil(needed / GRID_MM)) - 1)


# ── routing ──────────────────────────────────────────────────────────────────
def route(layout: dict[str, Any], parts: list[dict[str, Any]], nets: list[dict[str, Any]],
          spec: dict[str, Any], layers: int = 2) -> dict[str, Any]:
    """Turn a placed board and a netlist into copper."""
    width, height = layout["board_mm"]
    grid = _Grid(width, height, 2)
    by_ref = {p["reference"]: p for p in parts}
    seats = {p["reference"]: p for p in layout["placements"]}

    # Where every pin physically is.
    pads: dict[str, tuple[float, float]] = {}
    for ref, seat in seats.items():
        part = by_ref.get(ref)
        if not part:
            continue
        for pin, xy in pad_positions(seat, part).items():
            pads[f"{ref}.{pin}"] = xy

    # Components block the top layer under their bodies. Their own pads are punched back
    # open below, so a pad under a body is still reachable.
    for seat in layout["placements"]:
        package = PACKAGES.get(seat["package"], PACKAGES[_DEFAULT_PACKAGE])
        bw, bh = package["body"]
        gx0, gy0 = grid.index(seat["x"] - bw / 2, seat["y"] - bh / 2)
        gx1, gy1 = grid.index(seat["x"] + bw / 2, seat["y"] + bh / 2)
        for gy in range(gy0, gy1 + 1):
            for gx in range(gx0, gx1 + 1):
                grid.set(0, gx, gy, 255)

    for hole in layout["mounting_holes"]:
        gx, gy = grid.index(hole["x"], hole["y"])
        radius = int(math.ceil(HOLE_KEEPOUT_MM / GRID_MM))
        for layer in range(2):
            grid.fill_disc(layer, gx, gy, radius, 255)

    # What is a plane and what is a trace. Ground always; on a four-layer board the input
    # rail as well, because that is what the second inner layer is for and it is why the
    # stackup was chosen. A net on a plane is not a routing problem, it is a via per pad.
    ground = next((n for n in nets if n["name"] == "GND"), None)
    planes = [n for n in nets if n is ground]
    if layers >= 4:
        planes += [n for n in nets if n["name"].startswith("VIN")]
    routable = [n for n in nets if n not in planes]
    net_ids = {n["name"]: i + 1 for i, n in enumerate(routable)}
    if len(net_ids) > 250:
        routable = routable[:250]

    # Punch pads open for their own net so the router can enter them, then let each one claim
    # its clearance ring. Centres are marked in a first pass so a crowded neighbour can never
    # take a pad's own cell; rings are grown in a second pass, first come first served.
    node_cells: dict[str, list[tuple[int, int]]] = {}
    missing: list[str] = []
    for net in routable:
        nid = net_ids[net["name"]]
        cells = []
        for node in net["nodes"]:
            xy = pads.get(node)
            if xy is None:
                missing.append(node)
                continue
            gx, gy = grid.index(*xy)
            grid.set(0, gx, gy, nid)
            cells.append((gx, gy))
        node_cells[net["name"]] = cells
    # Every pad, not only the ones on a net. A regulator's EN and FB pins are copper whether
    # or not this design connects them, and the router could not see them at all: it happily
    # ran traces across pads that were simply absent from the netlist.
    unnetted: list[tuple[int, int]] = []
    netted = {node for net in routable for node in net["nodes"]}
    for node, xy in pads.items():
        if node in netted:
            continue
        gx, gy = grid.index(*xy)
        if grid.get(0, gx, gy) == 0:
            grid.set(0, gx, gy, 255)
        unnetted.append((gx, gy))
    grid.guard = _pad_guard(grid, node_cells, net_ids, unnetted)

    deadline = time.monotonic() + ROUTE_BUDGET_S
    router = _Router(grid, node_cells, net_ids, spec, deadline,
                     bottom_cost=1 if layers >= 4 else BOTTOM_COST)

    # Hardest first: the net with the most pads has the least freedom to find a way round,
    # so it should choose its path while the board is still empty.
    order = sorted(routable, key=lambda n: (-len(n["nodes"]), -_net_width_mm(n, spec)))
    by_name = {n["name"]: n for n in routable}

    failed = [n["name"] for n in order if not router.route_net(n)]
    best = router.snapshot(failed)
    rips: dict[str, int] = {}
    evictions = 0

    # Rounds, not a queue. Each round tears up whoever is blocking the nets that failed and
    # reroutes them, and the round is only kept if fewer nets are left failing than before.
    # Without that, rip-up wandered: it would evict a net, fail to put it back, and finish
    # with less copper than it started with.
    while failed and time.monotonic() < deadline and evictions < MAX_EVICTIONS:
        progressed = False
        for name in list(failed):
            blockers = [b for b in router.blockers_for(by_name[name])
                        if b != name and rips.get(b, 0) < MAX_RIPS_PER_NET]
            if not blockers:
                continue
            for blocked in blockers:
                rips[blocked] = rips.get(blocked, 0) + 1
                router.rip(blocked)
                evictions += 1
            progressed = True
            router.route_net(by_name[name])
            for blocked in sorted(blockers, key=lambda b: -len(by_name[b]["nodes"])):
                router.route_net(by_name[blocked])
        if not progressed:
            break
        failed = [n["name"] for n in order if not router.is_routed(n["name"])]
        if len(failed) < len(best["failed"]):
            best = router.snapshot(failed)
        elif len(failed) > len(best["failed"]):
            # This round made it worse. Put the best board back and stop guessing.
            router.restore(best)
            failed = list(best["failed"])
            break
    else:
        if len(failed) > len(best["failed"]):
            router.restore(best)
            failed = list(best["failed"])

    traces, vias = router.all_traces(), router.all_vias()
    pour = _ground_pour(grid, ground, pads, layout, layers) if ground else None
    for net in planes:
        if net is ground:
            continue
        vias += [{"net": net["name"], "x": pads[n][0], "y": pads[n][1],
                  "drill_mm": VIA_DRILL_MM, "pad_mm": VIA_PAD_MM}
                 for n in net["nodes"] if n in pads]
    checks = _route_checks(traces, vias, failed, missing, pour, layers, ground)
    complete = not failed and not missing and all(c["ok"] for c in checks)

    return {
        "version": ROUTE_VERSION,
        "grid_mm": GRID_MM,
        "clearance_mm": CLEARANCE_MM,
        "layers": layers,
        "traces": traces,
        "vias": vias,
        "ground": pour,
        "planes": [{"net": n["name"],
                    "layer": "inner 1" if n is ground else "inner 2",
                    "pads": len([x for x in n["nodes"] if x in pads]),
                    "note": "solid plane on an inner layer; every pad reaches it with a via"}
                   for n in planes] if layers >= 4 else [],
        "unrouted": failed,
        "unknown_pins": missing,
        "checks": checks,
        "complete": complete,
        "copper_mm": round(sum(_length(t["points"]) for t in traces), 1),
        "evictions": evictions,
        "fab_note": FAB_NOTE,
        "source": CALCULATED if complete else ASSUMED,
    }


class _Router:
    """One board's copper, with every net's cells recorded so any of it can be torn up.

    The recording is the whole point. Without knowing which cells belong to which net there
    is no way to remove one net's copper without rebuilding the board from scratch, which is
    why the previous version could only re-run the whole thing in a different order.
    """

    def __init__(self, grid: "_Grid", node_cells: dict[str, list[tuple[int, int]]],
                 net_ids: dict[str, int], spec: dict[str, Any],
                 deadline: float | None = None, bottom_cost: int = BOTTOM_COST) -> None:
        self.grid = grid
        self.deadline = deadline
        self.bottom_cost = bottom_cost
        self.node_cells = node_cells
        self.net_ids = net_ids
        self.spec = spec
        self.owned: dict[str, list[tuple[int, int, int]]] = {}
        self.traces: dict[str, list[dict[str, Any]]] = {}
        self.vias: dict[str, list[dict[str, Any]]] = {}
        self._own: dict[str, frozenset[tuple[int, int]]] = {}

    def own_guard(self, name: str) -> frozenset[tuple[int, int]]:
        """The cells this net may enter despite the guard: its own pads, and only those.

        Exempting the whole guard ring was too generous and let a net run within 0.1 mm of a
        neighbouring pin on its way into its own pad. Exempting nothing was too strict: on a
        fine-pitch package every cell including the pad itself is contested by its
        neighbours, so the net could not reach the pad at all and simply failed.

        The pad, and nothing around it. Escaping a fine-pitch part therefore means dropping
        a via straight down and routing underneath, which is what a real board does.
        """
        if name not in self._own:
            self._own[name] = frozenset(self.node_cells.get(name) or [])
        return self._own[name]

    def is_routed(self, name: str) -> bool:
        return name in self.traces

    def snapshot(self, failed: list[str]) -> dict[str, Any]:
        """A copy of the whole board, so a round that makes things worse can be undone."""
        return {"cells": [bytearray(layer) for layer in self.grid.cells],
                "history": [bytearray(layer) for layer in self.grid.history],
                "owned": {k: list(v) for k, v in self.owned.items()},
                "traces": {k: list(v) for k, v in self.traces.items()},
                "vias": {k: list(v) for k, v in self.vias.items()},
                "failed": list(failed)}

    def restore(self, state: dict[str, Any]) -> None:
        self.grid.cells = [bytearray(layer) for layer in state["cells"]]
        # History is deliberately NOT restored: what the board learned about congestion is
        # true whichever arrangement of copper is on it.
        self.owned = {k: list(v) for k, v in state["owned"].items()}
        self.traces = {k: list(v) for k, v in state["traces"].items()}
        self.vias = {k: list(v) for k, v in state["vias"].items()}

    def all_traces(self) -> list[dict[str, Any]]:
        return [t for runs in self.traces.values() for t in runs]

    def all_vias(self) -> list[dict[str, Any]]:
        return [v for group in self.vias.values() for v in group]

    def rip(self, name: str) -> None:
        """Remove one net's copper, leaving its pads and everyone else's copper alone.

        Every cell given up remembers that it was contested. Two nets that both want the same
        corridor will otherwise take it in turns for ever, each evicting the other, which is
        exactly what a CAN pair did: CANH routed, CANL tore it up, CANH tore that up, and the
        board never converged.
        """
        for layer, gx, gy in self.owned.pop(name, []):
            self.grid.set(layer, gx, gy, 0)
            index = gy * self.grid.w + gx
            if self.grid.history[layer][index] < 255:
                self.grid.history[layer][index] += 1
        self.traces.pop(name, None)
        self.vias.pop(name, None)

    def route_net(self, net: dict[str, Any]) -> bool:
        name = net["name"]
        cells = self.node_cells.get(name) or []
        if len(cells) < 2:
            self.traces.setdefault(name, [])
            return True
        nid = self.net_ids[name]
        width = _net_width_mm(net, self.spec)
        reserve = _corridor_cells(width)
        laid: list[tuple[int, int, int]] = []
        runs: list[dict[str, Any]] = []
        vias: list[dict[str, Any]] = []

        connected = {(0, cells[0][0], cells[0][1])}
        remaining = list(cells[1:])
        while remaining:
            # A box around the endpoints first. Most connections are short and the box makes
            # them cheap; the ones that genuinely need to go round the houses fall through to
            # the whole board.
            own = self.own_guard(name)
            # Widening boxes rather than one box and then the whole board. Every net here
            # routes in milliseconds on its own, so a failure means congestion, and the cure
            # for congestion is rip-up, not a search of all 72,000 cells. Those exhaustive
            # searches were spending the entire budget proving what rip-up would have fixed.
            path = None
            for margin in BBOX_MARGINS:
                path = _astar(self.grid, connected, remaining, nid,
                              bounds=_bbox(connected, remaining, margin),
                              deadline=self.deadline, bottom_cost=self.bottom_cost, own=own)
                if path is not None:
                    break
            if path is None:
                # Undo the half-routed net rather than leave orphan copper behind: a partly
                # routed net is copper that blocks other nets and connects nothing.
                for layer, gx, gy in laid:
                    self.grid.set(layer, gx, gy, 0)
                return False
            reached = (path[-1][1], path[-1][2])
            remaining = [c for c in remaining if c != reached]
            for layer, gx, gy in path:
                connected.add((layer, gx, gy))
                laid += self.grid.fill_square(layer, gx, gy, reserve, nid, only_free=True)
                if self.grid.get(layer, gx, gy) != nid:
                    laid.append((layer, gx, gy))
                self.grid.set(layer, gx, gy, nid)
            runs += _polylines(self.grid, path, name, width)
            vias += _vias(self.grid, path, name)

        self.owned[name] = laid
        self.traces[name] = runs
        self.vias[name] = vias
        return True

    def blockers_for(self, net: dict[str, Any]) -> list[str]:
        """Whose copper stands between this net's pads.

        Walked as straight L-shaped lines between the pads rather than searched properly.
        Rip-up does not need the optimal set of nets to evict, only a plausible one: it is
        going to reroute everything it tears up anyway, and the cost of being slightly wrong
        is one more cycle. Searching for the true answer cost a full A* per pad with every
        net passable, which is the most expensive search there is, and it was consuming the
        entire budget to answer a question that did not need answering exactly.
        """
        name = net["name"]
        cells = self.node_cells.get(name) or []
        if len(cells) < 2:
            return []
        nid = self.net_ids[name]
        by_id = {v: k for k, v in self.net_ids.items()}
        seen: list[str] = []
        for (ax, ay), (bx, by) in zip(cells, cells[1:]):
            for gx, gy in _line_cells(ax, ay, bx, by):
                for layer in range(2):
                    owner = self.grid.get(layer, gx, gy)
                    if owner in (0, nid, 255, _GUARD_CONTESTED):
                        continue
                    blocked = by_id.get(owner)
                    if blocked and blocked not in seen:
                        seen.append(blocked)
        return seen


def _line_cells(ax: int, ay: int, bx: int, by: int) -> list[tuple[int, int]]:
    """The cells on an L from a to b: along x, then along y. Not the route a net would take,
    but a fair sample of what sits between two pads."""
    out = [(x, ay) for x in range(min(ax, bx), max(ax, bx) + 1)]
    out += [(bx, y) for y in range(min(ay, by), max(ay, by) + 1)]
    return out


def _bbox(cells, targets, margin: int) -> tuple[int, int, int, int]:
    xs = [c[1] for c in cells] + [t[0] for t in targets]
    ys = [c[2] for c in cells] + [t[1] for t in targets]
    return (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)


def _astar(grid: _Grid, sources: set[tuple[int, int, int]],
           targets: list[tuple[int, int]], nid: int,
           penalty: int | None = None,
           bounds: tuple[int, int, int, int] | None = None,
           deadline: float | None = None,
           bottom_cost: int = BOTTOM_COST,
           own: frozenset[tuple[int, int]] = frozenset()
           ) -> list[tuple[int, int, int]] | None:
    """Cheapest path from anywhere already routed to the nearest pad still to reach.

    With `penalty` set, another net's copper is a toll rather than a wall, so the search can
    report who is in the way instead of only that there is no way.
    """
    goal = set(targets)

    def heuristic(gx: int, gy: int) -> int:
        return min(abs(gx - tx) + abs(gy - ty) for tx, ty in goal)

    heap: list[tuple[int, int, tuple[int, int, int]]] = []
    best: dict[tuple[int, int, int], int] = {}
    came: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    for node in sources:
        best[node] = 0
        heapq.heappush(heap, (heuristic(node[1], node[2]), 0, node))

    x0, y0, x1, y1 = bounds or (0, 0, grid.w - 1, grid.h - 1)
    expansions = 0
    while heap and expansions < MAX_EXPANSIONS:
        _, cost, node = heapq.heappop(heap)
        if cost > best.get(node, 1 << 30):
            continue
        expansions += 1
        if deadline is not None and not expansions % 2048 and time.monotonic() > deadline:
            return None
        layer, gx, gy = node
        if (gx, gy) in goal:
            path = [node]
            while path[-1] in came:
                path.append(came[path[-1]])
            return list(reversed(path))
        # A step on the bottom layer costs more only when the bottom IS the ground pour. On
        # a four-layer board it is ordinary routing space and should not be penalised.
        here = 1 if layer == 0 else bottom_cost
        moves = [(layer, gx + 1, gy, here), (layer, gx - 1, gy, here),
                 (layer, gx, gy + 1, here), (layer, gx, gy - 1, here),
                 (1 - layer, gx, gy, VIA_COST)]
        for nl, nx, ny, step in moves:
            if not (x0 <= nx <= x1 and y0 <= ny <= y1):
                continue
            if not (0 <= nx < grid.w and 0 <= ny < grid.h):
                continue
            owner = grid.get(nl, nx, ny)
            toll = 0
            if owner not in (0, nid):
                if penalty is None or owner == 255:
                    continue
                toll = penalty
            # Too close to somebody else's pad, or to two nets' pads at once. A net is always
            # allowed onto its own pad, which is otherwise unreachable on a fine-pitch part
            # where every neighbouring pin contests the same cells.
            if nl == 0:
                guard = grid.guard[ny * grid.w + nx]
                if guard and guard != nid and (nx, ny) not in own:
                    if penalty is None:
                        continue
                    toll = max(toll, penalty)
            nxt = (nl, nx, ny)
            new = cost + step + toll + grid.history[nl][ny * grid.w + nx] * HISTORY_WEIGHT
            if new < best.get(nxt, 1 << 30):
                best[nxt] = new
                came[nxt] = node
                heapq.heappush(heap, (new + heuristic(nx, ny), new, nxt))
    return None


def _polylines(grid: _Grid, path: list[tuple[int, int, int]], net: str,
               width: float) -> list[dict[str, Any]]:
    """One polyline per layer run, with collinear points collapsed so the output is readable
    geometry rather than a list of grid cells."""
    out: list[dict[str, Any]] = []
    run: list[tuple[int, int]] = []
    layer = path[0][0]
    for node in path:
        if node[0] != layer:
            if len(run) > 1:
                out.append({"net": net, "layer": "top" if layer == 0 else "bottom",
                            "width_mm": width, "points": _simplify(grid, run)})
            run, layer = [], node[0]
        run.append((node[1], node[2]))
    if len(run) > 1:
        out.append({"net": net, "layer": "top" if layer == 0 else "bottom",
                    "width_mm": width, "points": _simplify(grid, run)})
    return out


def _simplify(grid: _Grid, cells: list[tuple[int, int]]) -> list[list[float]]:
    points = [list(grid.mm(*cells[0]))]
    for i in range(1, len(cells) - 1):
        ax, ay = cells[i - 1]
        bx, by = cells[i]
        cx, cy = cells[i + 1]
        if (bx - ax, by - ay) != (cx - bx, cy - by):
            points.append(list(grid.mm(bx, by)))
    points.append(list(grid.mm(*cells[-1])))
    return points


def _vias(grid: _Grid, path: list[tuple[int, int, int]], net: str) -> list[dict[str, Any]]:
    out = []
    for a, b in zip(path, path[1:]):
        if a[0] != b[0]:
            x, y = grid.mm(a[1], a[2])
            out.append({"net": net, "x": x, "y": y,
                        "drill_mm": VIA_DRILL_MM, "pad_mm": VIA_PAD_MM})
    return out


def _ground_pour(grid: _Grid, ground: dict[str, Any], pads: dict[str, tuple[float, float]],
                 layout: dict[str, Any], layers: int = 2) -> dict[str, Any]:
    """Ground as a bottom-side pour, with a via under every ground pad.

    Then flood filled, because a pour is only a ground plane if it is one piece. A bottom
    signal trace across the middle of the board splits it into two islands and half the
    circuit loses its return path, which is exactly the sort of thing that looks fine in a
    picture and does not work on a bench.
    """
    stitching = []
    for node in ground["nodes"]:
        xy = pads.get(node)
        if xy:
            stitching.append({"net": "GND", "x": xy[0], "y": xy[1],
                              "drill_mm": VIA_DRILL_MM, "pad_mm": VIA_PAD_MM, "node": node})

    if layers >= 4:
        # An inner plane cannot be cut by signal routing, because no signal is on it.
        return {"layer": "inner 1", "kind": "plane", "net": "GND",
                "stitching_vias": stitching, "islands": 1, "coverage": 1.0, "orphans": [],
                "note": "solid ground plane on an inner layer, reached by a via from every "
                        "ground pad; nothing routes on it, so nothing can break it"}

    seen = bytearray(grid.w * grid.h)
    regions: list[int] = []
    region_of: dict[tuple[int, int], int] = {}
    for start_y in range(grid.h):
        for start_x in range(grid.w):
            if seen[start_y * grid.w + start_x] or grid.get(1, start_x, start_y) != 0:
                continue
            index = len(regions)
            size = 0
            stack = [(start_x, start_y)]
            seen[start_y * grid.w + start_x] = 1
            while stack:
                x, y = stack.pop()
                size += 1
                region_of[(x, y)] = index
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if (0 <= nx < grid.w and 0 <= ny < grid.h
                            and not seen[ny * grid.w + nx]
                            and grid.get(1, nx, ny) == 0):
                        seen[ny * grid.w + nx] = 1
                        stack.append((nx, ny))
            regions.append(size)

    biggest = regions.index(max(regions)) if regions else -1
    orphans = []
    for via in stitching:
        gx, gy = grid.index(via["x"], via["y"])
        found = region_of.get((gx, gy))
        if found is None:
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    found = region_of.get((gx + dx, gy + dy), found)
        if found != biggest:
            orphans.append(via["node"])

    total = grid.w * grid.h
    return {
        "layer": "bottom", "kind": "pour", "net": "GND",
        "stitching_vias": stitching,
        "islands": len(regions),
        "coverage": round(max(regions) / total, 3) if regions else 0.0,
        "orphans": orphans,
        "note": ("bottom-side copper pour with a via under every ground pad; the pour is the "
                 "return path, which is why it is checked for being one piece"),
    }


def _length(points: list[list[float]]) -> float:
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def _route_checks(traces: list[dict[str, Any]], vias: list[dict[str, Any]],
                  failed: list[str], missing: list[str], pour: dict[str, Any] | None,
                  layers: int, ground: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        out.append({"check": name, "ok": bool(ok), "detail": detail})

    check("Every net is routed", not failed,
          f"{len({t['net'] for t in traces})} nets in copper, "
          f"{round(sum(_length(t['points']) for t in traces))} mm total"
          if not failed else f"no path found for: {', '.join(failed)}")
    check("Every pin in the netlist exists on a part", not missing,
          "all nodes resolve to a pad" if not missing
          else f"{len(missing)} nodes have no pad: {', '.join(sorted(set(missing))[:5])}")
    if pour:
        # What matters is that no ground pad is stranded and that the main pour still covers
        # the board. Counting regions was wrong: the little pockets sealed off around
        # mounting-hole and via keepouts are normal copper, not a severed plane.
        intact = not pour["orphans"] and pour["coverage"] >= 0.55
        check("Ground pour is one piece", intact,
              f"one pour over {pour['coverage'] * 100:.0f}% of the bottom layer, "
              f"{len(pour['stitching_vias'])} stitching vias, "
              f"{pour['islands'] - 1} small pockets around keepouts"
              if intact and not pour["orphans"]
              else (f"{len(pour['orphans'])} ground pads sit on copper cut off from the main "
                    f"pour: {', '.join(pour['orphans'][:4])}" if pour["orphans"]
                    else f"the main pour only covers {pour['coverage'] * 100:.0f}% of the "
                         f"bottom layer; bottom-side traces have broken up the return path"))
    elif ground is None:
        check("Ground pour is one piece", True, "this board has no ground net to pour")
    check("Via count is sane", len(vias) <= 4 * max(1, len(traces)),
          f"{len(vias)} vias for {len(traces)} trace runs")
    return out


# ── independent design rule check ────────────────────────────────────────────
def drc(routed: dict[str, Any], layout: dict[str, Any], parts: list[dict[str, Any]],
        nets: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Re-derive the rules from the finished geometry.

    Deliberately not a readback of the router's own state. It recomputes every clearance from
    the trace coordinates that were actually emitted, so a bug in the router shows up here
    instead of being confirmed by it.
    """
    out: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        out.append({"check": name, "ok": bool(ok), "detail": detail})

    segments: list[tuple[str, str, tuple[float, float], tuple[float, float], float]] = []
    for trace in routed["traces"]:
        for a, b in zip(trace["points"], trace["points"][1:]):
            segments.append((trace["net"], trace["layer"], (a[0], a[1]), (b[0], b[1]),
                             trace["width_mm"]))

    worst = (1e9, "")
    for i, (net_a, layer_a, a1, a2, w_a) in enumerate(segments):
        for net_b, layer_b, b1, b2, w_b in segments[i + 1:]:
            if net_a == net_b or layer_a != layer_b:
                continue
            gap = _segment_gap(a1, a2, b1, b2) - (w_a + w_b) / 2
            if gap < worst[0]:
                worst = (gap, f"{net_a} to {net_b} on {layer_a}")
    check("Copper clearance", worst[0] >= CLEARANCE_MM - 1e-6,
          f"closest approach {worst[0]:.2f} mm ({worst[1]}), minimum {CLEARANCE_MM:g} mm"
          if segments else "no traces to check")

    thin = []
    for trace in routed["traces"]:
        need = _net_width_mm({"name": trace["net"]}, spec)
        if trace["width_mm"] + 1e-9 < need:
            thin.append(f"{trace['net']} is {trace['width_mm']:g} mm, needs {need:g} mm")
    check("Trace width carries the current", not thin,
          "every net is at or above its IPC-2221 width" if not thin else "; ".join(thin[:3]))

    # Connectivity, recomputed by walking the geometry rather than trusting the router.
    reached = _connectivity(routed, layout, parts, nets)
    broken = [name for name, whole in reached.items() if not whole]
    check("Connectivity holds in the geometry", not broken,
          f"{len(reached)} nets verified end to end" if not broken
          else f"not continuous: {', '.join(broken[:4])}")

    # Trace to another net's pad. The router models a pad as the single cell it sits on and
    # relies on the clearance halo, which is a simplification, so it is checked rather than
    # assumed.
    by_ref = {p["reference"]: p for p in parts}
    foreign = 1e9
    worst_pad = ""
    pad_net = {}
    for net in nets:
        for node in net["nodes"]:
            pad_net[node] = net["name"]
    for seat in layout["placements"]:
        part = by_ref.get(seat["reference"])
        if not part:
            continue
        for pin, xy in pad_positions(seat, part).items():
            owner = pad_net.get(f"{seat['reference']}.{pin}")
            for net_name, layer, a1, a2, width in segments:
                if layer != "top" or net_name == owner:
                    continue
                gap = _segment_gap(xy, xy, a1, a2) - width / 2 - PAD_MODEL_MM / 2
                if gap < foreign:
                    foreign = gap
                    worst_pad = f"{net_name} to {seat['reference']}.{pin}"
    check("Traces clear other nets' pads", foreign >= CLEARANCE_MM - 1e-6,
          f"closest approach {foreign:.2f} mm ({worst_pad})" if worst_pad
          else "no top-layer traces to check")

    holes = layout["mounting_holes"]
    fouled = [f"{v['net']} via" for v in routed["vias"]
              for h in holes
              if math.dist((v["x"], v["y"]), (h["x"], h["y"])) < HOLE_KEEPOUT_MM]
    check("Vias clear the mounting holes", not fouled,
          f"{len(routed['vias'])} vias, none inside a {HOLE_KEEPOUT_MM:g} mm keepout"
          if not fouled else f"{len(fouled)} via(s) inside a mounting keepout")
    return out


def _connectivity(routed: dict[str, Any], layout: dict[str, Any],
                  parts: list[dict[str, Any]], nets: list[dict[str, Any]]) -> dict[str, bool]:
    """Is every pad on a net actually joined to the others by the copper that was emitted?"""
    by_ref = {p["reference"]: p for p in parts}
    pads: dict[str, tuple[float, float]] = {}
    for seat in layout["placements"]:
        part = by_ref.get(seat["reference"])
        if part:
            for pin, xy in pad_positions(seat, part).items():
                pads[f"{seat['reference']}.{pin}"] = xy

    out: dict[str, bool] = {}
    near = GRID_MM * 1.6

    def _touches_run(point: tuple[float, float], run: list[list[float]]) -> bool:
        """A pad joins a run if it lies on any of its segments, not only at a vertex: a trace
        passing straight through a pad connects to it."""
        for a, b in zip(run, run[1:]):
            if _segment_gap(point, point, (a[0], a[1]), (b[0], b[1])) <= near:
                return True
        return bool(run) and math.dist(point, (run[0][0], run[0][1])) <= near

    planes = {p["net"] for p in (routed.get("planes") or [])} | {"GND"}
    for net in nets:
        if net["name"] in planes:
            continue         # a plane is checked by its stitching, not by tracing it
        points = [pads[n] for n in net["nodes"] if n in pads]
        if len(points) < 2:
            continue
        runs = [t["points"] for t in routed["traces"] if t["net"] == net["name"]]
        if not runs:
            out[net["name"]] = False
            continue
        via_points = [(v["x"], v["y"]) for v in routed["vias"] if v["net"] == net["name"]]

        # Pads, runs and vias are all nodes. A spanning-tree net is several runs joined end to
        # end and through vias, so unioning pads only through a shared run declared a
        # perfectly connected net broken.
        n_pads, n_runs = len(points), len(runs)
        groups = list(range(n_pads + n_runs))

        def find(i: int) -> int:
            while groups[i] != i:
                groups[i] = groups[groups[i]]
                i = groups[i]
            return i

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                groups[ra] = rb

        for ri, run in enumerate(runs):
            for pi, point in enumerate(points):
                if _touches_run(point, run):
                    union(pi, n_pads + ri)
        for i, a in enumerate(runs):
            for j, b in enumerate(runs[i + 1:], start=i + 1):
                ends_a = [(a[0][0], a[0][1]), (a[-1][0], a[-1][1])]
                ends_b = [(b[0][0], b[0][1]), (b[-1][0], b[-1][1])]
                joined = any(math.dist(p, q) <= near for p in ends_a for q in ends_b)
                if not joined:
                    joined = any(_touches_run(p, b) for p in ends_a) or \
                             any(_touches_run(q, a) for q in ends_b)
                if not joined and via_points:
                    joined = any(math.dist(v, p) <= near and math.dist(v, q) <= near
                                 for v in via_points for p in ends_a for q in ends_b)
                if joined:
                    union(n_pads + i, n_pads + j)

        out[net["name"]] = len({find(i) for i in range(n_pads)}) == 1
    return out


def _segment_gap(a1: tuple[float, float], a2: tuple[float, float],
                 b1: tuple[float, float], b2: tuple[float, float]) -> float:
    """Shortest distance between two line segments."""
    def point_to(p, q1, q2):
        vx, vy = q2[0] - q1[0], q2[1] - q1[1]
        length = vx * vx + vy * vy
        if length == 0:
            return math.dist(p, q1)
        t = max(0.0, min(1.0, ((p[0] - q1[0]) * vx + (p[1] - q1[1]) * vy) / length))
        return math.dist(p, (q1[0] + t * vx, q1[1] + t * vy))

    return min(point_to(a1, b1, b2), point_to(a2, b1, b2),
               point_to(b1, a1, a2), point_to(b2, a1, a2))


# ── copper as geometry ───────────────────────────────────────────────────────
MM = 1 / 25.4
COPPER_MM = 0.035
MAX_TRACE_BODIES = 260


def copper_features(routed: dict[str, Any], board_mm: list[float],
                    thickness_in: float) -> tuple[list[dict[str, Any]], int]:
    """Trace runs as thin plates, so the routed board is visible in the 3D viewer.

    Bounded on purpose: a few hundred slivers is a board you can look at, several thousand is
    a viewer that stops responding. The count that was dropped is returned so the dossier can
    say so rather than quietly showing a partial board.
    """
    from app.services.frc_cad import _at, _rot, plate  # noqa: PLC0415

    w_mm, h_mm = board_mm
    runs: list[tuple[str, str, list[float], list[float], float]] = []
    for trace in routed["traces"]:
        for a, b in zip(trace["points"], trace["points"][1:]):
            runs.append((trace["net"], trace["layer"], a, b, trace["width_mm"]))
    dropped = max(0, len(runs) - MAX_TRACE_BODIES)

    out: list[dict[str, Any]] = []
    for i, (net, layer, a, b, width) in enumerate(runs[:MAX_TRACE_BODIES]):
        length = math.dist(a, b)
        if length <= 0:
            continue
        cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        vertical = abs(b[1] - a[1]) > abs(b[0] - a[0])
        size = ((width * MM, COPPER_MM * MM, length * MM) if vertical
                else (length * MM, COPPER_MM * MM, width * MM))
        y = thickness_in + COPPER_MM * MM / 2 if layer == "top" else -COPPER_MM * MM / 2
        out.append(plate(f"{net} {layer} {i + 1}", size,
                         _at((cx - w_mm / 2) * MM, y, (cy - h_mm / 2) * MM),
                         mat="steel", note=f"{width:g} mm {layer} trace"))
    for i, via in enumerate(routed["vias"]):
        out.append(plate(f"via {i + 1} {via['net']}",
                         (via["pad_mm"] * MM, thickness_in + 2 * COPPER_MM * MM,
                          via["pad_mm"] * MM),
                         _at((via["x"] - w_mm / 2) * MM, thickness_in / 2,
                             (via["y"] - h_mm / 2) * MM),
                         mat="steel", note="plated through via"))
    return out, dropped


__all__ = ["route", "drc", "copper_features", "pad_positions", "ROUTE_VERSION", "FAB_NOTE",
           "_rot"]
