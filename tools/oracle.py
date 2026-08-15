"""Drive the Audio360 SDK reference decoder (the oracle) from Python.

The oracle is the `tbe_render` helper built in the immersive-formats-evaluation
study (pipeline/tbe/): it feeds raw float32 through the SDK's offline
getAudioMix() path with the audio device disabled. The SDK dylib is x86_64
only, so on Apple Silicon the helper runs under Rosetta via `arch -x86_64`.

The oracle directory is resolved from the OPENTBE_ORACLE_DIR environment
variable, which must be set: no local path is assumed. It must contain the
built `tbe_render` binary next to the SDK's `lib/` directory.

Known behaviour, measured on 2026-08-14 (SDK 1.7.12, fs 48000, block 512):
the engine discards roughly the first 7 blocks of the stream during warm-up,
so the output is time-advanced by about 3583 samples relative to the input
and any input event inside the warm-up window is lost. Callers must place
probe signals after a guard interval; see phase0_lti.py, which measures the
exact offset for a given block size.
"""

from __future__ import annotations

import os
import platform
import subprocess
import tempfile
from pathlib import Path

import numpy as np

ROT_HELPER = Path(__file__).resolve().parent.parent / "bin" / "tbe_render_rot"


def oracle_binary(oracle_dir: Path | None = None) -> Path:
    """The fixed-head oracle binary.

    Prefers this repository's own bin/tbe_render_rot, which renders at a
    given orientation and so covers the fixed-head case at (0, 0, 0). That
    keeps openTBE self-contained: tools/get_sdk_filters.py can build it from
    tools/tbe_render_rot.cpp given only an SDK.

    Falls back to the sibling study's `tbe_render` in OPENTBE_ORACLE_DIR,
    which is what earlier measurements used.
    """
    if ROT_HELPER.exists():
        return ROT_HELPER
    d = Path(oracle_dir) if oracle_dir else os.environ.get("OPENTBE_ORACLE_DIR")
    if d is None:
        raise FileNotFoundError(
            "No oracle available. Run 'python tools/get_sdk_filters.py', "
            "which builds bin/tbe_render_rot from an SDK it finds, or set "
            "OPENTBE_ORACLE_DIR to a directory holding a built tbe_render "
            "helper and the SDK's lib/. See docs/REPRODUCING.md."
        )
    helper = Path(d) / "tbe_render"
    if not helper.exists():
        raise FileNotFoundError(
            f"oracle not found: neither {ROT_HELPER} nor {helper}. Run "
            "'python tools/get_sdk_filters.py' or see docs/REPRODUCING.md."
        )
    return helper


def render(
    x: np.ndarray,
    fs: int = 48000,
    block: int = 512,
    oracle_dir: Path | None = None,
) -> np.ndarray:
    """Render an (n, 8) or (n, 10) TBE array to (m, 2) binaural float64.

    The output is what the oracle produces, untouched: the length is
    input + 1 s of tail, and the warm-up time advance is not compensated here.
    """
    if x.ndim != 2 or x.shape[1] not in (8, 10):
        raise ValueError(f"expected (n, 8) or (n, 10) input, got {x.shape}")
    helper = oracle_binary(oracle_dir)

    with tempfile.TemporaryDirectory() as td:
        raw_in = Path(td) / "in.raw"
        raw_out = Path(td) / "out.raw"
        np.ascontiguousarray(x, dtype="<f4").tofile(raw_in)

        cmd = [
            str(helper), str(raw_in), str(raw_out),
            str(x.shape[1]), str(fs), str(block),
        ]
        # tbe_render_rot takes an orientation; (0, 0, 0) is the fixed-head
        # case, and its output is identical to the study's tbe_render there.
        if helper == ROT_HELPER:
            cmd += ["0", "0", "0"]
        if platform.machine() == "arm64":
            cmd = ["arch", "-x86_64"] + cmd
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"tbe_render failed ({proc.returncode}): {proc.stderr.strip()}"
            )
        return np.fromfile(raw_out, dtype="<f4").reshape(-1, 2).astype(np.float64)


def residual_db(test: np.ndarray, ref: np.ndarray) -> float:
    """RMS of (test - ref) relative to RMS of ref, in dB. -inf when identical."""
    n = min(len(test), len(ref))
    ref_rms = float(np.sqrt((ref[:n] ** 2).mean()))
    if ref_rms == 0.0:
        return float("nan")
    diff_rms = float(np.sqrt(((test[:n] - ref[:n]) ** 2).mean()))
    if diff_rms == 0.0:
        return float("-inf")
    return 20.0 * np.log10(diff_rms / ref_rms)
