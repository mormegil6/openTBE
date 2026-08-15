"""Native TBE-to-binaural renderer: convolution with the shipped IR matrix.

Reproduces the SDK's fixed-head decode without the SDK: the output is the
sum over channels of the input convolved with the filter set generated from
Meta's published 3OA coefficients (tools/get_mit_filters.py),
time-advanced by the oracle's warm-up constant so the two outputs align
sample for sample. Runs natively on any CPU; no Rosetta, no dylib.

The oracle's W gate (see docs/PROTOCOL.md) is not emulated: this renderer is
plain linear convolution. On content whose channels are silent whenever W is
silent (which includes all real programme material) the two behave
identically; the difference only appears on signals that put energy on other
channels while W is exactly zero.

As a script: python tools/render_native.py in_tbe8.wav out_binaural.wav
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MEASURED_IR = DATA_DIR / "tbe8_ir_48k_block512.npz"
MIT_IR = DATA_DIR / "tbe8_filters_mit.npz"
# The MIT-derived set is the default, always. It is generated from Meta's
# own published 3OA coefficients and reproduces the SDK to about -134 dB,
# i.e. the float floor, so there is nothing for a locally measured set to
# improve on. The measurement is kept only as an independent cross-check
# (tools/phase1_capture.py); pass --ir explicitly to render with it.
DEFAULT_IR = MIT_IR


class NativeRenderer:
    def __init__(self, ir_path: Path | str = DEFAULT_IR, quiet: bool = False):
        ir_path = Path(ir_path)
        if not quiet:
            print(f"NativeRenderer: loading {ir_path.name}")
        d = np.load(ir_path)
        self.h = d["h"].astype(np.float64)          # (8, ir_len, 2)
        self.fs = int(d["fs"])
        self.advance = int(d["advance"])
        self.ir_len = int(d["ir_len"])

    def render(self, x: np.ndarray) -> np.ndarray:
        """(n, 8) TBE float array -> (n + fs, 2) binaural, oracle-aligned."""
        if x.ndim != 2 or x.shape[1] != 8:
            raise ValueError(f"expected (n, 8) input, got {x.shape}")
        frames = len(x)
        out_len = frames + self.fs          # the oracle renders 1 s of tail
        full = np.zeros((frames + self.ir_len - 1, 2))
        for ch in range(8):
            col = x[:, ch].astype(np.float64)
            if not np.any(col):
                continue
            for ear in range(2):
                full[:, ear] += fftconvolve(col, self.h[ch, :, ear])
        y = np.zeros((out_len, 2))
        seg = full[self.advance:self.advance + out_len]
        y[:len(seg)] = seg
        return y


def main() -> int:
    import soundfile as sf

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--ir", default=str(DEFAULT_IR))
    ap.add_argument("--subtype", default="FLOAT")
    args = ap.parse_args()

    r = NativeRenderer(args.ir)
    x, fs = sf.read(args.input, always_2d=True, dtype="float32")
    if fs != r.fs:
        raise SystemExit(f"{args.input} is {fs} Hz, IRs captured at {r.fs} Hz")
    y = r.render(x)
    sf.write(args.output, y, fs, subtype=args.subtype)
    print(f"{args.input}: {len(x)/fs:.1f} s -> {args.output} ({len(y)/fs:.1f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
