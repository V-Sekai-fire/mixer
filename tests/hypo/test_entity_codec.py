"""
Hypothesis tests of the entity messages in Mixer's codec (ENTITY_STATE, ENTITY_REMOVE). No Blender needed.

GOLDEN:    the 64 packets of contract-entity-packet unpack to the columns the contract lists, and pack back to the same bytes.
INVARIANT: for any entity, pack then unpack gives the entity; through a Command and the codec registry too; the scale and
           mesh digest in the payload survive; scale is within 1.1% of what was asked inside 2**-8 .. 2**8.
CONTROL:   a short or long packet, a nonzero reserved tail and an out of range field are refused, never truncated.
"""

import csv
import os

import pytest
from hypothesis import given, settings, strategies as st

from mixer import codec
from mixer.blender_client import codec as blender_codec
from mixer.blender_client.messages import EntityRemoveMessage, EntityStateMessage
from mixer.broadcaster import common, entity
from mixer.broadcaster.common import MessageType
from mixer.broadcaster.entity import Entity

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "packet_golden_full.csv")

i16 = st.integers(-32768, 32767)
i64 = st.integers(-(2**63), 2**63 - 1)
u32 = st.integers(0, 2**32 - 1)
u64 = st.integers(0, 2**64 - 1)
entities = st.builds(
    Entity,
    gid=u32,
    pos=st.tuples(i64, i64, i64),
    vel=st.tuples(i16, i16, i16),
    accel=st.tuples(i16, i16, i16),
    hlc=u64,
    class_owner=u32,
    sub_index=u32,
    rot=st.tuples(i16, i16, i16),
    payload=st.binary(min_size=36, max_size=36),
)


@pytest.fixture(autouse=True)
def registered():
    blender_codec.register()
    yield
    blender_codec.unregister()


def rows():
    with open(FIXTURE, newline="") as f:
        return list(csv.DictReader(f))


def test_golden_the_contract_packets():
    got = rows()
    assert len(got) == 64
    for r in got:
        raw = bytes.fromhex(r["hex"])
        e = Entity.unpack(raw)
        assert e.gid == int(r["gid"])
        assert e.pos == (int(r["pumx"]), int(r["pumy"]), int(r["pumz"]))
        assert e.vel == (int(r["velx"]), int(r["vely"]), int(r["velz"]))
        assert e.accel == (int(r["accx"]), int(r["accy"]), int(r["accz"]))
        assert e.hlc == int(r["hlc"])
        assert (e.class_owner, e.sub_index) == (int(r["class_owner"]), int(r["sub_index"]))
        assert e.rot == (int(r["rotx"]), int(r["roty"]), int(r["rotz"]))
        assert e.pack() == raw


@settings(max_examples=300, deadline=None)
@given(entities)
def test_invariant_pack_unpack_and_through_the_codec(e):
    raw = e.pack()
    assert len(raw) == entity.PACKET_SIZE
    assert Entity.unpack(raw) == e
    wire = common.Command(MessageType.ENTITY_STATE, codec.encode(EntityStateMessage(e))).to_byte_buffer()
    # a command on the wire: size u64, id u32, type u16, then the data
    size, cid, mtype = (
        int.from_bytes(wire[:8], "little"),
        int.from_bytes(wire[8:12], "little"),
        int.from_bytes(wire[12:14], "little"),
    )
    assert (size, mtype) == (entity.PACKET_SIZE, MessageType.ENTITY_STATE.value)
    back = common.Command(MessageType(mtype), wire[14:], cid)
    assert codec.decode(back) == EntityStateMessage(e)


@settings(max_examples=200, deadline=None)
@given(u32)
def test_invariant_remove_round_trips(gid):
    buf = codec.encode(EntityRemoveMessage(gid))
    assert len(buf) == 4
    assert codec.decode_as(MessageType.ENTITY_REMOVE, buf) == EntityRemoveMessage(gid)


@settings(max_examples=300, deadline=None)
@given(st.binary(min_size=32, max_size=32), st.tuples(*[st.floats(2.0**-8, 2.0**8)] * 3))
def test_invariant_scale_and_mesh_survive_the_payload(digest, scale):
    got_digest, got_scale = entity.unpack_payload(entity.pack_payload(digest, scale))
    assert got_digest == digest
    for want, got in zip(scale, got_scale):
        assert abs(got / want - 1.0) < 0.012  # steps of 2**(1/64) are 1.09%, half of that either way


