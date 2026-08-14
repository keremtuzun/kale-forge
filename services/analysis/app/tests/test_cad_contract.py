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


def _first_of(cad, kind):
    for assembly in cad["assemblies"]:
        for feature in assembly["features"]:
            if feature.get("t") == kind:
                return assembly, feature
    raise AssertionError(f"no {kind} in the compiled robot")


@pytest.mark.parametrize("kind, mutation", [
    # A 24-tooth gear at 20 DP is a 1.2 in pitch circle.  0.96 is the value the v18 held-out
    # arm actually emitted, and nothing in production rejected it before.
    ("gear", {"teeth": 24, "dp": 20.0, "pd": 0.96}),
    ("sprocket", {"teeth": 16, "pitch_in": 0.25, "pd": 0.9}),
    ("pulley", {"teeth": 24, "pitch_mm": 5.0, "pd": 2.4}),
])
def test_a_tooth_count_that_disagrees_with_its_pitch_circle_is_rejected(kind, mutation):
    cad = deepcopy(_cad())
    _, feature = _first_of(cad, kind)
    feature.update(mutation)
    with pytest.raises(CadContractError, match="disagrees with"):
        require_valid_cad(cad)


def test_stock_is_matched_regardless_of_how_the_section_is_ordered():
    """A 1x2 tube is a 2x1 tube rotated, so the catalog lookup is orientation-free."""
    cad = deepcopy(_cad())
    _, tube = _first_of(cad, "tube")
    tube["sec"] = [tube["sec"][1], tube["sec"][0]]
    assert validate_cad(cad) == []


def test_a_repetition_loop_cannot_be_renamed_into_validity():
    """The v18 CAD failures were one feature repeated at a fixed position until the token
    budget ran out.  Unique names must not be enough to make that compile."""
    cad = deepcopy(_cad())
    assembly, feature = _first_of(cad, "plate")
    for index in range(12):
        clone = deepcopy(feature)
        clone["n"] = f"{feature['n']} clone {index}"
        assembly["features"].append(clone)
    assert validate_cad(normalize_cad(cad)) != []
    with pytest.raises(CadContractError, match="coincident"):
        require_valid_cad(normalize_cad(cad))


def test_a_whole_assembly_emitted_twice_cannot_be_renamed_into_validity():
    """Renaming the copy would compile one mechanism invisibly on top of another.

    Four real swerve modules are the counter-case and must still pass: they repeat, but each
    sits at its own origin, so they are not identical in content and position.
    """
    cad = deepcopy(_cad())
    cad["assemblies"].append({**deepcopy(cad["assemblies"][5]), "id": "intake_copy"})
    with pytest.raises(CadContractError, match="identical in content and position"):
        require_valid_cad(normalize_cad(cad))


def test_repeated_modules_at_their_own_origins_are_not_duplicates():
    cad = _cad()
    swerve = [a for a in cad["assemblies"] if a["id"].startswith("swerve_")]
    assert len(swerve) == 4
    assert validate_cad(cad) == []


@pytest.mark.parametrize("mutation", [
    {"sec": ["two", "one"]},
    {"sec": [2.0, None]},
    {"len": "24 inches"},
    {"rep": {"n": 4, "step": ["x", 0, 0]}},
])
def test_malformed_values_are_reported_not_raised(mutation):
    """Everything here may be model-authored, so a bad value is a contract error, never a 500."""
    cad = deepcopy(_cad())
    _, tube = _first_of(cad, "tube")
    tube.update(mutation)
    assert validate_cad(cad) != []


def test_parametric_dependency_loops_and_duplicate_nodes_are_rejected():
    design = build_robot_spec("28 inch robot with intake", use_model=False)["parametric_design"]
    design["mechanisms"].append({"id": "intake", "kind": "hopper",
                                  "architecture": "belt", "depends_on": ["intake"]})
    errors = validate_parametric_design(design)
    assert any("duplicate id" in error for error in errors)
    assert any("dependency loop" in error for error in errors)


def test_a_non_object_feature_is_reported_not_crashed():
    """A model once emitted a bare string in a features list; normalize must survive it."""
    cad = deepcopy(_cad())
    cad["assemblies"][0]["features"].append("totally a part")
    normalized = normalize_cad(cad)          # must not raise
    assert any("object required" in e for e in validate_cad(normalized))


# ── mirror: a dual is one feature, its twin is derived ───────────────────────
# Added because v18 modeled "dual telescoping hooks" as two copies of every part at the SAME
# position. With mirror the second copy cannot be wrong, because nobody writes it.

def test_a_mirrored_pair_expands_to_two_parts_with_reflected_placement():
    from app.services.cad_contract import expand_mirrors

    feature = {"t": "hook", "n": "climber hook", "at": [6.0, 20.0, -1.0],
               "rot": [10.0, 20.0, 30.0], "mirror": "x"}
    out = expand_mirrors([feature])
    assert len(out) == 2
    original, twin = out
    assert "mirror" not in original and "mirror" not in twin
    assert twin["n"] == "climber hook mirror"
    assert twin["at"] == [-6.0, 20.0, -1.0]
    # rotation about the mirror axis survives; the other two negate
    assert twin["rot"] == [10.0, -20.0, -30.0]


def test_a_mirrored_feature_on_the_mirror_plane_is_rejected():
    """Reflecting a part sitting on the plane lands it on itself — the coincident-body
    defect this field exists to prevent, so the contract refuses it up front."""
    cad = deepcopy(_cad())
    _, plate = _first_of(cad, "plate")
    plate.update(at=[0.0, plate["at"][1], plate["at"][2]], mirror="x")
    assert any("reflect onto itself" in e for e in validate_cad(cad))


def test_a_bad_mirror_axis_is_rejected():
    cad = deepcopy(_cad())
    _, plate = _first_of(cad, "plate")
    plate.update(mirror="diagonal")
    assert any("axis must be one of" in e for e in validate_cad(cad))


def test_a_mirrored_tube_is_two_pieces_of_stock_in_the_cut_list():
    from app.services.frc_cad import cut_list

    cad = deepcopy(_cad())
    assembly, tube = _first_of(cad, "tube")
    before = sum(r["qty"] for r in cut_list(cad))
    tube["mirror"] = "x"
    tube["at"] = [max(abs(tube["at"][0]), 3.0), tube["at"][1], tube["at"][2]]
    after = sum(r["qty"] for r in cut_list(cad))
    assert after == before + 1, "a mirrored tube must count as two"


def test_a_mirrored_feature_reaches_featurescript_twice():
    from app.services.frc_featurescript import build_featurescript

    spec = build_robot_spec("27x27 REBUILT robot with a hooded shooter",
                            use_model=False, season="2026-rebuilt")
    shooter = next(a for a in spec["cad"]["assemblies"] if a["id"] == "shooter")
    shooter["features"].append({"t": "hook", "n": "test mirror hook",
                                "at": [6.0, 12.0, -2.0], "mirror": "x"})
    source = build_featurescript(spec, "Mirror Robot")
    assert "test_mirror_hook" in source
    assert "test_mirror_hook_mirror" in source
