"""Phase 1: capture the 8x2 impulse-response matrix from the oracle.

Method (forced by the W gate, see docs/PROTOCOL.md): every render carries an
identical 1e-5 noise pilot on channel 0 spanning the probe region, which
keeps the engine in its open, linear regime. One render of the pilot alone is
the baseline; for each channel a unit impulse is added on top, and the
channel's impulse response is the difference of the two outputs. Phase 0
verified this difference is exact at float32 precision.

A noise-probe deconvolution cross-check runs per channel: 1 s of noise on
the channel (plus the same pilot), the contribution extracted by difference,
and the IR recovered by regularised FFT division against the probe. The two
estimates must agree within the numerical floor.

Output: data/tbe8_ir_48k_block512.npz with the IR matrix and capture
metadata. The data directory is gitignored; whether measured IRs of the
proprietary engine can be published was settled and the answer is no
(docs/PLAN.md); they stay local.

Usage: python tools/phase1_capture.py [--fs 48000] [--block 512]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracle import render, residual_db

GUARD = 8000          # silence before the pilot region (warm-up window)
PROBE = 16000         # impulse position, well inside the pilot region
IR_LEN = 4096         # captured IR window, generous vs the observed ~178
PILOT_LEVEL = 1e-5
NOISE_LEVEL = 0.1


def pilot_signal(total: int, fs: int) -> np.ndarray:
    rng = np.random.default_rng(1000)
    x = np.zeros((total, 8), dtype=np.float32)
    x[GUARD:total - GUARD, 0] = (
        rng.standard_normal(total - 2 * GUARD).astype(np.float32) * PILOT_LEVEL
    )
    return x


def support_len(h: np.ndarray, floor_db: float = -120.0) -> int:
    mag = np.abs(h).max(axis=1)
    peak = mag.max()
    if peak == 0.0:
        return 0
    above = np.nonzero(mag > peak * 10 ** (floor_db / 20))[0]
    return int(above[-1]) + 1 if len(above) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fs", type=int, default=48000)
    ap.add_argument("--block", type=int, default=512)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    fs, block = args.fs, args.block

    # Advance is an exact whole number of blocks (phase 0): 7 at block 512.
    blocks_eaten = {256: 15, 512: 7, 1024: 3}.get(block)
    if blocks_eaten is None:
        raise SystemExit(f"advance not characterised for block {block}; "
                         "measure it first (phase0_lti.py warm-up section)")
    advance = blocks_eaten * block

    total = 2 * fs
    base = pilot_signal(total, fs)
    print(f"capture: fs={fs} block={block} advance={advance} "
          f"probe@{PROBE} ir_len={IR_LEN}")

    y_pilot = render(base, fs=fs, block=block)

    h = np.zeros((8, IR_LEN, 2), dtype=np.float64)
    print("impulse capture:")
    for ch in range(8):
        x = base.copy()
        x[PROBE, ch] += 1.0
        diff = render(x, fs=fs, block=block) - y_pilot
        seg = diff[PROBE - advance:PROBE - advance + IR_LEN]
        h[ch, :len(seg)] = seg
        sup = support_len(h[ch])
        pk_l = float(np.abs(h[ch, :, 0]).max())
        pk_r = float(np.abs(h[ch, :, 1]).max())
        print(f"  ch {ch}: support {sup:4d} samples, "
              f"peak L {pk_l:.4f} R {pk_r:.4f}")

    print("noise-deconvolution cross-check:")
    worst = -np.inf
    for ch in range(8):
        rng = np.random.default_rng(2000 + ch)
        n = rng.standard_normal(fs).astype(np.float32) * NOISE_LEVEL
        x = base.copy()
        x[PROBE:PROBE + fs, ch] += n
        contrib = render(x, fs=fs, block=block) - y_pilot
        # Align the contribution to the probe and deconvolve.
        seg = contrib[PROBE - advance:PROBE - advance + fs + IR_LEN]
        nfft = 1 << int(np.ceil(np.log2(len(seg))))
        X = np.fft.rfft(n, nfft)
        eps = 1e-12 * float(np.abs(X).max()) ** 2
        est = np.stack(
            [
                np.fft.irfft(
                    np.fft.rfft(seg[:, ear], nfft) * np.conj(X)
                    / (np.abs(X) ** 2 + eps),
                    nfft,
                )[:IR_LEN]
                for ear in range(2)
            ],
            axis=1,
        )
        r = residual_db(est, h[ch])
        worst = max(worst, r)
        print(f"  ch {ch}: impulse vs deconvolved IR: {r:.1f} dB")
    print(f"  worst channel: {worst:.1f} dB")

    out = Path(args.out) if args.out else (
        Path(__file__).resolve().parent.parent
        / "data" / f"tbe8_ir_{fs//1000}k_block{block}.npz"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        out,
        h=h.astype(np.float32),
        fs=fs,
        block=block,
        advance=advance,
        probe=PROBE,
        pilot_level=PILOT_LEVEL,
        ir_len=IR_LEN,
        sdk="Audio360 1.7.12 x86_64",
        captured="2026-08-14",
    )
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