def test_invariant_scale_one_is_exact_and_out_of_range_is_clamped():
    assert entity.decode_scale(entity.encode_scale(1.0, 1.0, 1.0)) == (1.0, 1.0, 1.0)
    lo, hi = entity.decode_scale(entity.encode_scale(1e-9, 1e9, 1.0))[0:2]
    assert lo == pytest.approx(2.0**-8) and hi == pytest.approx(2.0**7.984375)


def test_control_bad_packets_are_refused():
    good = Entity(gid=1).pack()
    for bad in (good[:-1], good + b"\0", b"", bytes(99), bytes(101)):
        with pytest.raises(ValueError):
            Entity.unpack(bad)
    with pytest.raises(ValueError):
        Entity.unpack(good[:98] + b"\1\0")  # a nonzero reserved tail
    with pytest.raises(ValueError):
        Entity.unpack(good[:98] + b"\0\1")


def test_control_out_of_range_fields_are_refused_not_truncated():
    for kwargs in (
        {"gid": 2**32},
        {"gid": -1},
        {"pos": (2**63, 0, 0)},
        {"vel": (32768, 0, 0)},
        {"rot": (0, 0, -32769)},
        {"hlc": 2**64},
        {"class_owner": 2**32},
        {"payload": bytes(35)},
        {"payload": bytes(37)},
    ):
        with pytest.raises(ValueError):
            Entity(**{"gid": 1, **kwargs}).pack()


def test_control_the_messages_are_registered_and_a_cleared_registry_refuses():
    assert codec.is_registered(MessageType.ENTITY_STATE) and codec.is_registered(MessageType.ENTITY_REMOVE)
    blender_codec.unregister()
    assert not codec.is_registered(MessageType.ENTITY_STATE)
    with pytest.raises(NotImplementedError):
        codec.decode_as(MessageType.ENTITY_STATE, Entity(gid=1).pack())
    blender_codec.register()


@settings(max_examples=100, deadline=None)
@given(entities, st.integers(0, entity.PACKET_SIZE - 1), st.integers(1, 255))
def test_control_flipping_a_reserved_byte_is_refused_and_other_bytes_change_the_entity(e, i, x):
    raw = bytearray(e.pack())
    raw[i] ^= x
    if i >= 98:
        with pytest.raises(ValueError):
            Entity.unpack(bytes(raw))
    else:
        assert Entity.unpack(bytes(raw)) != e


# ---- a command inside the packet: 78 bytes of data in the bytes a pose would use, bulk data by digest ----


class MemoryStore:
    """Stands in for SeaweedFS and desync: data stored under its SHA-256."""

    def __init__(self):
        self.blobs = {}
        self.puts = 0

    def put(self, data):
        self.puts += 1
        digest = __import__("hashlib").sha256(data).digest()
        self.blobs[digest] = data
        return digest

    def get(self, digest):
        return self.blobs[digest]


types = st.integers(1, 0xFFFF).filter(lambda t: t != entity.POSE)
datas = st.one_of(st.binary(max_size=entity.INLINE_MAX), st.binary(min_size=entity.INLINE_MAX + 1, max_size=3000))


def test_golden_the_layout_matches_the_lean_source():
    # EntityPacket/Classes.lean: COMMAND_DATA_A at 4 for 36, COMMAND_DATA_B at 56 for 42, class 128
    assert (entity.DATA_A, entity.DATA_B, entity.RUN_LEN, entity.CLASS_COMMAND) == ((4, 36), (56, 42), 78, 128)
    raw = entity.pack_command(101, bytes(range(1, 78)), 9, MemoryStore().put, hlc=7)
    assert int.from_bytes(raw[48:52], "little") == (128 << 24) | 9
    assert int.from_bytes(raw[52:56], "little") == (78 << 16) | 101  # used 78 bytes of the run, type 101
    assert raw[40:48] == (7).to_bytes(8, "little") and raw[98:] == b"\0\0"
    assert raw[4] == 0  # inline mode
    assert raw[5:40] == bytes(range(1, 36)) and raw[56:98] == bytes(
        range(36, 78)
    )  # the run continues in the second part


