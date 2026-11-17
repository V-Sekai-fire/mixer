"""
Hypothesis tests of the add-on publishing a large mesh in pieces (headless Blender, a fake store).
GOLDEN: a mesh over the limit goes out as several entities that share one pose and have different digests.
INVARIANT: for any size and limit the pieces hold every triangle of the mesh once, and a mesh under the limit is one entity.
CONTROL: with no store configured nothing is published and the object still has its one entity.
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


def probe(n, limit):
    env = dict(os.environ, REPO=REPO, SPLIT=json.dumps({"n": n, "max": limit}))
    out = subprocess.run(
        [BLENDER, "-b", "--factory-startup", "--python", os.path.join(REPO, "tools", "split_probe.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout
    line = [ln for ln in out.splitlines() if ln.startswith("PROBE ")]
    assert line, out[-800:]
    return json.loads(line[0][6:])


def test_golden_a_large_mesh_is_several_entities_with_one_pose():
    r = probe(20, 100)  # 800 triangles, limit 100
    assert r["entities"] >= 8 and r["gids"] == r["entities"] and r["same_pose"] and r["digests"] == r["entities"]
    assert r["tris"] == r["expect"]


@settings(max_examples=6, deadline=None)
@given(n=st.integers(2, 24), limit=st.integers(20, 900))
def test_invariant_every_triangle_once(n, limit):
    r = probe(n, limit)
    assert r["tris"] == r["expect"], r
    if r["expect"] <= limit:
        assert r["entities"] == 1
    else:
        assert r["entities"] >= 2
