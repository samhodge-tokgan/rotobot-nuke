"""``import rotobot_nuke.menu`` registers the menu in the GUI and is a no-op
in terminal sessions (``nuke -t``), where ``nuke.menu()`` raises."""

from __future__ import annotations

import importlib
import sys

import pytest


class _Menu:
    def __init__(self) -> None:
        self.submenus = {}
        self.commands = []

    def addMenu(self, name):
        return self.submenus.setdefault(name, _Menu())

    def addCommand(self, name, fn):
        self.commands.append(name)


def _import_menu():
    sys.modules.pop("rotobot_nuke.menu", None)
    return importlib.import_module("rotobot_nuke.menu")


def test_terminal_session_skips_registration(fake_nuke, monkeypatch):
    def _not_gui(_name):
        raise RuntimeError("not in GUI mode")

    monkeypatch.setattr(fake_nuke, "GUI", False, raising=False)
    monkeypatch.setattr(fake_nuke, "menu", _not_gui, raising=False)
    _import_menu()


def test_gui_session_registers_menu(fake_nuke, monkeypatch):
    menubar = _Menu()
    monkeypatch.setattr(fake_nuke, "GUI", True, raising=False)
    monkeypatch.setattr(
        fake_nuke, "menu", lambda name: menubar if name == "Nuke" else None,
        raising=False,
    )
    _import_menu()
    assert "Rotobot" in menubar.submenus
    assert len(menubar.submenus["Rotobot"].commands) == 6


@pytest.fixture(autouse=True)
def _drop_menu_module():
    yield
    sys.modules.pop("rotobot_nuke.menu", None)


def _run(fake_nuke, monkeypatch, command, path):
    monkeypatch.setattr(fake_nuke, "GUI", False, raising=False)
    monkeypatch.setattr(fake_nuke, "getFilename", lambda *a, **k: str(path))
    menu = _import_menu()
    getattr(menu, command)()
    return fake_nuke.nodes.created[-1]["label"].value().splitlines()[-1]


@pytest.mark.parametrize(
    "command, fixture, expected",
    [
        # A v3 JSON brings its camera and persons in as layers.
        ("_import_bspline", "v3_real_cut.json", "Mode: hierarchical"),
        ("_import_bezier", "v3_small.json", "Mode: hierarchical"),
        ("_import_bspline_undersampled_balanced", "v3_real_cut.json", "Mode: hierarchical"),
        # Without them there is nothing to build a hierarchy from.
        ("_import_bspline", "v2_small.json", "Mode: legacy"),
        # The flat command keeps every knot in plate pixels.
        ("_import_bspline_flat", "v3_real_cut.json", "Mode: legacy"),
    ],
)
def test_menu_import_mode(fake_nuke, monkeypatch, fixtures_dir, command, fixture, expected):
    assert _run(fake_nuke, monkeypatch, command, fixtures_dir / fixture) == expected
