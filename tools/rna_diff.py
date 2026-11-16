"""Compare Blender RNA schema snapshots (made by tools/rna_schema.py) and check what the add-on uses.

  python tools/rna_diff.py diff schemas/4.2.json schemas/5.3.json
  python tools/rna_diff.py usage mixer schemas/2.93.json schemas/3.6.json ...

Plain Python, no bpy. `usage` exits 1 when the add-on references a bpy.types / bpy.data name that a snapshot lacks and
mixer/blender_data/bl_compat.py does not account for.
"""
import gzip
import json
import re
import sys
from pathlib import Path


def load(path):
    p = Path(path)
    opener = gzip.open if p.suffix == ".gz" else open
    with opener(p, "rt") as f:
        return json.load(f)


def diff(old, new):
    o, n = old["structs"], new["structs"]
    out = {"removed": sorted(o.keys() - n.keys()), "added": sorted(n.keys() - o.keys()), "props": {}}
    for name in sorted(o.keys() & n.keys()):
        po, pn = o[name]["props"], n[name]["props"]
        changes = {}
        for k in sorted(po.keys() - pn.keys()):
            changes[k] = "removed"
        for k in sorted(pn.keys() - po.keys()):
            changes[k] = "added"
        for k in sorted(po.keys() & pn.keys()):
            if po[k] != pn[k]:
                changes[k] = {"was": po[k], "now": pn[k]}
        if changes:
            out["props"][name] = changes
    out["bpy_data_removed"] = sorted(set(old["bpy_data"]) - set(new["bpy_data"]))
    out["bpy_data_added"] = sorted(set(new["bpy_data"]) - set(old["bpy_data"]))
    return out


SHIM_USE = re.compile(r"(?<![\w.])T\.([A-Z][A-Za-z0-9_]*)")  # through bl_compat: absent => placeholder
DIRECT_USE = re.compile(r"\bbpy\.types\.([A-Z][A-Za-z0-9_]*)")  # unprotected: absent => AttributeError
DATA_USE = re.compile(r"\bbpy\.data\.([a-z_]+)")


def used_names(src_dir):
    shim, direct, data = {}, {}, {}
    for path in sorted(Path(src_dir).rglob("*.py")):
        if "/tests/" in str(path):
            continue
        for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            for m in SHIM_USE.finditer(line):
                shim.setdefault(m.group(1), f"{path}:{lineno}")
            for m in DIRECT_USE.finditer(line):
                direct.setdefault(m.group(1), f"{path}:{lineno}")
            for m in DATA_USE.finditer(line):
                data.setdefault(m.group(1), f"{path}:{lineno}")
    return shim, direct, data


def compat_names(src_dir):
    p = Path(src_dir) / "blender_data" / "bl_compat.py"
    return set(re.findall(r"[A-Z][A-Za-z0-9_]+", p.read_text())) if p.exists() else set()


def usage(src_dir, snapshots):
    """Exit 1 only for names that would break: direct bpy.types.X or bpy.data.X absent from a version.
    Names used through the T shim are reported as features that are silently off on that version."""
    shim, direct, data = used_names(src_dir)
    compat = compat_names(src_dir)
    breaks = 0
    for snap in snapshots:
        s = load(snap)
        off = sorted(t for t in shim if t not in s["structs"])
        broken = sorted(t for t in direct if t not in s["structs"] and t not in compat)
        gone = sorted(d for d in data if d not in s["bpy_data"] and d in ALL_COLLECTIONS_EVER)
        print(f"{s['blender']}: {len(broken) + len(gone)} breaks, {len(off)} shimmed types absent")
        for t in broken:
            print(f"   BREAK bpy.types.{t}   at {direct[t]}")
        for d in gone:
            print(f"   BREAK bpy.data.{d}   at {data[d]}")
        if off:
            print("   off: " + ", ".join(off))
        breaks += len(broken) + len(gone)
    return 1 if breaks else 0


ALL_COLLECTIONS_EVER = set()


def main(argv):
    global ALL_COLLECTIONS_EVER
    if len(argv) >= 3 and argv[0] == "diff":
        d = diff(load(argv[1]), load(argv[2]))
        print(f"types removed {len(d['removed'])}, added {len(d['added'])}, structs with property changes {len(d['props'])}")
        print("bpy.data collections removed:", d["bpy_data_removed"], "added:", d["bpy_data_added"])
        for name in d["removed"]:
            print("  - type", name)
        return 0
    if len(argv) >= 3 and argv[0] == "usage":
        for snap in argv[2:]:
            ALL_COLLECTIONS_EVER |= set(load(snap)["bpy_data"])
        return usage(argv[1], argv[2:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
