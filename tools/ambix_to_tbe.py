"""Encode ambiX (ACN/SN3D) to the 8-channel TBE format, natively.

This is the encode half of openTBE: the FB360 Encoder's ".tbe" conversion,
without the Encoder. Like the decode half it is a fixed linear operation, so
it is exact rather than approximate, and it runs anywhere Python does.

The matrix and the provenance of its gains are in tools/tbe_matrix.py; the
gains were measured against the real FB360 Encoder rather than copied from
the published table (tools/phase7_encode.py, docs/PROTOCOL.md).

Input is ambiX in ACN/SN3D, second order or higher. Reducing order in that
convention is exactly truncation to the first (N+1)^2 channels, so a
third-order 16-channel or seventh-order 64-channel master can be passed
straight in with no separate second-order intermediate.

    python tools/ambix_to_tbe.py in_ambix.wav out_tbe8.wav
    python tools/ambix_to_tbe.py --check reference_tbe8.wav in_ambix.wav

--check reports the residual against a reference TBE file, which is how a
file produced by the real Encoder can be compared with this one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tbe_matrix import (FARINA_PUBLISHED, N_AMBIX_MIN, N_TBE, TBE_FROM_ACN,
                        encode_matrix)


def encode(ambix: np.ndarray, published_gains: bool = False) -> np.ndarray:
    """(n, >=4) ambiX ACN/SN3D -> (n, 8) TBE.

    Second order or higher fills all 8 TBE channels. First-order input (4
    channels, what the Encoder calls ambix-first) is accepted too: the
    second-order harmonics simply are not there, so those TBE channels come
    out silent. That is a real order reduction, not an error, and it matches
    the Encoder accepting ambix-first as an input format.

    published_gains uses Farina's table instead of the measured gains, which
    exists so the difference can be measured rather than argued about.
    """
    if ambix.ndim != 2 or ambix.shape[1] < 4:
        raise ValueError(
            f"expected (n, >=4) ambiX input, got {ambix.shape}")
    n_in = ambix.shape[1]
    if n_in < N_AMBIX_MIN:
        # Pad to second order with silence rather than refusing: in ACN/SN3D
        # the missing harmonics are exactly zero, not unknown.
        ambix = np.concatenate(
            [ambix, np.zeros((len(ambix), N_AMBIX_MIN - n_in))], axis=1)
    out = np.zeros((len(ambix), N_TBE))
    for k, (acn, gain) in enumerate(TBE_FROM_ACN):
        g = FARINA_PUBLISHED[k] if published_gains else gain
        out[:, k] = ambix[:, acn] * g
    return out


def decode(tbe: np.ndarray, n_ambix: int = 9) -> np.ndarray:
    """(n, 8) TBE -> (n, n_ambix) ambiX ACN/SN3D.

    Exact inverse of encode() on the 8 harmonics TBE carries. ACN 6 (R) is
    returned as silence: the format cannot carry it, so it is not there to
    recover. Anything above second order is silence for the same reason.
    """
    if tbe.ndim != 2 or tbe.shape[1] != N_TBE:
        raise ValueError(f"expected (n, {N_TBE}) TBE input, got {tbe.shape}")
    out = np.zeros((len(tbe), n_ambix))
    for k, (acn, gain) in enumerate(TBE_FROM_ACN):
        if acn < n_ambix:
            out[:, acn] = tbe[:, k] / gain
    return out


def main() -> int:
    import soundfile as sf

    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="ambiX wav, ACN/SN3D, second order or higher")
    ap.add_argument("output", nargs="?", help="8-channel TBE wav to write")
    ap.add_argument("--check", metavar="REFERENCE_TBE",
                    help="compare against a reference TBE file instead of "
                         "writing output")
    ap.add_argument("--published-gains", action="store_true",
                    help="use Farina's published gains rather than the "
                         "measured ones (see tools/tbe_matrix.py)")
    ap.add_argument("--subtype", default="FLOAT")
    args = ap.parse_args()

    x, fs = sf.read(args.input, always_2d=True, dtype="float64")
    y = encode(x, published_gains=args.published_gains)

    if args.check:
        ref, fs_r = sf.read(args.check, always_2d=True, dtype="float64")
        if fs_r != fs:
            raise SystemExit(f"{args.check} is {fs_r} Hz, input is {fs} Hz")
        if ref.shape[1] != N_TBE:
            raise SystemExit(f"{args.check} has {ref.shape[1]} channels, "
                             f"expected {N_TBE}")
        n = min(len(ref), len(y))
        num = np.sqrt(((y[:n] - ref[:n]) ** 2).mean())
        den = np.sqrt((ref[:n] ** 2).mean())
        db = 20 * np.log10(max(num, 1e-30) / max(den, 1e-30))
        print(f"{args.input} -> encoded, vs {args.check}: {db:.1f} dB "
              f"over {n} frames")
        for k in range(N_TBE):
            n_k = np.sqrt(((y[:n, k] - ref[:n, k]) ** 2).mean())
            d_k = np.sqrt((ref[:n, k] ** 2).mean())
            print(f"  TBE {k}: "
                  f"{20 * np.log10(max(n_k, 1e-30) / max(d_k, 1e-30)):7.1f} dB")
        return 0

    if not args.output:
        raise SystemExit("give an output path, or use --check")
    sf.write(args.output, y, fs, subtype=args.subtype)
    print(f"{args.input}: {x.shape[1]}ch ambiX, {len(x)/fs:.1f} s "
          f"-> {args.output} ({N_TBE}ch TBE)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
