"""
Frontier learning over the two-client sync, driven only by Hypothesis.

A level is a class of problem with fixed attributes, low to high. A problem is one example drawn by Hypothesis.
Each level reports passes over total (s of n), and its regret 1[s>=1] - s/n: zero when mastered or impossible,
positive when it only sometimes works. Work goes to the level with the highest regret; a change stays only when
that level's rate rises and no lower level drops.

One no-argument entry:  pixi run frontier
Problems per level:     FRONTIER_N (default 8, the window)
Result file:            frontier-results.json (read by the CHANGELOG entry, not committed)

Problems never raise: a failure is recorded with its reason so the whole table is measured in one run.
The last test fails when any level is below n/n, so CI is red until every level is mastered.
"""
import json
import os
from collections import OrderedDict
from pathlib import Path

from hypothesis import HealthCheck, Phase, given, settings, strategies as st

from tests.hypo.harness import wait_until
from tests.hypo.test_two_clients import (
    ADD,
    DELETE_ALL,
    close_session,
    expected_empty,
    fresh,
    locations,
    names,
    state,
)

N = int(os.environ.get("FRONTIER_N", "8"))
LEDGER = OrderedDict()  # level -> list of (ok, reason)

SETTINGS = settings(
    max_examples=N,
    deadline=None,
    database=None,
    suppress_health_check=list(HealthCheck),
    phases=[Phase.generate],
)

MOVE = "o = bpy.data.objects[{name!r}]; o.location = {loc}; o.update_tag(); bpy.context.view_layer.update()"


def attempt(level, problem):
    """Run one problem. A failure is data, not an exception; a dead worker is replaced for the next problem."""
    LEDGER.setdefault(level, [])
    try:
        problem()
        LEDGER[level].append((True, ""))
    except (AssertionError, RuntimeError, OSError, KeyError, ValueError) as e:
        LEDGER[level].append((False, f"{type(e).__name__}: {str(e)[:140]}"))
        if isinstance(e, (RuntimeError, OSError)):
            close_session()


def converged(s, expected):
    return wait_until(lambda: state(s["a"]) == expected == state(s["b"]), [s["a"], s["b"]])


@SETTINGS
@given(name=names, loc=locations)
def test_level_1_create_a_to_b(name, loc):
    def problem():
        s = fresh()
        s["a"].call("exec", code=ADD.format(loc=loc, name=name))
        assert converged(s, {name: expected_empty(loc)}), (state(s["a"]), state(s["b"]))

    attempt("1 create A to B", problem)


@SETTINGS
@given(name=names, loc=locations)
def test_level_2_create_b_to_a(name, loc):
    def problem():
        s = fresh()
        s["b"].call("exec", code=ADD.format(loc=loc, name=name))
        assert converged(s, {name: expected_empty(loc)}), (state(s["a"]), state(s["b"]))

    attempt("2 create B to A", problem)


@SETTINGS
@given(name=names, loc=locations)
def test_level_3_delete_replicates(name, loc):
    def problem():
        s = fresh()
        s["a"].call("exec", code=ADD.format(loc=loc, name=name))
        assert converged(s, {name: expected_empty(loc)}), "create did not converge"
        s["a"].call("exec", code=DELETE_ALL)
        assert converged(s, {}), (state(s["a"]), state(s["b"]))

    attempt("3 delete replicates", problem)


@SETTINGS
@given(pair=st.lists(names, min_size=2, max_size=2, unique=True), loc=locations)
def test_level_4_create_after_delete(pair, loc):
    first, second = pair

    def problem():
        s = fresh()
        s["a"].call("exec", code=ADD.format(loc=loc, name=first))
        assert converged(s, {first: expected_empty(loc)}), "first create did not converge"
        s["a"].call("exec", code=DELETE_ALL)
        assert converged(s, {}), "delete did not converge"
        s["a"].call("exec", code=ADD.format(loc=loc, name=second))
        assert converged(s, {second: expected_empty(loc)}), (state(s["a"]), state(s["b"]))

    attempt("4 create after delete", problem)


@SETTINGS
@given(pair=st.lists(names, min_size=2, max_size=2, unique=True), who=st.permutations(["a", "b"]), loc=locations)
def test_level_5_two_creators_in_turn(pair, who, loc):
    def problem():
        s = fresh()
        expected = {}
        for key, name in zip(who, pair):
            s[key].call("exec", code=ADD.format(loc=loc, name=name))
            expected[name] = expected_empty(loc)
            assert converged(s, expected), (state(s["a"]), state(s["b"]))

    attempt("5 two creators, in turn", problem)


@SETTINGS
@given(pair=st.lists(names, min_size=2, max_size=2, unique=True), who=st.permutations(["a", "b"]), loc=locations)
def test_level_6_two_creators_back_to_back(pair, who, loc):
    def problem():
        s = fresh()
        for key, name in zip(who, pair):
            s[key].call("exec", code=ADD.format(loc=loc, name=name))
        expected = {name: expected_empty(loc) for name in pair}
        assert converged(s, expected), (state(s["a"]), state(s["b"]))

    attempt("6 two creators, back to back", problem)


@SETTINGS
@given(name=names, loc=locations, moved=locations)
def test_level_7_move_replicates(name, loc, moved):
    def problem():
        s = fresh()
        s["a"].call("exec", code=ADD.format(loc=loc, name=name))
        assert converged(s, {name: expected_empty(loc)}), "create did not converge"
        s["a"].call("exec", code=MOVE.format(name=name, loc=moved))
        assert converged(s, {name: expected_empty(moved)}), (state(s["a"]), state(s["b"]))

    attempt("7 move replicates", problem)


@SETTINGS
@given(name=names, loc=locations)
def test_level_8_control_outside_room_sees_nothing(name, loc):
    def problem():
        s = fresh()
        s["a"].call("exec", code=ADD.format(loc=loc, name=name))
        assert converged(s, {name: expected_empty(loc)}), "B never got the object"
        s["c"].call("pump", seconds=1)
        assert state(s["c"]) == {}, state(s["c"])

    attempt("8 control: C outside the room", problem)


def regret(s, n):
    return (1 if s >= 1 else 0) - s / n if n else 0.0


def test_zz_frontier_report():
    rows = []
    for level, results in LEDGER.items():
        s, n = sum(ok for ok, _ in results), len(results)
        reasons = sorted({r for ok, r in results if not ok})
        rows.append({"level": level, "s": s, "n": n, "regret": regret(s, n), "reasons": reasons})
    print("\nFRONTIER  level                          s/n    regret")
    for r in rows:
        print(f"FRONTIER  {r['level']:<30} {r['s']}/{r['n']}   {r['regret']:.2f}")
    print("FRONTIER  highest regret first:", [r["level"] for r in sorted(rows, key=lambda r: -r["regret"])][:3])
    for r in rows:
        for reason in r["reasons"][:2]:
            print(f"FRONTIER  {r['level']}: {reason}")
    Path("frontier-results.json").write_text(json.dumps(rows, indent=1))
    assert rows, "no level ran: the table is unmeasured"
    low = [r["level"] for r in rows if r["s"] < r["n"]]
    assert not low, f"levels below n/n: {low}"