@settings(max_examples=300, deadline=None)
@given(types, datas, st.integers(0, 2**24 - 1), u64)
def test_invariant_a_command_round_trips_as_one_packet(t, data, owner, hlc):
    store = MemoryStore()
    raw = entity.pack_command(t, data, owner, store.put, hlc)
    assert len(raw) == entity.PACKET_SIZE  # always one packet
    assert store.puts == (0 if len(data) <= entity.INLINE_MAX else 1)  # bulk only when it does not fit
    assert int.from_bytes(raw[40:48], "little") == hlc
    assert entity.unpack_command(raw, store.get) == (t, owner, data)


@given(types, st.binary(min_size=entity.INLINE_MAX, max_size=entity.INLINE_MAX + 1))
def test_golden_the_inline_boundary_is_77_bytes(t, data):
    store = MemoryStore()
    entity.pack_command(t, data, 1, store.put)
    assert (store.puts == 0) == (len(data) == entity.INLINE_MAX)


@settings(max_examples=200, deadline=None)
@given(types, st.text(min_size=1, max_size=20), st.binary(max_size=40), st.binary(max_size=40))
def test_invariant_a_command_is_keyed_by_type_and_leading_name(t, name, tail1, tail2):
    lead = common.encode_string(name)
    assert entity.command_key(t, lead + tail1) == entity.command_key(t, lead + tail2)  # same name: one key


def test_control_commands_that_do_not_fit_or_are_malformed_are_refused():
    store = MemoryStore()
    for t in (0, 210, 0x10000, -1):
        with pytest.raises(ValueError):
            entity.pack_command(t, b"x", 1, store.put)
    with pytest.raises(ValueError):
        entity.pack_command(101, b"x", 2**24, store.put)
    with pytest.raises(ValueError):
        entity.pack_command(101, bytes(100), 1, lambda d: b"short")
    big = entity.pack_command(101, bytes(100), 1, store.put)
    with pytest.raises(ValueError):  # the chunk store returns the wrong data
        entity.unpack_command(big, lambda d: bytes(99))
    ok = entity.pack_command(101, b"abc", 1, store.put)

    def edit(raw, at, value):
        b = bytearray(raw)
        b[at : at + len(value)] = value
        return bytes(b)

    for bad in (
        edit(ok, 60, b"\1"),  # a nonzero byte past the used length, in the second part
        edit(ok, 20, b"\1"),  # and in the first
        edit(ok, 99, b"\1"),  # the reserved tail
        edit(ok, 51, b"\4"),  # another class
        edit(ok, 52, (210).to_bytes(2, "little")),  # a pose is not a command
        edit(ok, 52, bytes(2)),  # type 0
        edit(ok, 54, bytes(2)),  # nothing used
        edit(ok, 54, (79).to_bytes(2, "little")),  # more than the run
        edit(ok, 4, b"\2"),  # unknown mode
        ok[:-1],
        ok + b"\0",
    ):
        with pytest.raises(ValueError):
            entity.unpack_command(bad, store.get)


# ---- a pose from a Blender transform ----

import math  # noqa: E402

from hypothesis import assume  # noqa: E402

units = st.floats(-1.0, 1.0, allow_nan=False)
unit_quats = (
    st.tuples(units, units, units, units)
    .filter(lambda q: sum(c * c for c in q) > 0.05)
    .map(lambda q: tuple(c / math.sqrt(sum(d * d for d in q)) for c in q))
)
metres = st.floats(-1000.0, 1000.0, allow_nan=False)
scales = st.floats(2.0**-7, 2.0**7)


