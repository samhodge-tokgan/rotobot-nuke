"""Install a fake ``nuke`` + ``nuke.rotopaint`` into ``sys.modules`` so
``rotobot_nuke.importer`` can be imported and exercised outside of Nuke.

The fakes are deliberately NOT :class:`unittest.mock.MagicMock` — the
importer iterates over Layers and Shapes, so we need real sequence
behaviour and class identity. Each fake records call history on
``_log`` so tests can assert against it.
"""

from __future__ import annotations

import sys
import types
from typing import List, Tuple

import pytest


# ---------------------------------------------------------------------------
# Fake Nuke classes


class _KnobAnim:
    def __init__(self) -> None:
        self.keys: List[Tuple[float, Tuple[float, float]]] = []

    def addPositionKey(self, frame, value):
        self.keys.append((frame, tuple(value)))


class _KnobFloat:
    def __init__(self, value=0.0) -> None:
        self.value_ = value
        self.anim_keys: List[Tuple[float, float]] = []

    def setValue(self, v):
        self.value_ = v

    def value(self):
        return self.value_

    def addKey(self, frame, value):
        self.anim_keys.append((frame, value))


class _TranslationAnimCurveSet:
    def __init__(self) -> None:
        self.curves = [_KnobFloat(), _KnobFloat()]

    def getTranslationAnimCurve(self, index):
        return self.curves[index]


class _Transform:
    def __init__(self) -> None:
        self._translation = _TranslationAnimCurveSet()

    def getTranslationAnimCurve(self, index):
        return self._translation.getTranslationAnimCurve(index)


class _ControlPoint:
    def __init__(self, x, y):
        self.initial = (x, y)
        self.center = _KnobAnim()
        self.leftTangent = _KnobAnim()
        self.rightTangent = _KnobAnim()


class _Layer:
    def __init__(self, _curves):
        self.name = ""
        self.children = []

    def __iter__(self):
        return iter(self.children)

    def append(self, child):
        self.children.append(child)


class _Shape:
    def __init__(self, _curves, type="bspline"):
        self.name = ""
        self.curve_type = type
        self.points: List[_ControlPoint] = []
        self.visibility_keys: List[Tuple[int, bool]] = []

    def __iter__(self):
        return iter(self.points)

    def append(self, cp):
        self.points.append(cp)

    def setVisible(self, frame, visible):
        self.visibility_keys.append((int(frame), bool(visible)))


class _CurvesKnob:
    def __init__(self):
        self.rootLayer = _Layer(self)
        self._transform = _Transform()
        self.changed_count = 0

    def changed(self):
        self.changed_count += 1


class _Knob:
    def __init__(self, value=None):
        self._value = value
        self.history: List[object] = []

    def setValue(self, v):
        self._value = v
        self.history.append(v)

    def value(self):
        return self._value


class _Node:
    def __init__(self, name="Roto1"):
        self.name_ = name
        self._knobs = {
            "curves": _CurvesKnob(),
            "label": _Knob(""),
            "fps": _Knob(24.0),
        }

    def __getitem__(self, key):
        if key not in self._knobs:
            self._knobs[key] = _Knob()
        return self._knobs[key]

    def name(self):
        return self.name_


class _Nodes:
    def __init__(self):
        self.created: List[_Node] = []

    def Roto(self, name="Roto"):
        n = _Node(name=name)
        self.created.append(n)
        return n


class _Root:
    """Backing object returned by ``nuke.root()`` — must share state across calls."""

    def __init__(self):
        self._knobs = {"fps": _Knob(24.0), "format": _Knob(None)}

    def __getitem__(self, key):
        return self._knobs.setdefault(key, _Knob())


# One shared root per test — tests mutate `_log` state via it.
_fake_state = {"root": _Root()}


def _nuke_root():
    return _fake_state["root"]


# Root transform access path: ``curves.rootLayer.getTransform()``
_Layer.getTransform = lambda self: self._transform if hasattr(self, "_transform") else self.__dict__.setdefault("_transform", _Transform())  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Module stubs


def _install_fake_nuke():
    """Install ``nuke`` + ``nuke.rotopaint`` into ``sys.modules``.

    Idempotent: safe to call under pytest-xdist or multiple test modules.
    """
    nuke_mod = types.ModuleType("nuke")
    nuke_rotopaint_mod = types.ModuleType("nuke.rotopaint")

    nuke_rotopaint_mod.Layer = _Layer
    nuke_rotopaint_mod.Shape = _Shape
    nuke_rotopaint_mod.AnimControlPoint = _ControlPoint

    nuke_mod.rotopaint = nuke_rotopaint_mod
    nuke_mod.nodes = _Nodes()
    nuke_mod.root = _nuke_root

    def _msg(*_args, **_kw):
        return None

    nuke_mod.tprint = _msg
    nuke_mod.message = _msg
    nuke_mod.getFilename = lambda *a, **k: None
    nuke_mod.getInput = lambda *a, **k: None

    sys.modules["nuke"] = nuke_mod
    sys.modules["nuke.rotopaint"] = nuke_rotopaint_mod
    return nuke_mod


# Install at import time so collection-time `import rotobot_nuke.importer`
# in a test file succeeds.
_install_fake_nuke()


@pytest.fixture
def fake_nuke():
    """Reset mutable state on the shared fake-Nuke module between tests.

    We mutate the existing module rather than reinstalling, because the
    importer module caches ``import nuke`` references at its own import
    time; re-binding ``sys.modules['nuke']`` here would leave the cached
    references pointing at the old instance.
    """
    nuke_mod = sys.modules["nuke"]
    nuke_mod.nodes = _Nodes()
    _fake_state["root"] = _Root()
    return nuke_mod


@pytest.fixture
def fixtures_dir():
    from pathlib import Path

    return Path(__file__).parent / "fixtures"
