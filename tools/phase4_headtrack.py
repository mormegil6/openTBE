"""Phase 4: head-tracked (rotated-listener) decode, verified per orientation.

Native chain: TBE -> ACN harmonics (per-channel gains) -> order-2 real-SH
rotation (tools/rotation.py) -> fixed Ambisonics-to-binaural decode (mono IR
per harmonic, L/R combined by the sign of m). All nine ACN decode filters
come from Meta's published 3OA coefficients, ACN 6 included: TBE cannot
carry that harmonic, but rotation feeds it under pitch and roll, and the
published set supplies it.

The oracle side uses bin/tbe_render_rot, which is the study's helper with the
listener rotation taken from the command line.

Stages, matching the printed output: stage 0 is a convention-free sanity
check at the identity rotation; stages 1 and 2 fit the SDK's undocumented
yaw/pitch/roll conventions (sign per axis, then composition order) against
the oracle using single-axis probes; stage 3 measures the native-vs-oracle
residual across an orientation grid with the fitted convention; stages 4
and 5 recover the SDK's own ACN 6 filter by deconvolution and re-measure
the grid with it. The recovery is a cross-check rather than a necessity,
since the published filter already lands at the float floor. Results go to
docs/PROTOCOL.md.

Usage: python tools/phase4_headtrack.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracle import residual_db
from rotation import listener_rotation_matrix, sh_rotation_matrix

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ROT_BIN = ROOT / "bin" / "tbe_render_rot"
MIT_CPP = ROOT / "docs" / "upstream" / "audio360-mit" / "AmbiBinauralCoefficients3OA.cpp"
DATA_DIR = ROOT / "data"
GRID_NPZ = DATA_DIR / "phase4_orientation_grid.npz"

FS = 48000
BLOCK = 512
ADVANCE = 3584
GUARD = 8000
SKIP = 8000

# Single source of truth, measured against the FB360 Encoder; see
# tools/tbe_matrix.py and tools/phase7_encode.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tbe_matrix import TBE_FROM_ACN  # noqa: E402

ACN_M = [0, -1, 0, 1, -2, -1, 0, 1, 2]   # m per ACN index 0..8


def render_oracle(x: np.ndarray, ypr: tuple[float, float, float]) -> np.ndarray:
    with tempfile.TemporaryDirectory() as td:
        raw_in, raw_out = Path(td) / "in.raw", Path(td) / "out.raw"
        np.ascontiguousarray(x, dtype="<f4").tofile(raw_in)
        cmd = ["arch", "-x86_64", str(ROT_BIN), str(raw_in), str(raw_out),
               "8", str(FS), str(BLOCK), str(ypr[0]), str(ypr[1]), str(ypr[2])]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip())
        return np.fromfile(raw_out, dtype="<f4").reshape(-1, 2).astype(np.float64)


def acn_decode_filters() -> list[np.ndarray]:
    """Mono decode IR per ACN harmonic 0..8, from Meta's published 3OA set.

    All nine come from AmbiBinauralCoefficients3OA.cpp, which is what the
    SDK itself uses: its arrays are byte-identical to the ones inside
    libAudio360.dylib, and decoding with them matches the SDK to about
    -134 dB at every tested orientation, ACN 6 included. No local
    measurement is involved or needed (docs/PROTOCOL.md, phase 4).
    """
    from get_tbe_filters import parse_mit_harmonics
    harmonics = parse_mit_harmonics(MIT_CPP)
    return [harmonics[acn].astype(np.float64) for acn in range(9)]


def render_native(x: np.ndarray, ypr, signs, order,
                  filters: list[np.ndarray]) -> np.ndarray:
    acn = np.zeros((len(x), 9))
    for k, (a, g) in enumerate(TBE_FROM_ACN):
        acn[:, a] = x[:, k].astype(np.float64) / g
    m3 = listener_rotation_matrix(*ypr, signs=signs, order=order)
    M = sh_rotation_matrix(m3)
    rot = acn @ M.T
    out_len = len(x) + FS
    max_len = max(len(f) for f in filters)
    L = np.zeros(len(x) + max_len - 1)
    R = np.zeros_like(L)
    for j in range(9):
        col = rot[:, j]
        if not np.any(col):
            continue
        c = fftconvolve(col, filters[j])
        L[: len(c)] += c
        R[: len(c)] += c if ACN_M[j] >= 0 else -c
    y = np.zeros((out_len, 2))
    seg = np.stack([L, R], axis=1)[ADVANCE:ADVANCE + out_len]
    y[: len(seg)] = seg
    return y


def make_content(seed: int = 5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    total = int(1.5 * FS)
    x = np.zeros((total, 8), dtype=np.float32)
    active = total - 2 * GUARD
    for ch in range(8):
        x[GUARD:GUARD + active, ch] = rng.standard_normal(active).astype(
            np.float32) * (0.05 if ch else 0.08)
    return x


def compare(x, ypr, signs, order, filters) -> float:
    y_o = render_oracle(x, ypr)
    y_n = render_native(x, ypr, signs, order, filters)
    n = min(len(y_o), len(y_n))
    return residual_db(y_n[SKIP:n], y_o[SKIP:n])


R_MEASURED_NPZ = ROOT / "data" / "acn_r_filter_measured.npz"


def recover_r_filter(x, signs, order, filters,
                     probe_ypr=(0, 30, 0)) -> np.ndarray:
    """Recover the SDK's actual R (ACN 6) decode filter.

    TBE carries no R, so phase 1 could not measure its filter directly.
    Meta's published 3OA set supplies it and is already what the decode
    uses, so this is a cross-check rather than a necessity: the recovered
    filter agrees with the published one to -133.2 dB. Under a known
    rotation the R-channel signal is
    known exactly, and the native-vs-oracle residual is that signal
    convolved with the filter difference (m=0 harmonics feed L and R
    identically, which the residual's L/R symmetry confirms). One
    deconvolution recovers the difference; published + difference is the
    actual filter.
    """
    acn = np.zeros((len(x), 9))
    for k, (a, g) in enumerate(TBE_FROM_ACN):
        acn[:, a] = x[:, k].astype(np.float64) / g
    M = sh_rotation_matrix(
        listener_rotation_matrix(*probe_ypr, signs=signs, order=order))
    rot_r = (acn @ M.T)[:, 6]

    y_o = render_oracle(x, probe_ypr)
    y_n = render_native(x, probe_ypr, signs, order, filters)
    n = min(len(y_o), len(y_n))
    resid_l = (y_o[:n] - y_n[:n])[:, 0]

    L = 512
    target = np.concatenate([np.zeros(ADVANCE), resid_l])[:len(rot_r) + L]
    nfft = 1 << int(np.ceil(np.log2(len(rot_r) + L)))
    X = np.fft.rfft(rot_r, nfft)
    eps = 1e-9 * float(np.abs(X).max()) ** 2
    delta = np.fft.irfft(
        np.fft.rfft(target, nfft) * np.conj(X) / (np.abs(X) ** 2 + eps),
        nfft)[:L]

    r = np.zeros(max(len(filters[6]), L))
    r[: len(filters[6])] = filters[6]
    r[:L] += delta
    return r


def main() -> int:
    if not ROT_BIN.exists():
        raise SystemExit(f"{ROT_BIN} not built; see tools/tbe_render_rot.cpp")
    filters = acn_decode_filters()
    x = make_content()

    print("stage 0: convention-free sanity, identity rotation")
    r = compare(x, (0, 0, 0), (1, 1, 1), "zyx", filters)
    print(f"  ypr (0,0,0): {r:.1f} dB (should match the fixed-head result)")

    print("stage 1: fit per-axis signs (single-axis probes, 30 degrees)")
    fitted = []
    for axis, ypr in (("yaw", (30, 0, 0)), ("pitch", (0, 30, 0)),
                      ("roll", (0, 0, 30))):
        best = None
        for s in (1, -1):
            signs = {"yaw": (s, 1, 1), "pitch": (1, s, 1),
                     "roll": (1, 1, s)}[axis]
            res = compare(x, ypr, signs, "zyx", filters)
            print(f"  {axis} 30, sign {s:+d}: {res:7.1f} dB")
            if best is None or res < best[0]:
                best = (res, s)
        fitted.append(best[1])
    signs = tuple(fitted)
    print(f"  fitted signs (yaw, pitch, roll): {signs}")

    print("stage 2: fit composition order on a combined rotation")
    combined = (35, 20, 10)
    results = {}
    for order in ("zyx", "zxy", "yzx", "yxz", "xzy", "xyz"):
        results[order] = compare(x, combined, signs, order, filters)
        print(f"  order {order}: {results[order]:7.1f} dB")
    order = min(results, key=results.get)
    print(f"  fitted order: {order}")

    grid = [(0, 0, 0), (30, 0, 0), (-30, 0, 0), (90, 0, 0), (180, 0, 0),
            (0, 30, 0), (0, -30, 0), (0, 0, 30), (0, 0, -30),
            (35, 20, 10), (-60, -25, 15)]

    print("stage 3: orientation grid, shipped filters")
    stage3_db = []
    for ypr in grid:
        res = compare(x, ypr, signs, order, filters)
        stage3_db.append(res)
        print(f"  ypr {str(ypr):>15}: {res:7.1f} dB")

    print("stage 4: recover the SDK's actual R filter from one pitch probe")
    r = recover_r_filter(x, signs, order, filters)
    delta_db = 20 * np.log10(
        np.linalg.norm(r[: len(filters[6])] - filters[6])
        / np.linalg.norm(filters[6]))
    print(f"  published-vs-recovered R difference: {delta_db:.1f} dB")
    np.savez(R_MEASURED_NPZ, r=r.astype(np.float32), fs=FS,
             method="published MIT R plus deconvolved residual from a "
                    "pitch-30 oracle render",
             probe="(0, 30, 0)")
    print(f"  -> {R_MEASURED_NPZ}")

    print("stage 5: orientation grid with the recovered R "
          "(all probes out-of-sample except pitch +30)")
    f2 = list(filters)
    f2[6] = r
    worst = -np.inf
    stage5_db = []
    for ypr in grid:
        res = compare(x, ypr, signs, order, f2)
        stage5_db.append(res)
        worst = max(worst, res)
        print(f"  ypr {str(ypr):>15}: {res:7.1f} dB")
    print(f"\nworst orientation with recovered R: {worst:.1f} dB "
          f"(signs {signs}, order {order})")

    # Additive: persist the full per-orientation grid (yaw/pitch/roll plus
    # the dB residual already printed above, for both the published-R and
    # recovered-R passes) so tools/plot_validation.py can plot it. Does not
    # change any printed output or the pass/fail verdict below.
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    grid_arr = np.array(grid, dtype=np.float64)
    np.savez(
        GRID_NPZ,
        yaw=grid_arr[:, 0],
        pitch=grid_arr[:, 1],
        roll=grid_arr[:, 2],
        stage3_db_published_r=np.array(stage3_db, dtype=np.float64),
        stage5_db_recovered_r=np.array(stage5_db, dtype=np.float64),
        # Identical to stage3_db by construction: stage 3 runs the shipped
        # filters. The key survives for plot_validation.py, which prefers it.
        shipped_db_mit_only=np.array(stage3_db, dtype=np.float64),
        signs=np.array(signs, dtype=np.int64),
        order=order,
        r_filter_delta_db=delta_db,
    )
    print(f"  -> {GRID_NPZ}")

    return 0 if worst < -100 else 1


if __name__ == "__main__":
    raise SystemExit(main())
