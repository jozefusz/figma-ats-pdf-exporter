"""Minimal SVG path-data parser for Figma's `fillGeometry`.

Figma emits absolute-only path data (M/L/C/Z, occasionally H/V/Q) for
vector nodes like icons. This is not a general SVG parser -- no arcs (A),
smooth-curve shorthands (S/T), or relative commands (Figma doesn't emit
them) -- just enough to draw the simple icon geometry this project needs.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[MLCQHVZ]|-?\d*\.?\d+(?:[eE][+-]?\d+)?", re.IGNORECASE)
_ARG_COUNTS = {"M": 2, "L": 2, "C": 6, "Q": 4, "H": 1, "V": 1, "Z": 0}


def apply_path_to_reportlab(path_obj, d: str, to_pdf) -> bool:
    """Replay an SVG path `d` string onto a reportlab PDFPathObject.

    `to_pdf(x, y)` converts a local (Figma-space) point to PDF page
    coordinates. Returns False (and draws nothing further) the first time
    it hits a command it can't handle, so callers can warn rather than
    silently emit a wrong/partial shape.
    """
    tokens = _TOKEN_RE.findall(d)
    i = 0
    cmd = None
    cur = (0.0, 0.0)
    start = (0.0, 0.0)

    def read_floats(n: int) -> list[float] | None:
        nonlocal i
        if i + n > len(tokens):
            return None
        vals = [float(t) for t in tokens[i : i + n]]
        i += n
        return vals

    while i < len(tokens):
        tok = tokens[i]
        if tok.upper() in _ARG_COUNTS:
            cmd = tok.upper()
            i += 1
        if cmd is None:
            return False

        if cmd == "M":
            args = read_floats(2)
            if args is None:
                return False
            cur = (args[0], args[1])
            start = cur
            path_obj.moveTo(*to_pdf(*cur))
            cmd = "L"  # subsequent implicit pairs after M are lineTos
        elif cmd == "L":
            args = read_floats(2)
            if args is None:
                return False
            cur = (args[0], args[1])
            path_obj.lineTo(*to_pdf(*cur))
        elif cmd == "H":
            args = read_floats(1)
            if args is None:
                return False
            cur = (args[0], cur[1])
            path_obj.lineTo(*to_pdf(*cur))
        elif cmd == "V":
            args = read_floats(1)
            if args is None:
                return False
            cur = (cur[0], args[0])
            path_obj.lineTo(*to_pdf(*cur))
        elif cmd == "C":
            args = read_floats(6)
            if args is None:
                return False
            p1 = to_pdf(args[0], args[1])
            p2 = to_pdf(args[2], args[3])
            p3 = to_pdf(args[4], args[5])
            path_obj.curveTo(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1])
            cur = (args[4], args[5])
        elif cmd == "Q":
            args = read_floats(4)
            if args is None:
                return False
            # Promote quadratic -> cubic (reportlab paths are cubic-only).
            qc = (args[0], args[1])
            end = (args[2], args[3])
            c1 = (cur[0] + 2 / 3 * (qc[0] - cur[0]), cur[1] + 2 / 3 * (qc[1] - cur[1]))
            c2 = (end[0] + 2 / 3 * (qc[0] - end[0]), end[1] + 2 / 3 * (qc[1] - end[1]))
            p1, p2, p3 = to_pdf(*c1), to_pdf(*c2), to_pdf(*end)
            path_obj.curveTo(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1])
            cur = end
        elif cmd == "Z":
            path_obj.close()
            cur = start
        else:
            return False

    return True
