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
    assert len(menubar.submenus["Rotobot"].commands) == 5


@pytest.fixture(autouse=True)
def _drop_menu_module():
    yield
    sys.modules.pop("rotobot_nuke.menu", None)
