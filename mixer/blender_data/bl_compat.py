# GPLv3 License
#
# Copyright (C) 2020 Ubisoft
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
Blender version compatibility layer for ``bpy.types``.

``T`` behaves like ``bpy.types`` but:

- types renamed in recent Blender releases (e.g. ``EffectSequence`` -> ``EffectStrip`` in 5.0) resolve to
  their new name,
- types removed from Blender (e.g. ``GPencilStroke``, ``FaceMap``) resolve to a unique placeholder class so
  that ``singledispatch`` registrations and dictionary keys keep working. A placeholder never matches any
  real Blender object. Use ``is_available()`` to test for a real type.
"""

from __future__ import annotations

from typing import Any, Dict

import bpy.types

# old name -> new name, in the order of introduction. The first existing candidate wins.
_ALIASES = {
    "EffectSequence": "EffectStrip",
    "ImageSequence": "ImageStrip",
    "MaskSequence": "MaskStrip",
    "MetaSequence": "MetaStrip",
    "MovieClipSequence": "MovieClipStrip",
    "MovieSequence": "MovieStrip",
    "SceneSequence": "SceneStrip",
    "SequenceModifiers": "StripModifiers",
    "Sequences": "Strips",
    "SequencesTopLevel": "StripsTopLevel",
}


class _AbsentRna:
    """Stand-in for ``bl_rna`` of a missing type: unique and hashable, so it never collides with a real
    type or with the ``None`` sentinel used by filter sets, and it has no properties."""

    base = None
    properties = ()

    def __init__(self, identifier: str) -> None:
        self.identifier = identifier


class Absent:
    """Base class of placeholders for ``bpy.types`` members missing from the running Blender."""

    bl_rna: Any = None


class _Types:
    def __init__(self) -> None:
        self._absent: Dict[str, type] = {}

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        for candidate in (name, _ALIASES.get(name)):
            if candidate is not None and hasattr(bpy.types, candidate):
                return getattr(bpy.types, candidate)
        placeholder = self._absent.get(name)
        if placeholder is None:
            placeholder = type(f"Absent_{name}", (Absent,), {"bl_rna": _AbsentRna(name)})
            self._absent[name] = placeholder
        return placeholder


T = _Types()


def is_available(t: Any) -> bool:
    """Return False for a placeholder standing for a type missing from this Blender."""
    return not (isinstance(t, type) and issubclass(t, Absent))


class LegacyFloatLayers:
    """
    Stands in for `bm.<elements>.layers.bevel_weight` and `.crease`, which Blender 4.0 removed: those values are float
    attributes now (`bevel_weight_vert`, `bevel_weight_edge`, `crease_edge`). It answers the few calls the mesh codec
    makes on a layer collection: `len`, indexing, `is_singleton`, `new` and `verify`.
    """

    is_singleton = True

    def __init__(self, float_layers, attribute: str):
        self._float_layers = float_layers
        self._attribute = attribute

    def _layer(self):
        return self._float_layers.get(self._attribute)

    def __len__(self):
        return 0 if self._layer() is None else 1

    def __getitem__(self, index):
        layer = self._layer()
        if layer is None or index != 0:
            raise IndexError(index)
        return layer

    def new(self):
        return self._float_layers.new(self._attribute)

    def verify(self):
        return self._layer() or self._float_layers.new(self._attribute)


def float_layers(elements, legacy: str, attribute: str):
    """The layer collection for per element floats that older Blender kept under `layers.<legacy>`."""
    layers = elements.layers
    return getattr(layers, legacy) if hasattr(layers, legacy) else LegacyFloatLayers(layers.float, attribute)
