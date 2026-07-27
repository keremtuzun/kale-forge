"""Safe, dependency-free Wavefront OBJ metadata and topology parser."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isfinite
import re

from app.models.normalized import Mesh, MeshBounds, ParserWarning

TOPOLOGY_FACE_LIMIT = 250_000


@dataclass
class ObjData:
    mesh: Mesh
    warnings: list[ParserWarning]


def parse_obj(text: str, filename: str = "") -> ObjData:
    vertices = texcoords = normals = faces = triangles = lines = points = 0
    invalid_vertices = invalid_faces = degenerate_faces = 0
    objects: set[str] = set(); groups: set[str] = set(); materials: set[str] = set(); libraries: set[str] = set()
    minimum = [float("inf")] * 3; maximum = [float("-inf")] * 3
    edge_counts: Counter[tuple[int, int]] = Counter(); topology_truncated = False
    units = "unspecified"

    for line_number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped: continue
        if stripped.startswith("#"):
            unit_match = re.search(r"\bunits?\s*=\s*([A-Za-z]+)", stripped, re.I)
            if unit_match: units = unit_match.group(1).lower()
            continue
        parts = stripped.split(); kind = parts[0]
        if kind == "v":
            if len(parts) < 4:
                invalid_vertices += 1; continue
            try: xyz = [float(parts[i]) for i in range(1, 4)]
            except ValueError: invalid_vertices += 1; continue
            if not all(isfinite(value) for value in xyz): invalid_vertices += 1; continue
            vertices += 1
            for axis, value in enumerate(xyz): minimum[axis] = min(minimum[axis], value); maximum[axis] = max(maximum[axis], value)
        elif kind == "vt": texcoords += 1
        elif kind == "vn": normals += 1
        elif kind == "f":
            indices: list[int] = []
            try:
                for token in parts[1:]:
                    raw_index = int(token.split("/", 1)[0]); index = raw_index if raw_index > 0 else vertices + raw_index + 1
                    if index <= 0 or index > vertices: raise ValueError
                    indices.append(index)
            except (ValueError, IndexError): invalid_faces += 1; continue
            if len(indices) < 3: invalid_faces += 1; continue
            faces += 1; triangles += len(indices) - 2
            if len(set(indices)) < 3: degenerate_faces += 1
            if faces <= TOPOLOGY_FACE_LIMIT:
                for a, b in zip(indices, indices[1:] + indices[:1]): edge_counts[tuple(sorted((a, b)))] += 1
            else: topology_truncated = True
        elif kind == "l": lines += 1
        elif kind == "p": points += 1
        elif kind == "o" and len(parts) > 1: objects.add(" ".join(parts[1:])[:200])
        elif kind == "g" and len(parts) > 1: groups.update(name[:200] for name in parts[1:])
        elif kind == "usemtl" and len(parts) > 1: materials.add(" ".join(parts[1:])[:200])
        elif kind == "mtllib" and len(parts) > 1: libraries.update(name[:200] for name in parts[1:])

    bounds = None
    if vertices:
        bounds = MeshBounds(min_x=minimum[0], min_y=minimum[1], min_z=minimum[2],
                            max_x=maximum[0], max_y=maximum[1], max_z=maximum[2])
    boundary_edges = sum(1 for count in edge_counts.values() if count == 1)
    nonmanifold_edges = sum(1 for count in edge_counts.values() if count > 2)
    mesh = Mesh(filename=filename, units=units, vertices=vertices, texture_coordinates=texcoords, normals=normals,
                faces=faces, triangles=triangles, lines=lines, points=points, objects=sorted(objects),
                groups=sorted(groups), materials=sorted(materials), material_libraries=sorted(libraries),
                bounds=bounds, boundary_edges=boundary_edges, nonmanifold_edges=nonmanifold_edges,
                degenerate_faces=degenerate_faces, invalid_vertices=invalid_vertices,
                invalid_faces=invalid_faces, topology_truncated=topology_truncated)
    warnings: list[ParserWarning] = []
    if vertices == 0: warnings.append(ParserWarning(file=filename, message="OBJ contains no valid vertices"))
    if faces == 0: warnings.append(ParserWarning(file=filename, message="OBJ contains no valid polygon faces"))
    if invalid_vertices: warnings.append(ParserWarning(file=filename, message=f"{invalid_vertices} invalid vertex record(s) ignored"))
    if invalid_faces: warnings.append(ParserWarning(file=filename, message=f"{invalid_faces} invalid face record(s) ignored"))
    if degenerate_faces: warnings.append(ParserWarning(file=filename, message=f"{degenerate_faces} degenerate face(s) detected"))
    if boundary_edges: warnings.append(ParserWarning(file=filename, message=f"{boundary_edges} boundary edge(s); mesh may be open"))
    if nonmanifold_edges: warnings.append(ParserWarning(file=filename, message=f"{nonmanifold_edges} non-manifold edge(s) detected"))
    if topology_truncated: warnings.append(ParserWarning(file=filename, message=f"topology checks limited to first {TOPOLOGY_FACE_LIMIT:,} faces"))
    return ObjData(mesh=mesh, warnings=warnings)
