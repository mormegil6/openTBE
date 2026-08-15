"""Head-tracked TBE rendering with a time-varying orientation trajectory.

Implements the SDK's measured dynamic-rotation behaviour (docs/PROTOCOL.md,
dynamic rotation): orientation updates quantize to the next processing-block
boundary, and across the first block after a change the engine linearly
interpolates the rotation matrix from the previous block's matrix to the
new one, sample by sample (weight n/512 for n = 0..511 from the boundary).
Equivalently, the two rotated signal streams are crossfaded linearly over
exactly one block. Between updates the decode is the static phase 4 chain.

The trajectory file is text, one update per line, in output frames:

    <outputFrame> <yawDeg> <pitchDeg> <rollDeg>

matching tools/tbe_render_traj.cpp, so the same file drives both the oracle
and this renderer.

Usage: python tools/render_trajectory.py in_tbe8.wav trajectory.txt out.wav
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase4_headtrack import (ACN_M, ADVANCE, BLOCK, FS, TBE_FROM_ACN,
                              acn_decode_filters)
from rotation import listener_rotation_matrix, sh_rotation_matrix

SIGNS = (-1, -1, 1)
ORDER = "zyx"


def load_filters(quiet: bool = False) -> list[np.ndarray]:
    """The 9 mono decode filters, all from Meta's published 3OA coefficients.

    There is no configuration to choose any more. The published set includes
    ACN 6, the harmonic TBE itself cannot carry but which rotation feeds
    under pitch and roll, so head-tracked decode reaches about -132 to
    -134 dB against the SDK at every tested orientation without any local
    measurement (docs/PROTOCOL.md, phase 4).
    """
    filters = list(acn_decode_filters())
    if not quiet:
        print("filters: Meta's published 3OA coefficients, all 9 harmonics")
    return filters


def parse_trajectory(path: Path) -> list[tuple[int, tuple[float, float, float]]]:
    updates = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        updates.append((int(parts[0]),
                        (float(parts[1]), float(parts[2]), float(parts[3]))))
    if not updates:
        raise ValueError(f"no updates in {path}")
    updates.sort(key=lambda u: u[0])
    return updates


def render_trajectory(x: np.ndarray, updates, filters=None) -> np.ndarray:
    """(n, 8) TBE float array + orientation updates -> (n + fs, 2) binaural,
    aligned to the oracle's output timeline like the static renderer."""
    if filters is None:
        filters = load_filters()
    acn = np.zeros((len(x), 9))
    for k, (a, g) in enumerate(TBE_FROM_ACN):
        acn[:, a] = x[:, k].astype(np.float64) / g

    def m_of(ypr):
        return sh_rotation_matrix(
            listener_rotation_matrix(*ypr, signs=SIGNS, order=ORDER))

    n_blocks = int(np.ceil(len(acn) / BLOCK))
    targets = np.zeros((n_blocks, 9, 9))
    ui = 0
    for b in range(n_blocks):
        out_frame = b * BLOCK - ADVANCE
        while ui + 1 < len(updates) and updates[ui + 1][0] <= out_frame:
            ui += 1
        targets[b] = m_of(updates[ui][1])

    rot = np.zeros_like(acn)
    w = np.arange(BLOCK) / BLOCK
    prev = targets[0]
    for b in range(n_blocks):
        s, e = b * BLOCK, min((b + 1) * BLOCK, len(acn))
        tgt = targets[b]
        if np.array_equal(tgt, prev):
            rot[s:e] = acn[s:e] @ tgt.T
        else:
            blend = (prev[None] * (1 - w[: e - s, None, None])
                     + tgt[None] * w[: e - s, None, None])
            rot[s:e] = np.einsum("tij,tj->ti", blend, acn[s:e])
        prev = tgt

    max_len = max(len(f) for f in filters)
    left = np.zeros(len(x) + max_len - 1)
    right = np.zeros_like(left)
    for j in range(9):
        col = rot[:, j]
        if not np.any(col):
            continue
        c = fftconvolve(col, filters[j])
        left[: len(c)] += c
        right[: len(c)] += c if ACN_M[j] >= 0 else -c
    out_len = len(x) + FS
    y = np.zeros((out_len, 2))
    seg = np.stack([left, right], axis=1)[ADVANCE:ADVANCE + out_len]
    y[: len(seg)] = seg
    return y


def main() -> int:
    import soundfile as sf

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="8-channel TBE wav, 48 kHz")
    ap.add_argument("trajectory", help="trajectory file (outputFrame yaw pitch roll)")
    ap.add_argument("output", help="stereo binaural wav to write")
    ap.add_argument("--headlocked", default=None,
                    help="stereo wav to mix in unrotated (optional), as "
                         "produced by fb360_ingest.py --headlocked-out")
    ap.add_argument("--subtype", default="FLOAT")
    args = ap.parse_args()

    x, fs = sf.read(args.input, always_2d=True, dtype="float32")
    if fs != FS:
        raise SystemExit(f"{args.input} is {fs} Hz, expected {FS}")
    updates = parse_trajectory(Path(args.trajectory))
    y = render_trajectory(x, updates)
    if args.headlocked:
        hl, fs_hl = sf.read(args.headlocked, always_2d=True, dtype="float64")
        if fs_hl != fs:
            raise SystemExit("head-locked sample rate differs from TBE input")
        n = min(len(y), len(hl))
        y[:n] += hl[:n]
        print(f"  head-locked mixed in from {args.headlocked}")
    sf.write(args.output, y, fs, subtype=args.subtype)
    print(f"{args.input}: {len(x)/fs:.1f} s, {len(updates)} orientation updates "
          f"-> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
