"""Phase 5: how the SDK handles rotation changes mid-stream, and whether
the native trajectory renderer reproduces it.

Stage 1 measures the step response: content held steady, one orientation
step scheduled mid-stream via bin/tbe_render_traj, output compared sample
by sample against static renders of both orientations. This established
(docs/PROTOCOL.md): updates take effect at the next block boundary, and
the transition is a linear crossfade of the two rotated signal streams
over exactly one block, with the remaining bit-difference decaying over
one IR length. A slerp of the rotation itself is refuted at -19 dB.

Stage 2 verifies tools/render_trajectory.py against the oracle on discrete
steps, chained consecutive-block steps, mixed-axis sequences, and
continuous per-block tracking.

Requires bin/tbe_render_rot and bin/tbe_render_traj (see the build lines in
their sources).

Usage: python tools/phase5_dynamic.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracle import residual_db
from phase4_headtrack import BLOCK, FS, ROT_BIN
from render_trajectory import load_filters, render_trajectory

ROOT = Path(__file__).resolve().parent.parent
TRAJ_BIN = ROOT / "bin" / "tbe_render_traj"
GUARD = 8000

DATA_DIR = ROOT / "data"
OUT_NPZ = DATA_DIR / "phase5_trajectory_residuals.npz"


def make_content(seed: int = 9) -> np.ndarray:
    rng = np.random.default_rng(seed)
    total = 3 * FS
    x = np.zeros((total, 8), dtype=np.float32)
    x[GUARD:-GUARD] = rng.standard_normal(
        (total - 2 * GUARD, 8)).astype(np.float32) * 0.05
    return x


def run_oracle_traj(x: np.ndarray, updates) -> np.ndarray:
    with tempfile.TemporaryDirectory() as td:
        raw_in = Path(td) / "in.raw"
        raw_out = Path(td) / "out.raw"
        traj = Path(td) / "traj.txt"
        np.ascontiguousarray(x, dtype="<f4").tofile(raw_in)
        traj.write_text("".join(
            f"{f} {y} {p} {r}\n" for f, (y, p, r) in updates))
        cmd = ["arch", "-x86_64", str(TRAJ_BIN), str(raw_in), str(raw_out),
               "8", str(FS), str(BLOCK), str(traj)]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip())
        return np.fromfile(raw_out, dtype="<f4").reshape(-1, 2).astype(np.float64)


def run_oracle_static(x: np.ndarray, ypr) -> np.ndarray:
    with tempfile.TemporaryDirectory() as td:
        raw_in = Path(td) / "in.raw"
        raw_out = Path(td) / "out.raw"
        np.ascontiguousarray(x, dtype="<f4").tofile(raw_in)
        cmd = ["arch", "-x86_64", str(ROT_BIN), str(raw_in), str(raw_out),
               "8", str(FS), str(BLOCK), str(ypr[0]), str(ypr[1]), str(ypr[2])]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip())
        return np.fromfile(raw_out, dtype="<f4").reshape(-1, 2).astype(np.float64)


def main() -> int:
    for b in (ROT_BIN, TRAJ_BIN):
        if not b.exists():
            raise SystemExit(f"{b} not built")
    filters = load_filters()
    x = make_content()
    ok = True

    print("stage 1: step-response timing (step scheduled at frame 60000)")
    y_a = run_oracle_static(x, (0, 0, 0))
    y_b = run_oracle_static(x, (90, 0, 0))
    y_t = run_oracle_traj(x, [(0, (0, 0, 0)), (60000, (90, 0, 0))])
    n = min(len(y_a), len(y_b), len(y_t))
    d_a = np.abs(y_t[:n] - y_a[:n]).max(axis=1)
    d_b = np.abs(y_t[:n] - y_b[:n]).max(axis=1)
    onset = int(np.nonzero(d_a > 1e-7)[0][0])
    settled = int(np.nonzero(d_b > 1e-7)[0][-1])
    boundary = ((60000 // BLOCK) + 1) * BLOCK
    print(f"  departs old orientation at {onset} "
          f"(next block boundary is {boundary}); "
          f"bit-converged to new after {settled} "
          f"(span {settled - onset + 1} samples = one block + IR memory)")
    ok &= boundary <= onset < boundary + 16

    print("stage 2: native trajectory renderer vs oracle")
    cases = [
        ("single step, yaw 90", [(0, (0, 0, 0)), (60000, (90, 0, 0))]),
        ("single step, pitch 40", [(0, (0, 0, 0)), (60000, (0, 40, 0))]),
        # The second update must land on the NEXT block boundary, not the same
        # one. An update at output frame f first takes effect at the boundary
        # b*BLOCK - ADVANCE >= f; for f=60000 that is b=125, out_frame 60416.
        # Putting the second update at exactly 60416 makes both apply in the
        # same block, so the 45-degree orientation renders for zero blocks and
        # the case silently degenerates into "single step, yaw 90" (it was
        # bit-identical to it, to 14 significant digits). 60900 falls inside
        # (60416, 60928], so it lands on b=126 and the intermediate
        # orientation really is rendered for exactly one block.
        ("chained steps in consecutive blocks",
         [(0, (0, 0, 0)), (60000, (45, 0, 0)), (60900, (90, 0, 0))]),
        ("mixed-axis sequence",
         [(0, (0, 0, 0)), (55000, (30, 20, 0)), (70000, (-40, 10, 25)),
          (90000, (0, -30, -10))]),
    ]
    case_names = []
    case_db = []
    case_thresh = []
    for name, upd in cases:
        y_o = run_oracle_traj(x, upd)
        y_n = render_trajectory(x, upd, filters)
        n = min(len(y_o), len(y_n))
        r = residual_db(y_n[GUARD:n], y_o[GUARD:n])
        passed = r < -100
        ok &= passed
        case_names.append(name)
        case_db.append(r)
        case_thresh.append(-100.0)
        print(f"  {'PASS' if passed else 'FAIL'}  {name}: {r:7.1f} dB")

    upd = [(0, (0.0, 0.0, 0.0))]
    for i in range(40):
        upd.append((55000 + i * BLOCK, (2.0 * (i + 1), 0.0, 0.0)))
    y_o = run_oracle_traj(x, upd)
    y_n = render_trajectory(x, upd, filters)
    n = min(len(y_o), len(y_n))
    r = residual_db(y_n[GUARD:n], y_o[GUARD:n])
    passed = r < -90
    ok &= passed
    print(f"  {'PASS' if passed else 'FAIL'}  continuous per-block tracking "
          f"(40-block yaw sweep): {r:7.1f} dB")
    case_names.append("continuous per-block tracking (40-block yaw sweep)")
    case_db.append(r)
    case_thresh.append(-90.0)

    # Additive: persist the per-case dB residuals already printed above
    # (discrete/chained/mixed-axis steps plus the continuous sweep) so
    # tools/plot_validation.py can plot them. Does not change any printed
    # output or the pass/fail verdict below.
    # case_threshold_db travels with the data so the plotter uses the same
    # per-case threshold this script judges against (-100 dB for the discrete
    # steps, -90 dB for the continuous sweep) rather than assuming one value
    # for all of them.
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(
        OUT_NPZ,
        case_names=np.array(case_names),
        case_residual_db=np.array(case_db, dtype=np.float64),
        case_threshold_db=np.array(case_thresh, dtype=np.float64),
        stage1_onset=onset,
        stage1_settled=settled,
        stage1_boundary=boundary,
    )
    print(f"  -> {OUT_NPZ}")

    print()
    print("phase 5: " + ("all checks passed" if ok else "FAILURES above"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
