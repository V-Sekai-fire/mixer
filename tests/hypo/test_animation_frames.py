"""
Hypothesis tests of what the add-on sends while an animation plays (headless Blender, one process per example).
GOLDEN: an object keyed to move is sent as a pose on frames where its matrix changed.
INVARIANT: for any keyframes, poses sent == frames where the world matrix changed from the frame before (the first
  frame counts), and no mesh is ever published by playback.
CONTROL: an object without animation sends nothing; nothing is sent for an object not yet shared (the depsgraph path
  announces it with its mesh).
"""

import json
import os
import shutil
import subprocess

import pytest
from hypothesis import given, settings, strategies as st

BLENDER = os.environ.get("BLENDER", "/Applications/Blender.app/Contents/MacOS/Blender")
REPO = os.path.join(os.path.dirname(__file__), "..", "..")
pytestmark = pytest.mark.skipif(not (os.path.exists(BLENDER) or shutil.which(BLENDER)), reason="Blender not installed")


def play(keys, frames, shared=True):
    env = dict(os.environ, REPO=os.path.abspath(REPO), ANIM=json.dumps({"keys": keys, "frames": frames, "shared": shared}))
    out = subprocess.run(
        [BLENDER, "-b", "--factory-startup", "--python", os.path.join(REPO, "tools", "anim_probe.py")],
        env=env, capture_output=True, text=True, timeout=120,
    ).stdout
    line = [ln for ln in out.splitlines() if ln.startswith("PROBE ")]
    assert line, out[-600:]
    return json.loads(line[0][6:])


def test_golden_a_moving_object_is_sent_when_it_moves():
    r = play([[1, 0.0], [5, 2.0]], 8)
    assert r["moved"] == 2 and r["mover"] == r["moved"] and r["published"] == 0


def test_control_still_and_unshared_send_nothing():
    r = play([[1, 0.0], [4, 3.0]], 6)
    assert r["still"] == 0
    r = play([[1, 0.0], [4, 3.0]], 6, shared=False)
    assert r["mover"] == 0 and r["state"] == 0


@settings(max_examples=8, deadline=None)
@given(
    keys=st.lists(st.tuples(st.integers(1, 12), st.sampled_from([0.0, 1.0, 2.5, -3.0])), min_size=1, max_size=5, unique_by=lambda k: k[0]),
    frames=st.integers(2, 12),
)
def test_invariant_one_pose_per_change(keys, frames):
    r = play([list(k) for k in keys], frames)
    assert r["mover"] == r["moved"] and r["still"] == 0 and r["published"] == 0
