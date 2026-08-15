"""Validate the native renderer against the oracle on a real TBE file.

Complements phase2_validate.py (synthetic signals) with programme material:
renders the given 8-channel TBE file through the oracle and through the
shipped-filter convolution renderer and reports the residual between the two.

Also scans the input for the one regime where the two legitimately differ:
samples where W is exactly zero while other channels carry signal (the
oracle's W gate mutes there, plain convolution does not; docs/PROTOCOL.md).
The comparison skips the first 8000 output samples, past the oracle's
4096-sample lost-input window.

If the file ends with content still sounding, the oracle's gate closes
about one block after the input runs out and mutes the last few thousand
samples of programme still in its pipeline (see PROTOCOL, end of stream).
To keep that measured artifact out of the comparison, an 8000-sample 1e-5 W
pilot tail is appended before rendering; disable with --no-pilot-tail to
observe the raw end-cut behaviour instead.

Usage: python tools/phase2_real.py in_tbe8.wav
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracle import render, residual_db
from render_native import NativeRenderer

SKIP = 8000


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="8-channel TBE wav, 48 kHz")
    ap.add_argument("--block", type=int, default=512)
    ap.add_argument("--no-pilot-tail", action="store_true",
                    help="do not append the W pilot tail; exposes the "
                         "oracle's end-of-stream gate cut")
    ap.add_argument("--ir", default=None,
                    help="filter npz to use (default: NativeRenderer's own "
                         "default: the shipped MIT-derived set)")
    args = ap.parse_args()

    r = NativeRenderer(args.ir) if args.ir else NativeRenderer()
    x, fs = sf.read(args.input, always_2d=True, dtype="float32")
    if fs != r.fs:
        raise SystemExit(f"{args.input} is {fs} Hz, IRs captured at {r.fs} Hz")
    if x.shape[1] != 8:
        raise SystemExit(f"{args.input} has {x.shape[1]} channels, expected 8")
    print(f"{Path(args.input).name}: {len(x)/fs:.1f} s, "
          f"peak {np.abs(x).max():.3f}")

    if not args.no_pilot_tail:
        rng = np.random.default_rng(3)
        tail = np.zeros((8000, 8), dtype=np.float32)
        tail[:, 0] = rng.standard_normal(8000).astype(np.float32) * 1e-5
        x = np.concatenate([x, tail])

    w_zero = x[:, 0] == 0.0
    others = np.abs(x[:, 1:]).max(axis=1) > 0.0
    unsafe = int(np.count_nonzero(w_zero & others))
    print(f"gate-unsafe samples (W exactly zero, others active): {unsafe} "
          f"of {len(x)}")

    y_o = render(x, fs=fs, block=args.block)
    y_n = r.render(x)
    n = min(len(y_o), len(y_n))
    res = residual_db(y_n[SKIP:n], y_o[SKIP:n])
    peak = float(np.abs(y_n[SKIP:n] - y_o[SKIP:n]).max())
    print(f"native vs oracle: {res:.1f} dB rms residual, "
          f"peak sample difference {peak:.2e}")
    ok = res < -100
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
