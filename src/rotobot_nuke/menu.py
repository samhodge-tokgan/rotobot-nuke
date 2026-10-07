"""Nuke menu hook. Add a "Rotobot" menu with roto-JSON import commands.

Install (one of):

* ``~/.nuke/init.py`` adds::

      import rotobot_nuke.menu

* or copy this file to your ``NUKE_PATH`` as ``menu.py``.

The menu lives under the Nuke menubar (not inside File → Import) so it
sits next to other third-party integrations that follow the same
convention.
"""

from __future__ import annotations

import os

import nuke

from .reader import MissingResolutionError, load_json
from .undersample import (
    DEFAULT_TOLERANCE,
    TOLERANCE_AGGRESSIVE,
    TOLERANCE_BALANCED,
    TOLERANCE_CONSERVATIVE,
)


def _prompt_resolution_fallback(err: MissingResolutionError):
    """Ask the artist for a plate resolution; return ``(w, h)`` or ``None``."""
    txt = nuke.getInput(
        f"{err}\n\nEnter plate resolution as WxH (e.g. 1920x1080):",
        "1920x1080",
    )
    if not txt:
        return None
    try:
        w_str, h_str = str(txt).lower().replace(" ", "").split("x", 1)
        return (int(w_str), int(h_str))
    except (ValueError, AttributeError):
        nuke.message(f"Could not parse resolution {txt!r} (expected 'WxH').")
        return None


def _import_lozenge(
    curve_type: str, undersample_tolerance: float | None = None
) -> None:
    path = nuke.getFilename("Select Rotobot JSON", "*.json")
    if not path or not os.path.isfile(path):
        return

    try:
        doc = load_json(path)
    except MissingResolutionError as exc:
        resolution = _prompt_resolution_fallback(exc)
        if resolution is None:
            return
        try:
            doc = load_json(path, resolution=resolution)
        except (ValueError, MissingResolutionError) as exc2:
            nuke.message(f"Failed to load {path}: {exc2}")
            return
    except ValueError as exc:
        nuke.message(f"Failed to load {path}: {exc}")
        return

    # Import lazily so this module stays importable in CI (no nuke.rotopaint).
    from .importer import build_roto

    try:
        build_roto(
            doc,
            curve_type=curve_type,
            undersample_tolerance=undersample_tolerance,
        )
    except Exception as exc:  # pragma: no cover - Nuke-only
        nuke.message(f"Rotobot import failed: {exc}")
        return

    tol_suffix = (
        f", undersample_tolerance={undersample_tolerance}"
        if undersample_tolerance is not None
        else ""
    )
    nuke.tprint(
        f"[rotobot-nuke] imported {len(doc.objects)} objects from {path} "
        f"(curve_type={curve_type}, res={doc.resolution}{tol_suffix})"
    )


def _import_bspline() -> None:
    _import_lozenge("bspline")


def _import_bezier() -> None:
    _import_lozenge("bezier")


def _import_bspline_undersampled_balanced() -> None:
    _import_lozenge("bspline", undersample_tolerance=TOLERANCE_BALANCED)


def _import_bspline_undersampled_aggressive() -> None:
    _import_lozenge("bspline", undersample_tolerance=TOLERANCE_AGGRESSIVE)


def _import_bspline_undersampled_prompt() -> None:
    txt = nuke.getInput(
        "RDP tolerance (pixel-equivalent units). "
        f"Suggested: {TOLERANCE_CONSERVATIVE} (safe), "
        f"{TOLERANCE_BALANCED} (balanced), "
        f"{TOLERANCE_AGGRESSIVE} (aggressive).",
        str(DEFAULT_TOLERANCE),
    )
    if not txt:
        return
    try:
        tol = float(txt)
    except (TypeError, ValueError):
        nuke.message(f"Could not parse tolerance {txt!r} (expected a number).")
        return
    _import_lozenge("bspline", undersample_tolerance=tol)


def register() -> None:
    """Idempotently register the Rotobot menu. Called on module import."""
    menubar = nuke.menu("Nuke")
    menu = menubar.addMenu("Rotobot")
    # addCommand clobbers an existing entry with the same name, so re-import
    # of this module during a Nuke session is safe.
    menu.addCommand("Import Rotobot JSON (B-spline)…", _import_bspline)
    menu.addCommand("Import Rotobot JSON (Bezier)…", _import_bezier)
    menu.addCommand(
        "Import Rotobot JSON — balanced undersample…",
        _import_bspline_undersampled_balanced,
    )
    menu.addCommand(
        "Import Rotobot JSON — aggressive undersample…",
        _import_bspline_undersampled_aggressive,
    )
    menu.addCommand(
        "Import Rotobot JSON — undersample (prompt for tolerance)…",
        _import_bspline_undersampled_prompt,
    )


register()
