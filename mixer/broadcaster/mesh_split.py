# MIT License. Copyright (c) 2026-present K. S. Ernest (iFire) Lee
# (Permission notice as in the other files of this package.)
"""
Cuts a triangle mesh into pieces of at most `max_tris` triangles, so each piece is a small mesh a viewer can fetch at once.
No `bpy`, no numpy: lists of points and (a, b, c) index triples.

The rule follows what the project already does with models:
  1. shells (model_explode's explode.gd): triangles that share a vertex, vertices welded by position, are one shell; nothing
     is cut, so every piece edge is an edge the artist made;
  2. small shells are packed together, largest first, into a piece that is still under the limit;
  3. a shell over the limit is cut on a grid (godot-splerger's split by grid): a triangle goes whole to the cell that holds
     its centre, none is split or copied; a cell still over the limit is cut again along its longest side at the median.

`split(points, tris, max_tris)` returns lists of triangle indices: a partition of range(len(tris)), each at most `max_tris`,
the same for the same input.
"""

import math
from typing import Dict, List, Sequence, Tuple

Point = Tuple[float, float, float]
Tri = Tuple[int, int, int]
WELD = 1e-5


def shells(points: Sequence[Point], tris: Sequence[Tri]) -> List[List[int]]:
    """Triangle indices grouped by shell, each shell in order, shells ordered by their first triangle."""
    key = {}
    weld = []
    for p in points:
        weld.append(key.setdefault((round(p[0] / WELD), round(p[1] / WELD), round(p[2] / WELD)), len(key)))
    parent = list(range(len(key)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b, c in tris:
        ra, rb, rc = find(weld[a]), find(weld[b]), find(weld[c])
        parent[rb] = ra
        parent[find(rc)] = ra
    groups: Dict[int, List[int]] = {}
    for i, (a, _, _) in enumerate(tris):
        groups.setdefault(find(weld[a]), []).append(i)
    return sorted(groups.values(), key=lambda g: g[0])


def _centre(points: Sequence[Point], t: Tri) -> Point:
    a, b, c = points[t[0]], points[t[1]], points[t[2]]
    return ((a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3, (a[2] + b[2] + c[2]) / 3)


def grid_cut(points: Sequence[Point], tris: Sequence[Tri], ids: List[int], max_tris: int) -> List[List[int]]:
    """One shell over the limit, cut by a grid over its bounding box, then by medians where a cell is still too big."""
    centres = {i: _centre(points, tris[i]) for i in ids}
    lo = [min(centres[i][k] for i in ids) for k in range(3)]
    hi = [max(centres[i][k] for i in ids) for k in range(3)]
    want = math.ceil(len(ids) / max_tris) * 2  # twice the cells the count needs: a cell is rarely full
    spans = [max(hi[k] - lo[k], 1e-9) for k in range(3)]
    size = (spans[0] * spans[1] * spans[2] / want) ** (1 / 3) if min(spans) > 1e-6 else max(spans) / want
    size = max(size, 1e-9)
    cells: Dict[Tuple[int, int, int], List[int]] = {}
    for i in ids:
        c = centres[i]
        cells.setdefault(tuple(int((c[k] - lo[k]) / size) for k in range(3)), []).append(i)  # type: ignore[arg-type]
    out: List[List[int]] = []
    for cell in sorted(cells):
        out.extend(_median_cut(centres, cells[cell], max_tris))
    return out


def _median_cut(centres: Dict[int, Point], ids: List[int], max_tris: int) -> List[List[int]]:
    if len(ids) <= max_tris:
        return [ids]
    axis = max(range(3), key=lambda k: max(centres[i][k] for i in ids) - min(centres[i][k] for i in ids))
    ordered = sorted(ids, key=lambda i: (centres[i][axis], i))  # ties by index: the same input cuts the same way
    half = len(ordered) // 2
    return _median_cut(centres, ordered[:half], max_tris) + _median_cut(centres, ordered[half:], max_tris)


def split(points: Sequence[Point], tris: Sequence[Tri], max_tris: int) -> List[List[int]]:
    if max_tris < 1:
        raise ValueError("max_tris is at least 1")
    if len(tris) <= max_tris:
        return [list(range(len(tris)))] if tris else []
    pieces: List[List[int]] = []
    small: List[List[int]] = []
    for shell in shells(points, tris):
        if len(shell) > max_tris:
            pieces.extend(grid_cut(points, tris, shell, max_tris))
        else:
            small.append(shell)
    bins: List[List[int]] = []
    for shell in sorted(small, key=lambda s: (-len(s), s[0])):  # first fit, largest first
        for b in bins:
            if len(b) + len(shell) <= max_tris:
                b.extend(shell)
                break
        else:
            bins.append(list(shell))
    pieces.extend(sorted(b) for b in bins)
    return pieces


def piece(points: Sequence[Point], tris: Sequence[Tri], ids: Sequence[int]) -> Tuple[List[Point], List[Tri]]:
    """The points and triangles of one piece, with the points it does not use dropped and the indices renumbered."""
    remap: Dict[int, int] = {}
    pts: List[Point] = []
    out: List[Tri] = []
    for i in ids:
        row = []
        for v in tris[i]:
            if v not in remap:
                remap[v] = len(pts)
                pts.append(points[v])
            row.append(remap[v])
        out.append((row[0], row[1], row[2]))
    return pts, out
