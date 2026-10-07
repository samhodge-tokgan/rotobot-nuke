"""Tests for the `rotobot-undersample` CLI (presets + hierarchical kwargs)."""

from __future__ import annotations

import json

import pytest

from rotobot_nuke import (
    PRESET_BALANCED,
    PRESET_COARSE,
    PRESET_FINE,
    PRESETS,
)
from rotobot_nuke.undersample import _cli


class TestPresets:
    def test_presets_dict_exposed(self):
        assert set(PRESETS.keys()) == {"fine", "balanced", "coarse"}
        assert PRESETS["fine"] == PRESET_FINE
        assert PRESETS["balanced"] == PRESET_BALANCED
        assert PRESETS["coarse"] == PRESET_COARSE

    def test_preset_values_body_local_tuned(self):
        """Measured on the 4-clip real-plate aggregate — these are the
        committed sweet-spot triples from PR #5's data."""
        assert PRESET_FINE == (0.5, 1.0, 0.015)
        assert PRESET_BALANCED == (1.0, 2.0, 0.05)
        assert PRESET_COARSE == (2.5, 5.0, 0.1)

    def test_preset_ordering(self):
        assert PRESET_COARSE.camera_tolerance > PRESET_BALANCED.camera_tolerance > PRESET_FINE.camera_tolerance
        assert PRESET_COARSE.person_tolerance > PRESET_BALANCED.person_tolerance > PRESET_FINE.person_tolerance
        assert PRESET_COARSE.articulation_tolerance > PRESET_BALANCED.articulation_tolerance > PRESET_FINE.articulation_tolerance


class TestCliLegacyPath:
    def test_tolerance_arg_runs_single_pass(self, fixtures_dir, tmp_path, capsys):
        inp = fixtures_dir / "v2_small.json"
        out = tmp_path / "out.json"
        rc = _cli([str(inp), str(out), "--tolerance", "5.0"])
        assert rc == 0
        assert out.exists()
        captured = capsys.readouterr().out
        assert "single-pass tolerance=5.0" in captured

    def test_no_args_uses_default_tolerance(self, fixtures_dir, tmp_path, capsys):
        inp = fixtures_dir / "v2_small.json"
        out = tmp_path / "out.json"
        rc = _cli([str(inp), str(out)])
        assert rc == 0
        assert out.exists()
        # Default single-pass is DEFAULT_TOLERANCE (TOLERANCE_BALANCED=5.0)
        captured = capsys.readouterr().out
        assert "single-pass tolerance=" in captured


class TestCliHierarchicalPath:
    def test_preset_flag_resolves_to_tuple(self, fixtures_dir, tmp_path, capsys):
        inp = fixtures_dir / "v3_small.json"
        out = tmp_path / "out.json"
        rc = _cli([str(inp), str(out), "--preset", "balanced"])
        assert rc == 0
        captured = capsys.readouterr().out
        assert "hierarchical" in captured
        assert "cam=1.0" in captured
        assert "person=2.0" in captured
        assert "articulation=0.05" in captured

    def test_individual_kwarg_overrides_preset_component(
        self, fixtures_dir, tmp_path, capsys
    ):
        inp = fixtures_dir / "v3_small.json"
        out = tmp_path / "out.json"
        rc = _cli([
            str(inp), str(out),
            "--preset", "balanced",
            "--articulation-tolerance", "0.5",
        ])
        assert rc == 0
        captured = capsys.readouterr().out
        # Articulation overridden to 0.5; camera / person stay at balanced's 1.0 / 2.0
        assert "articulation=0.5" in captured
        assert "cam=1.0" in captured
        assert "person=2.0" in captured

    def test_single_hierarchical_kwarg_triggers_hierarchical(
        self, fixtures_dir, tmp_path, capsys
    ):
        inp = fixtures_dir / "v3_small.json"
        out = tmp_path / "out.json"
        rc = _cli([
            str(inp), str(out),
            "--articulation-tolerance", "0.05",
        ])
        assert rc == 0
        captured = capsys.readouterr().out
        assert "hierarchical" in captured

    def test_mixing_tolerance_and_preset_errors(
        self, fixtures_dir, tmp_path, capsys
    ):
        inp = fixtures_dir / "v3_small.json"
        out = tmp_path / "out.json"
        with pytest.raises(SystemExit):
            _cli([
                str(inp), str(out),
                "--tolerance", "5.0",
                "--preset", "balanced",
            ])

    def test_mixing_tolerance_and_bare_hierarchical_errors(
        self, fixtures_dir, tmp_path
    ):
        inp = fixtures_dir / "v3_small.json"
        out = tmp_path / "out.json"
        with pytest.raises(SystemExit):
            _cli([
                str(inp), str(out),
                "--tolerance", "5.0",
                "--camera-tolerance", "1.0",
            ])


class TestHierarchicalJsonRoundtrip:
    def test_hierarchical_path_writes_valid_output(
        self, fixtures_dir, tmp_path
    ):
        inp = fixtures_dir / "v3_small.json"
        out = tmp_path / "out.json"
        rc = _cli([str(inp), str(out), "--preset", "coarse"])
        assert rc == 0
        # Output must be valid JSON with the lozenge_bezier_anim schema
        rebuilt = json.loads(out.read_text())
        assert rebuilt["schema"] == "lozenge_bezier_anim"
        assert rebuilt["schema_version"] == 3
        assert "camera" in rebuilt
        assert "persons" in rebuilt
