"""Shared geometry helpers for PCB rules (not a rule; underscore prefix keeps it obvious)."""
from __future__ import annotations

from app.models.normalized import Board, Point


def board_bbox(board: Board) -> tuple[float, float, float, float] | None:
    """(min_x, min_y, max_x, max_y) from the outline, or None."""
    if not board.outline:
        return None
    xs = [p.x for p in board.outline]
    ys = [p.y for p in board.outline]
    return (min(xs), min(ys), max(xs), max(ys))


def edge_distance(x: float, y: float, bbox: tuple[float, float, float, float]) -> float:
    """Distance from a point to the nearest bbox edge (negative if outside)."""
    min_x, min_y, max_x, max_y = bbox
    return min(x - min_x, max_x - x, y - min_y, max_y - y)


def inside(x: float, y: float, bbox: tuple[float, float, float, float], margin: float = 0.0) -> bool:
    min_x, min_y, max_x, max_y = bbox
    return (min_x - margin) <= x <= (max_x + margin) and (min_y - margin) <= y <= (max_y + margin)


def seg_length(a: Point, b: Point) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5
