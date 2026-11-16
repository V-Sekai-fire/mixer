"""
Hypothesis tests: one broadcaster server and three headless Blender clients.
A and B are in one room, C is connected to the server but never joins it.

Three kinds of checks, all driven by Hypothesis:
- GOLDEN:    a fixed @example whose expected end state is written down, not derived.
- INVARIANT: for any generated sequence of edits on either client, A and B converge to the expected scene.
- CONTROL:   C, outside the room, must NOT receive the edits. Proves the checks can fail.

Waits are condition based (poll until converged or timeout), never fixed sleeps.
"""

import atexit

from hypothesis import HealthCheck, Phase, example, given, settings, strategies as st

from tests.hypo.harness import Client, Server, wait_until

_session = {}


def session():
    """Server and clients are slow to start: share them across examples. Each example resets the scene."""
    if not _session:
        server = Server()
        _session.update(server=server)
        atexit.register(close_session)
        for key, name in (("a", "A"), ("b", "B"), ("c", "C")):
            _session[key] = Client(server, name)
        _session["a"].call("join", room="hypo", create=True)
        _session["b"].call("join", room="hypo", pump=2)
        # C stays connected to the server but never joins the room
    return _session


def close_session():
    for k in ("a", "b", "c"):
        if k in _session:
            _session[k].close()
    if "server" in _session:
        _session["server"].close()
    _session.clear()


def state(c):
    return {k: v for k, v in c.call("snapshot")["objects"].items() if k.startswith("hy_")}


DELETE_ALL = (
    "objs = [o for o in bpy.data.objects if o.name.startswith('hy_')]\n"
    "if objs:\n"
    "    bpy.ops.object.select_all(action='DESELECT')\n"
    "    [o.select_set(True) for o in objs]\n"
    "    bpy.context.view_layer.objects.active = objs[0]\n"
    "    bpy.ops.object.delete()\n"
)


def fresh():
    """Remove every test object on A and wait until A and B are both empty."""
    s = session()
    # delete through the operator, as a user does: bpy.data.objects.remove() fires no depsgraph update, so Mixer
    # (which diffs on depsgraph updates) would not see it
    s["a"].call("exec", code=DELETE_ALL)
    assert wait_until(lambda: not state(s["a"]) and not state(s["b"]), [s["a"], s["b"]]), "reset did not converge"
    return s


def expected_empty(loc):
    return {"type": "EMPTY", "loc": [float(v) for v in loc], "scale": [1.0, 1.0, 1.0], "parent": None}


SETTINGS = settings(
    max_examples=5,
    deadline=None,
    suppress_health_check=list(HealthCheck),
    phases=[Phase.explicit, Phase.reuse, Phase.generate],  # an example costs seconds: no shrinking
)

# A script rename fires no depsgraph update (a rename in the UI does), and Mixer finds renames in the depsgraph
# handler: tag the object as the UI would, or the creation is sent as "Empty" and never renamed.
ADD = (
    "bpy.ops.object.empty_add(location={loc}); o = bpy.context.object; o.name = {name!r}; "
    "o.update_tag(); bpy.context.view_layer.update()"
)
names = st.text(alphabet="abcdefghij", min_size=1, max_size=6).map(lambda s: "hy_" + s)
locations = st.tuples(*[st.integers(-50, 50)] * 3)


@SETTINGS
@given(name=st.just("hy_golden"), loc=st.just((1, 2, 3)))
@example(name="hy_golden", loc=(1, 2, 3))
def test_golden_empty_replicates(name, loc):
    s = fresh()
    s["a"].call("exec", code=ADD.format(loc=loc, name=name))
    golden = {"hy_golden": {"type": "EMPTY", "loc": [1.0, 2.0, 3.0], "scale": [1.0, 1.0, 1.0], "parent": None}}
    assert wait_until(lambda: state(s["b"]) == golden, [s["a"], s["b"]]), state(s["b"])


@SETTINGS
@given(
    edits=st.lists(
        st.tuples(st.sampled_from(["a", "b"]), names, locations), min_size=1, max_size=4, unique_by=lambda e: e[1]
    )
)
def test_invariant_clients_converge(edits):
    s = fresh()
    for who, name, loc in edits:
        s[who].call("exec", code=ADD.format(loc=loc, name=name))
    expected = {name: expected_empty(loc) for _, name, loc in edits}
    ok = wait_until(lambda: state(s["a"]) == expected == state(s["b"]), [s["a"], s["b"]])
    assert ok, (state(s["a"]), state(s["b"]), expected)


@SETTINGS
@given(name=names, loc=locations)
def test_control_client_outside_room_sees_nothing(name, loc):
    s = fresh()
    s["a"].call("exec", code=ADD.format(loc=loc, name=name))
    assert wait_until(lambda: name in state(s["b"]), [s["a"], s["b"]]), "B never got the object"
    s["c"].call("pump", seconds=2)
    assert name not in state(s["c"])
