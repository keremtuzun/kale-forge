import pytest
from scripts.build_preference_pairs import build_pairs

def test_builds_canonical_pair():
    pairs=build_pairs([{"prompt":"make an elevator","split":"train","category":"duplicate_parts",
                        "rejected":{"parts":["rail","rail"]},"chosen":{"parts":["rail left","rail right"]}}])
    assert pairs[0]["failure_category"]=="duplicate_parts"
    assert pairs[0]["chosen"] != pairs[0]["rejected"]

def test_refuses_heldout_leakage():
    with pytest.raises(ValueError,match="forbidden"):
        build_pairs([{"prompt":"secret","split":"held-out","category":"schema_violation",
                      "rejected":{},"chosen":{"ok":True}}])

def test_refuses_identical_pair():
    with pytest.raises(ValueError,match="identical"):
        build_pairs([{"prompt":"x","category":"schema_violation","rejected":{},"chosen":{}}])
