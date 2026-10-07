"""Tests for the ``.nk`` curves-knob import cache (:mod:`rotobot_nuke.cache`).

Validates:
- cache miss on first build, populate on return;
- cache hit on second build (skips the slow Python-API path);
- mtime bump invalidates;
- mode / curve_type / tolerance all participate in the key;
- entries land under ``$XDG_CACHE_HOME/rotobot_nuke/``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from rotobot_nuke import cache, load_json
from rotobot_nuke.importer import build_roto


@pytest.fixture
def xdg_cache(tmp_path, monkeypatch):
    """Point XDG_CACHE_HOME at a fresh tmp dir so each test gets a clean cache."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    yield tmp_path / "rotobot_nuke"


# ---------------------------------------------------------------------------
# CacheKey


class TestCacheKey:
    def test_digest_stable_across_calls(self, tmp_path):
        p = tmp_path / "a.json"
        p.write_text("{}")
        k1 = cache.make_key(str(p), mode="legacy", curve_type="bspline")
        k2 = cache.make_key(str(p), mode="legacy", curve_type="bspline")
        assert k1.digest() == k2.digest()

    def test_mode_changes_digest(self, tmp_path):
        p = tmp_path / "a.json"
        p.write_text("{}")
        a = cache.make_key(str(p), mode="legacy", curve_type="bspline").digest()
        b = cache.make_key(str(p), mode="hierarchical", curve_type="bspline").digest()
        assert a != b

    def test_tolerance_changes_digest(self, tmp_path):
        p = tmp_path / "a.json"
        p.write_text("{}")
        a = cache.make_key(str(p), undersample_tolerance=None).digest()
        b = cache.make_key(str(p), undersample_tolerance=1.0).digest()
        c = cache.make_key(str(p), undersample_tolerance=2.0).digest()
        assert len({a, b, c}) == 3

    def test_mtime_changes_digest(self, tmp_path):
        p = tmp_path / "a.json"
        p.write_text("{}")
        a = cache.make_key(str(p)).digest()
        time.sleep(0.01)
        p.write_text("{}  ")  # bump mtime
        os.utime(p, None)
        b = cache.make_key(str(p)).digest()
        assert a != b

    def test_missing_file_still_hashable(self, tmp_path):
        # No file at the path — key should still build (mtime_ns=0).
        k = cache.make_key(str(tmp_path / "nope.json"))
        assert k.doc_mtime_ns == 0
        assert k.digest()


# ---------------------------------------------------------------------------
# get / put / invalidate


class TestGetPut:
    def test_miss_then_put_then_hit(self, xdg_cache, tmp_path):
        p = tmp_path / "a.json"
        p.write_text("{}")
        k = cache.make_key(str(p))
        assert cache.get(k) is None
        cache.put(k, "hello")
        assert cache.get(k) == "hello"

    def test_entry_lands_under_xdg_cache(self, xdg_cache, tmp_path):
        p = tmp_path / "a.json"
        p.write_text("{}")
        cache.put(cache.make_key(str(p)), "payload")
        entries = list(xdg_cache.glob("*.curves.txt"))
        assert len(entries) == 1
        assert entries[0].read_text() == "payload"

    def test_invalidate_removes_entry(self, xdg_cache, tmp_path):
        p = tmp_path / "a.json"
        p.write_text("{}")
        k = cache.make_key(str(p))
        cache.put(k, "x")
        assert cache.invalidate(k) is True
        assert cache.get(k) is None
        assert cache.invalidate(k) is False  # second remove is a no-op

    def test_stats_reports_entries(self, xdg_cache, tmp_path):
        for i in range(3):
            p = tmp_path / f"a{i}.json"
            p.write_text("{}")
            cache.put(cache.make_key(str(p)), f"payload-{i}")
        n, total = cache.cache_stats()
        assert n == 3
        assert total > 0


# ---------------------------------------------------------------------------
# build_roto integration


def _curves(node):
    return node["curves"]


def _has_fast_path_marker(node) -> bool:
    return any(
        type(c).__name__ == "_Layer" and c.name == "__from_script_marker__"
        for c in _curves(node).rootLayer.children
    )


class TestBuildRotoCacheIntegration:
    def test_no_cache_path_means_no_cache(
        self, xdg_cache, fake_nuke, fixtures_dir
    ):
        """Without ``cache_path=``, nothing is read or written."""
        doc = load_json(fixtures_dir / "v2_small.json")
        build_roto(doc)
        assert not xdg_cache.exists() or not list(xdg_cache.glob("*.curves.txt"))

    def test_first_import_populates_cache(
        self, xdg_cache, fake_nuke, fixtures_dir
    ):
        doc_path = fixtures_dir / "v2_small.json"
        doc = load_json(doc_path)
        node = build_roto(doc, cache_path=str(doc_path))
        # Fast path was NOT taken (built normally); but the cache was filled.
        assert not _has_fast_path_marker(node)
        entries = list(xdg_cache.glob("*.curves.txt"))
        assert len(entries) == 1

    def test_second_import_hits_cache(self, xdg_cache, fake_nuke, fixtures_dir):
        doc_path = fixtures_dir / "v2_small.json"
        doc = load_json(doc_path)
        # First build populates.
        build_roto(doc, cache_path=str(doc_path))
        # Second build takes the fast path.
        node2 = build_roto(doc, cache_path=str(doc_path))
        assert _has_fast_path_marker(node2)

    def test_mode_change_misses_cache(self, xdg_cache, fake_nuke, fixtures_dir):
        doc_path = fixtures_dir / "v3_small.json"
        doc = load_json(doc_path)
        build_roto(doc, cache_path=str(doc_path), mode="legacy")
        node = build_roto(doc, cache_path=str(doc_path), mode="hierarchical")
        # Legacy entry doesn't satisfy a hierarchical request.
        assert not _has_fast_path_marker(node)
        # And now there are two cached entries.
        assert len(list(xdg_cache.glob("*.curves.txt"))) == 2

    def test_tolerance_change_misses_cache(
        self, xdg_cache, fake_nuke, fixtures_dir
    ):
        doc_path = fixtures_dir / "v2_small.json"
        doc = load_json(doc_path)
        build_roto(doc, cache_path=str(doc_path), undersample_tolerance=None)
        node = build_roto(
            doc, cache_path=str(doc_path), undersample_tolerance=1.0
        )
        assert not _has_fast_path_marker(node)

    def test_mtime_bump_misses_cache(self, xdg_cache, fake_nuke, fixtures_dir, tmp_path):
        # Copy the fixture into a tmp file so we can bump its mtime.
        src = (fixtures_dir / "v2_small.json").read_text()
        p = tmp_path / "doc.json"
        p.write_text(src)
        doc = load_json(p)
        build_roto(doc, cache_path=str(p))
        # Bump mtime by rewriting.
        time.sleep(0.01)
        p.write_text(src + " ")
        os.utime(p, None)
        node = build_roto(doc, cache_path=str(p))
        assert not _has_fast_path_marker(node)