def test_golden_blender_axes_map_to_the_wire_frame():
    # Blender (1, 2, 3): x right, y forward, z up. On the wire: x right, y up, -z forward.
    e = entity.pose_entity(1, (1.0, 2.0, 3.0), (1.0, 0.0, 0.0, 0.0), (1, 1, 1), 0)
    assert e.pos == (1_000_000, 3_000_000, -2_000_000)
    assert e.rot == (0, 0, 0) and e.class_owner >> 24 == entity.CLASS_PROP
    # a quarter turn about Blender's up axis (z) is a quarter turn about the wire's up axis (y): pure twist
    h = math.sqrt(0.5)
    e = entity.pose_entity(1, (0, 0, 0), (h, 0.0, 0.0, h), (1, 1, 1), 0)
    assert e.rot[:2] == (0, 0) and abs(e.rot[2] - 16384) <= 1


@settings(max_examples=300, deadline=None)
@given(
    u32,
    st.tuples(metres, metres, metres),
    unit_quats,
    st.tuples(scales, scales, scales),
    u64,
    st.integers(0, 2**24 - 1),
)
def test_invariant_a_pose_survives_its_packet(gid, loc, q, scale, hlc, owner):
    e = entity.pose_entity(gid, loc, q, scale, hlc, owner)
    back = Entity.unpack(e.pack())
    assert back == e
    x, y, z = loc
    assert all(abs(got - want * 1e6) <= 0.5 + 1e-6 for got, want in zip(back.pos, (x, z, -y)))
    want = entity.to_wire_quat(*q)
    got = entity.quat_of_codes(*back.rot)
    assert entity.angle_between(want, got) < math.radians(0.02)  # i16 codes over a half turn: 0.0055 degrees per step
    _, s = back.mesh()
    for want_s, got_s in zip(scale, s):
        assert abs(got_s / want_s - 1.0) < 0.012


@settings(max_examples=100, deadline=None)
@given(u32, st.tuples(metres, metres, metres), unit_quats)
def test_control_a_wrong_axis_map_would_be_seen(gid, loc, q):
    e = entity.pose_entity(gid, loc, q, (1, 1, 1), 0)
    x, y, z = loc
    assume(abs(y) > 0.01 or abs(z) > 0.01)
    assume(abs(y - z) > 0.01)  # swapping y and z must change the packet
    assert e.pos != (round(x * 1e6), round(y * 1e6), round(z * 1e6))


def test_control_bad_inputs_are_refused_not_wrapped():
    with pytest.raises(ValueError):
        entity.pose_entity(2**32, (0, 0, 0), (1, 0, 0, 0), (1, 1, 1), 0).pack()
    with pytest.raises(ValueError):
        entity.pose_entity(1, (1e13, 0, 0), (1, 0, 0, 0), (1, 1, 1), 0).pack()  # beyond i64 micrometres
    with pytest.raises(ValueError):
        entity.pose_entity(1, (0, 0, 0), (1, 0, 0, 0), (1, 1, 1), 2**64).pack()
    with pytest.raises(ValueError):
        entity.pose_entity(1, (0, 0, 0), (1, 0, 0, 0), (1, 1, 1), 0, owner=2**24).pack()


# ---- a mesh as USD ----

from mixer.broadcaster import geo_publish  # noqa: E402

pts = st.lists(st.tuples(*[st.floats(-100, 100, allow_nan=False)] * 3), min_size=3, max_size=30)


@settings(max_examples=100, deadline=None)
@given(pts, st.data())
def test_invariant_usda_points_are_the_wire_frame_and_indices_valid(points, data):
    tris = data.draw(st.lists(st.tuples(*[st.integers(0, len(points) - 1)] * 3), max_size=20))
    text = geo_publish.usda("M", points, tris).decode()
    got = [
        tuple(float(c) for c in p.split(", "))
        for p in text.split("point3f[] points = [(")[1].split(")]")[0].split("), (")
    ]
    assert len(got) == len(points)
    for (x, y, z), (a, b, c) in zip(points, got):
        assert abs(a - x) < 1e-5 and abs(b - z) < 1e-5 and abs(c + y) < 1e-5
    assert text.count(" 3,") + text.count("[3]") + text.count("[3,") >= (1 if tris else 0)


def test_control_usda_refuses_a_triangle_off_the_end():
    with pytest.raises(ValueError):
        geo_publish.usda("M", [(0, 0, 0)] * 3, [(0, 1, 3)])
    with pytest.raises(ValueError):
        geo_publish.usda("M", [(0, 0, 0)] * 3, [(0, 1, -1)])
