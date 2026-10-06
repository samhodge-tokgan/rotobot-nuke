"""Cheap smoke: every fixture file parses cleanly."""

from __future__ import annotations

import pytest

from rotobot_nuke import MissingResolutionError, load_json


@pytest.mark.parametrize(
    "name, kwargs",
    [
        ("v2_small.json", {}),
        ("v2_hd_1920_1080.json", {}),
        ("v1_small.json", {"resolution": (1920, 1080)}),
    ],
)
def test_fixture_parses(fixtures_dir, name, kwargs):
    doc = load_json(fixtures_dir / name, **kwargs)
    assert doc.schema == "lozenge_bezier_anim"
    assert doc.resolution[0] > 0 and doc.resolution[1] > 0
    assert doc.objects, f"{name}: expected at least one object"


def test_v1_fixture_without_kwarg_raises(fixtures_dir):
    with pytest.raises(MissingResolutionError):
        load_json(fixtures_dir / "v1_small.json")
