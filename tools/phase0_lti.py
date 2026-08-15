"""Phase 0: is the oracle's fixed-head decode linear and time-invariant?

Runs the test battery from docs/PLAN.md against the reference decoder:

  warm-up   measure the constant time advance and the lost-input window,
            per block size
  gate      the engine mutes entirely while channel 0 (W) is exactly zero;
            these tests document that and show a tiny W pilot reopens it
  determinism   identical input twice, expect bit-identical output
  scaling   render(g * x) vs g * render(x), at -24 and -48 dBFS
  superposition   render(a) + render(b) vs render(a + b)
  shift     render(delay(x, N)) vs delay(render(x), N), N inside and
            off the block grid

Because of the W gate, every probe signal carries a 1e-5 W pilot so the
engine stays in its open, linear regime. Every probe also sits behind a
guard interval so the warm-up window cannot touch it. Residuals are RMS
relative to reference, in dB; -inf means bit-identical.

Usage: python tools/phase0_lti.py [--fs 48000] [--block 512]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracle import render, residual_db

GUARD = 8000  # samples of silence before any probe, well past the warm-up window


def measure_warmup(fs: int, block: int) -> tuple[int, int]:
    """Return (advance_first_nonzero, advance_peak) in samples for this block size."""
    pos = fs  # probe 1 s in
    x = np.zeros((2 * fs, 8), dtype=np.float32)
    x[pos, 0] = 1.0
    y = render(x, fs=fs, block=block)
    mag = np.abs(y).max(axis=1)
    nz = np.nonzero(mag > 1e-7)[0]
    if not len(nz):
        raise RuntimeError("no response to the warm-up probe at all")
    first = pos - int(nz[0])
    peak = pos - int(np.argmax(mag))
    return first, peak


PILOT_LEVEL = 1e-5


def make_signal(fs: int, seed: int, channels: list[int], start: int, length: int,
                total: int, level: float = 0.1, pilot: bool = True) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = np.zeros((total, 8), dtype=np.float32)
    for ch in channels:
        x[start:start + length, ch] = (
            rng.standard_normal(length).astype(np.float32) * level
        )
    if pilot:
        # Keep the W gate open for the whole probe region; the pilot is part
        # of the signal, so linearity tests remain exact.
        x[start:start + length, 0] += (
            rng.standard_normal(length).astype(np.float32) * PILOT_LEVEL
        )
    return x


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fs", type=int, default=48000)
    ap.add_argument("--block", type=int, default=512)
    args = ap.parse_args()
    fs, block = args.fs, args.block

    results: list[tuple[str, str, bool]] = []

    def report(name: str, value: str, ok: bool) -> None:
        results.append((name, value, ok))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: {value}")

    print(f"phase 0 against the oracle, fs={fs}, block={block}")

    print("warm-up geometry:")
    for b in (256, 512, 1024):
        first, peak = measure_warmup(fs, b)
        blocks = first / b
        print(f"  block {b:4d}: advance {first} samples (first nonzero, "
              f"= {blocks:.2f} blocks), {peak} (peak)")
    first, _ = measure_warmup(fs, block)
    print(f"  using block {block}: probes guarded by {GUARD} samples")

    total = 3 * fs
    base = make_signal(fs, seed=1, channels=[0, 3, 5], start=GUARD,
                       length=fs, total=total)

    print("W gate:")
    ch1_only = make_signal(fs, seed=4, channels=[1], start=GUARD, length=fs,
                           total=total, level=0.5, pilot=False)
    y = render(ch1_only, fs=fs, block=block)
    silent = float(np.abs(y).max()) == 0.0
    report("ch 1 alone (0.5, no W) is muted", str(silent), silent)

    pilot_only = make_signal(fs, seed=5, channels=[], start=GUARD, length=fs,
                             total=total)
    with_ch1 = pilot_only.copy()
    with_ch1[:, 1] = ch1_only[:, 1] * 0.2  # back to 0.1 level
    y_p = render(pilot_only, fs=fs, block=block)
    y_c = render(with_ch1, fs=fs, block=block)
    contrib = float(np.sqrt(((y_c - y_p) ** 2).mean()))
    report("1e-5 W pilot restores ch 1 contribution",
           f"rms {contrib:.4f}", contrib > 0.01)

    print("determinism:")
    y1 = render(base, fs=fs, block=block)
    y2 = render(base, fs=fs, block=block)
    identical = np.array_equal(y1, y2)
    report("bit-identical across runs", str(identical), identical)

    print("scaling:")
    for gain_db in (-24.0, -48.0):
        g = 10 ** (gain_db / 20)
        yg = render((base * g).astype(np.float32), fs=fs, block=block)
        r = residual_db(yg, y1 * g)
        report(f"render({gain_db:+.0f} dB x) vs scaled render(x)",
               f"{r:.1f} dB", r < -100)

    print("superposition:")
    a = make_signal(fs, seed=2, channels=[1, 2], start=GUARD, length=fs, total=total)
    b = make_signal(fs, seed=3, channels=[2, 6, 7], start=GUARD + 2400,
                    length=fs, total=total)
    ya = render(a, fs=fs, block=block)
    yb = render(b, fs=fs, block=block)
    yab = render(a + b, fs=fs, block=block)
    r = residual_db(yab, ya + yb)
    report("render(a+b) vs render(a)+render(b)", f"{r:.1f} dB", r < -100)

    print("time invariance:")
    for n in (512, 1000, 4096):
        shifted = np.zeros_like(base)
        shifted[n:] = base[:-n]
        ys = render(shifted, fs=fs, block=block)
        ref = np.zeros_like(ys)
        ref[n:] = y1[:-n]
        r = residual_db(ys[GUARD:], ref[GUARD:])
        grid = "on" if n % block == 0 else "off"
        report(f"shift by {n} ({grid}-grid)", f"{r:.1f} dB", r < -100)

    failed = [name for name, _, ok in results if not ok]
    print()
    if failed:
        print(f"phase 0: {len(failed)} of {len(results)} tests failed")
        return 1
    print(f"phase 0: all {len(results)} tests passed, the decode behaves as LTI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
