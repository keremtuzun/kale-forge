from copy import deepcopy

import pytest

from app.services.cad_contract import (CadContractError, editable_manifest, normalize_cad,
                                       require_valid_cad, validate_cad,
                                       validate_parametric_design)
from app.services.robot_spec import build_robot_spec


def _cad():
    return build_robot_spec(
        "28 inch swerve with intake hopper shooter elevator and climber",
        use_model=False,
    )["cad"]


def test_compiled_cad_is_strict_and_every_part_has_stable_editable_id():
    cad = _cad()
    assert validate_cad(cad) == []
    manifest = editable_manifest(cad)
    ids = [part["id"] for part in manifest["parts"]]
    assert manifest["flattened"] is False
    assert len(ids) == len(set(ids))
    assert all(part["dimensions"] for part in manifest["parts"])


@pytest.mark.parametrize("mutation, needle", [
    (lambda c: c["assemblies"][0]["features"][0].update(t="invented_extrude"), "illegal feature"),
    (lambda c: c["assemblies"][0]["features"][0].update(len=-2), "negative"),
    (lambda c: c["assemblies"][0]["features"][0].update(sec=[1.25, 0.75]), "non-catalog"),
])
def test_bad_cad_fails_closed(mutation, needle):
    cad = deepcopy(_cad())
    mutation(cad)
    with pytest.raises(CadContractError, match=needle):
        require_valid_cad(cad)


def test_duplicate_parts_are_normalized_before_compilation():
    cad = deepcopy(_cad())
    cad["assemblies"][0]["features"][1]["n"] = cad["assemblies"][0]["features"][0]["n"]
    assert any("duplicate part name" in error for error in validate_cad(cad))
    normalized = normalize_cad(cad)
    assert validate_cad(normalized) == []


def test_parametric_dependency_loops_and_duplicate_nodes_are_rejected():
    design = build_robot_spec("28 inch robot with intake", use_model=False)["parametric_design"]
    design["mechanisms"].append({"id": "intake", "kind": "hopper",
                                  "architecture": "belt", "depends_on": ["intake"]})
    errors = validate_parametric_design(design)
    assert any("duplicate id" in error for error in errors)
    assert any("dependency loop" in error for error in errors)
