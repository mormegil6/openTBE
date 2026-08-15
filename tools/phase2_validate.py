"""Phase 2: does the native renderer match the oracle on programme material?

Renders the same signals through the oracle and through the shipped-filter
convolution renderer, and reports the residual between the two.

Signals:

  noise      3 s of independent noise on all 8 channels
  programme  5 s of programme-like content: several correlated sources with
             distinct 8-channel gain patterns, level changes, and a quiet
             passage, W active throughout
  offgrid    impulses at positions off the block grid, indexing paranoia
  gatestress content that deliberately violates the W gate's assumption:
             energy on other channels across a window where W is exactly
             zero. The native renderer is plain convolution, so this test
             is expected to differ; it exists to characterise the gate's
             dynamics, not to pass.

Usage: python tools/phase2_validate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracle import render, residual_db
from render_native import NativeRenderer

FS = 48000
GUARD = 8000

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_NPZ = DATA_DIR / "phase2_residuals.npz"

# Populated by compare() (and the gate-stress block in main()) as the script
# runs. This is purely additive bookkeeping for tools/plot_validation.py: it
# mirrors the numbers already printed below plus the per-sample residual
# (native minus oracle) each comparison already computes internally before
# reducing it to the dB figure that gets printed. Nothing here changes what
# is printed or the pass/fail logic.
_residual_arrays: dict[str, np.ndarray] = {}
_residual_scalars: dict[str, float] = {}
# Pass threshold per stored test, so a consumer knows which entries are
# pass/fail cases and which are characterisation-only. The gate-stress test is
# deliberately expected to differ (it probes the W gate, which this renderer
# does not emulate) and is never folded into `ok` below, so it gets NaN rather
# than a threshold it would appear to fail. Without this, a plot of these
# numbers shows the renderer failing a test the script never fails it on.
_residual_thresholds: dict[str, float] = {}


def compare(name: str, x: np.ndarray, r: NativeRenderer,
            skip: int = GUARD, store_key: str | None = None,
            threshold: float = -100.0) -> float:
    y_o = render(x, fs=FS, block=512)
    y_n = r.render(x)
    n = min(len(y_o), len(y_n))
    res = residual_db(y_n[skip:n], y_o[skip:n])
    print(f"  {name}: {res:.1f} dB")
    if store_key is not None:
        _residual_arrays[store_key] = (y_n[skip:n] - y_o[skip:n]).astype(
            np.float32)
        _residual_scalars[store_key] = res
        _residual_thresholds[store_key] = threshold
    return res


def main() -> int:
    r = NativeRenderer()
    ok = True

    print("noise, all channels:")
    rng = np.random.default_rng(42)
    x = np.zeros((3 * FS, 8), dtype=np.float32)
    x[GUARD:-GUARD] = rng.standard_normal((3 * FS - 2 * GUARD, 8)).astype(
        np.float32) * 0.05
    ok &= compare("8ch independent noise", x, r, store_key="noise") < -100

    print("programme-like:")
    total = 5 * FS
    x = np.zeros((total, 8), dtype=np.float32)
    for i in range(4):
        src_rng = np.random.default_rng(100 + i)
        length = FS + i * 12000
        start = GUARD + i * 40000
        src = src_rng.standard_normal(length).astype(np.float32)
        env = np.hanning(length).astype(np.float32)
        gains = src_rng.uniform(-1, 1, 8).astype(np.float32) * 0.1
        gains[0] = abs(gains[0]) + 0.05    # W present and positive
        x[start:start + length] += np.outer(src * env, gains)
    x[3 * FS:3 * FS + FS // 2] *= 0.01     # quiet passage, W still nonzero
    ok &= compare("4 sources + quiet passage", x, r,
                  store_key="programme") < -100

    print("off-grid impulses:")
    x = np.zeros((2 * FS, 8), dtype=np.float32)
    pilot_rng = np.random.default_rng(7)
    x[GUARD:-GUARD, 0] = pilot_rng.standard_normal(
        2 * FS - 2 * GUARD).astype(np.float32) * 1e-5
    for pos, ch in [(GUARD + 333, 1), (GUARD + 10007, 4), (GUARD + 30001, 7)]:
        x[pos, ch] += 0.7
    ok &= compare("impulses at 333/10007/30001 past guard", x, r,
                  store_key="offgrid") < -100

    print("gate stress (expected to differ, characterisation only):")
    x = np.zeros((3 * FS, 8), dtype=np.float32)
    g_rng = np.random.default_rng(9)
    # W active only in the first second of the content region; channel 1
    # keeps sounding for another second while W is exactly zero.
    x[GUARD:GUARD + FS, 0] = g_rng.standard_normal(FS).astype(np.float32) * 0.05
    x[GUARD:GUARD + 2 * FS, 1] = g_rng.standard_normal(
        2 * FS).astype(np.float32) * 0.1
    y_o = render(x, fs=FS, block=512)
    y_n = r.render(x)
    n = min(len(y_o), len(y_n))
    res = residual_db(y_n[:n], y_o[:n])
    print(f"  overall residual: {res:.1f} dB")
    _residual_arrays["gatestress"] = (y_n[:n] - y_o[:n]).astype(np.float32)
    _residual_scalars["gatestress"] = res
    # NaN, not a number: this test is characterisation, not pass/fail. It is
    # never folded into `ok`, so it has no threshold to be judged against.
    _residual_thresholds["gatestress"] = float("nan")
    # Where does the oracle diverge from plain convolution?
    d = np.abs(y_n[:n] - y_o[:n]).max(axis=1)
    thresh = float(np.abs(y_o).max()) * 1e-4
    div = np.nonzero(d > thresh)[0]
    if len(div):
        w_end = GUARD + FS
        print(f"  divergence from sample {div[0]} to {div[-1]} "
              f"(W goes silent at {w_end}, advance is 3584)")
        # Is the oracle silent there, or something subtler?
        seg = y_o[div[0]:div[-1] + 1]
        oracle_rms = float(np.sqrt((seg**2).mean()))
        native_rms = float(np.sqrt((y_n[div[0]:div[-1]+1]**2).mean()))
        print(f"  oracle rms in divergent region: "
              f"{oracle_rms:.2e}; "
              f"native rms: "
              f"{native_rms:.2e}")
        _residual_scalars["gatestress_divergence_start"] = float(div[0])
        _residual_scalars["gatestress_divergence_end"] = float(div[-1])
        _residual_scalars["gatestress_oracle_rms_divergent"] = oracle_rms
        _residual_scalars["gatestress_native_rms_divergent"] = native_rms
    else:
        print("  no divergence: the gate did not engage on this signal")

    # Additive: persist the per-sample residuals and dB summaries computed
    # above so tools/plot_validation.py can plot them, without altering any
    # of the printed output or the pass/fail verdict below.
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    empty = np.zeros((0, 2), dtype=np.float32)
    np.savez(
        OUT_NPZ,
        fs=FS,
        guard=GUARD,
        # The residual arrays live on the output timeline, which the engine
        # advances by this many samples relative to the input (block 512).
        advance=3584,
        test_names=np.array(list(_residual_scalars.keys())),
        test_residual_db=np.array(list(_residual_scalars.values()),
                                  dtype=np.float64),
        # Parallel to test_names: the threshold each entry is judged against,
        # or NaN where the entry is characterisation-only (gate stress) or is
        # not a dB residual at all (the gatestress_* bookkeeping scalars).
        test_threshold_db=np.array(
            [_residual_thresholds.get(k, float("nan"))
             for k in _residual_scalars],
            dtype=np.float64),
        # The residual arrays for the three pass/fail tests start GUARD
        # samples into the render (compare()'s skip); the gate-stress one
        # starts at 0 because the gate's effect is what is being characterised.
        # Recorded so a plot can put the time axis where the data really is.
        residual_start_sample=np.array(
            [GUARD, GUARD, GUARD, 0], dtype=np.int64),
        residual_start_names=np.array(
            ["noise", "programme", "offgrid", "gatestress"]),
        residual_noise=_residual_arrays.get("noise", empty),
        residual_programme=_residual_arrays.get("programme", empty),
        residual_offgrid=_residual_arrays.get("offgrid", empty),
        residual_gatestress=_residual_arrays.get("gatestress", empty),
    )
    print(f"  -> {OUT_NPZ}")

    print()
    if not ok:
        print("phase 2: FAILED, the native render does not match the oracle")
        return 1
    print("phase 2: native renderer matches the oracle on all gate-safe "
          "signals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
