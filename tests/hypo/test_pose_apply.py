"""
Hypothesis tests of a pose arriving from the room (Godot, three.js, another Blender) and moving a Blender object.
GOLDEN: a pose for an object elsewhere puts it at the sent location, rotation and scale.
INVARIANT: for any location, rotation and scale within the wire's range, the object lands within the wire's rounding
  (1 um position, 0.01 rad rotation, 1.5 % scale), and the add-on does not send that pose back.
CONTROL: a pose for an id that no object has changes nothing and says so.
"""

import json
import os
import shutil
import subprocess

import pytest
from hypothesis import given, settings, strategies as st

BLENDER = os.environ.get("BLENDER", "/Applications/Blender.app/Contents/MacOS/Blender")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
pytestmark = pytest.mark.skipif(not (os.path.exists(BLENDER) or shutil.which(BLENDER)), reason="Blender not installed")


def apply(loc, euler, scale, ghost=False):
    env = dict(os.environ, REPO=REPO, POSE=json.dumps({"loc": loc, "euler": euler, "scale": scale, "ghost": ghost}))
    out = subprocess.run(
        [BLENDER, "-b", "--factory-startup", "--python", os.path.join(REPO, "tools", "pose_apply_probe.py")],
        env=env, capture_output=True, text=True, timeout=120,
    ).stdout
    line = [ln for ln in out.splitlines() if ln.startswith("PROBE ")]
    assert line, out[-600:]
    return json.loads(line[0][6:])


def test_golden_a_pose_moves_the_object():
    r = apply([1.5, -2.0, 0.5], [0.3, 0.0, 1.0], [2.0, 1.0, 0.5])
    assert r["applied"] and r["pos"] < 1e-5 and r["rot"] < 0.01 and r["scale"] < 0.015 and r["echoed"] == 0


@settings(max_examples=10, deadline=None)
@given(
    loc=st.lists(st.floats(-500, 500), min_size=3, max_size=3),
    euler=st.lists(st.floats(-3.1, 3.1), min_size=3, max_size=3),
    scale=st.lists(st.sampled_from([0.25, 0.5, 1.0, 1.7, 3.0]), min_size=3, max_size=3),
)
def test_invariant_lands_where_sent_and_is_not_echoed(loc, euler, scale):
    r = apply(loc, euler, scale)
    assert r["applied"] and r["pos"] < 1e-4 and r["rot"] < 0.01 and r["scale"] < 0.015 and r["echoed"] == 0, r


def test_control_a_pose_for_an_unknown_id_changes_nothing():
    r = apply([1.5, -2.0, 0.5], [0.3, 0.0, 1.0], [2.0, 1.0, 0.5], ghost=True)
    assert r["applied"] is False and r["moved"] is False
