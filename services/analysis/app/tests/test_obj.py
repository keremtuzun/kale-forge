from app.parsers.normalizer import normalize_project
from app.parsers.obj import parse_obj
from app.services.security import classify_file, validate_upload
from app.config import Settings


CUBE = b"""# unit cube
o Cube
v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
v 0 0 1
v 1 0 1
v 1 1 1
v 0 1 1
usemtl Aluminum
f 1 4 3 2
f 5 6 7 8
f 1 2 6 5
f 2 3 7 6
f 3 4 8 7
f 4 1 5 8
"""


def test_parse_closed_cube_obj():
    result = parse_obj(CUBE.decode(), "cube.obj")
    assert result.mesh.vertices == 8
    assert result.mesh.faces == 6
    assert result.mesh.triangles == 12
    assert result.mesh.boundary_edges == 0
    assert result.mesh.nonmanifold_edges == 0
    assert result.mesh.bounds.size == (1.0, 1.0, 1.0)
    assert result.mesh.materials == ["Aluminum"]


def test_obj_normalizes_and_upload_is_allowed():
    normalized = normalize_project([("cube.obj", CUBE)], "Cube")
    assert normalized.project.source_format == "obj"
    assert normalized.mesh is not None and normalized.mesh.vertices == 8
    assert classify_file("robot.obj") == "mesh"
    assert validate_upload("robot.obj", len(CUBE), Settings()) == "robot.obj"


def test_obj_units_and_large_upload_limit():
    result = parse_obj((b"# Units = meters\n" + CUBE).decode(), "robot.obj")
    assert result.mesh.units == "meters"
    assert validate_upload("robot.obj", 55 * 1024 * 1024, Settings()) == "robot.obj"
