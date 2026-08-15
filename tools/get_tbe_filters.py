"""Generate the TBE-domain filter set from Meta's MIT-licensed coefficients.

Parses docs/upstream/audio360-mit/AmbiBinauralCoefficients3OA.cpp and
produces the 8-channel TBE filter set openTBE ships, using only that source:
no code path here touches the proprietary SDK or any local measurement.

Why the 3OA file and not 2OA. Meta published both. TBE is a second-order
format, so 2OA looks like the obvious choice, and openTBE used it at first;
that was wrong and it cost this project a long detour. The engine decodes
TBE through the third-order path, and the give-away is in the declared tap
counts, before any audio is compared:

    2OA  {180, 184, 181, 77, 80, 179, 183, 84, 185}
    3OA  {180, 183, 182, 77, 73, 179, 183, 84, 185, ...}
    measured, per TBE channel, mapped to its ACN:
         {180, 183, 182, 77, 73, 179,  --, 84, 185}

Eight of eight match 3OA exactly (ACN 6 has no measured entry: TBE does not
carry that harmonic, so phase 1 could not probe it). Against 2OA the per-channel residuals run
-8 to -38 dB, which this project once wrote up as the SDK having a revised
private filter set; against 3OA they run -136 to -149 dB, the float floor.
See docs/PROTOCOL.md, "RESOLVED: the SDK uses the 3OA coefficient set".

Derivation, from first principles, not curve-fitting:

1. docs/upstream/audio360-mit/AmbiSphericalConvolution.cpp (Meta's own MIT
   decoder) convolves each ACN harmonic with ONE mono published IR, then
   combines: harmonics with m >= 0 go to L and R identically; harmonics with
   m < 0 go to L as +f and to R as -f. So a harmonic's stereo contribution
   is fully determined by its mono IR and the sign of m.
2. tools/tbe_matrix.py encodes TBE channel k as a single ACN harmonic times
   a fixed gain, with no mixing across channels, measured against the real
   FB360 Encoder (tools/phase7_encode.py).
3. Composing the two: decoding TBE(k) through "the MIT algorithm applied to
   the reconstructed ACN(acn(k)) = TBE(k) / gain(k)" is identical to
   convolving TBE(k) directly with published_IR(acn(k)) / gain(k),
   sign-combined into L/R per the m rule. That quotient is what this script
   computes. No parameter is fit to data anywhere in this file.

Verified against the SDK: the resulting filters reproduce its decode to
about -134 dB on programme-like material and -132 to -134 dB at every
tested listener orientation, i.e. the arithmetic floor. Nothing proprietary
is needed to obtain that; the local measurement (tools/phase1_capture.py)
now serves only as an independent cross-check.

Usage:
    python tools/get_tbe_filters.py            # writes data/tbe8_filters_mit.npz
    python tools/get_tbe_filters.py --verify   # compare against a local
                                               # measurement, if one exists
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CPP = HERE.parent / "docs" / "upstream" / "audio360-mit" / "AmbiBinauralCoefficients3OA.cpp"
DEFAULT_OUT = HERE.parent / "data" / "tbe8_filters_mit.npz"
MEASURED_NPZ = HERE.parent / "data" / "tbe8_ir_48k_block512.npz"

# TBE channel (0-indexed) -> (ambiX ACN index, encode gain), from
# tools/tbe_matrix.py: mapping and signs per Farina (2017, corrected form),
# gains measured against the real FB360 Encoder (tools/phase7_encode.py).
#
# This import is the one dependency outside docs/upstream/, and it is a plain
# table of constants, not code that touches the SDK or the measurement. The
# filter data below still descends only from Meta's MIT-licensed source; the
# gains are a property of the ambiX-to-TBE format, measured separately.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tbe_matrix import TBE_FROM_ACN  # noqa: E402


def acn_lm(n: int) -> tuple[int, int]:
    l = int(np.floor(np.sqrt(n)))
    return l, n - l * l - l


def parse_mit_harmonics(cpp_path: Path) -> dict[int, np.ndarray]:
    src = cpp_path.read_text()
    taps = [int(x) for x in re.search(
        r"kNumTaps_3c_48000\[kNum3OAHarmonics\] = \{([^}]+)\}", src
    ).group(1).split(",")]
    arrays = re.findall(r"const float ambiIR_3c_48000_(\d+)\[\d+\] = \{([^}]+)\};", src)
    if not arrays:
        raise RuntimeError(f"could not find harmonic IR arrays in {cpp_path}")
    harmonics: dict[int, np.ndarray] = {}
    for idx_str, body in arrays:
        idx = int(idx_str)
        vals = np.array(
            [float(x.strip().rstrip("f")) for x in body.split(",") if x.strip()],
            dtype=np.float32,
        )
        if len(vals) != taps[idx]:
            raise RuntimeError(
                f"harmonic {idx}: parsed {len(vals)} taps, header declares {taps[idx]}"
            )
        harmonics[idx] = vals
    if not set(range(9)) <= set(harmonics):
        raise RuntimeError(
            f"expected at least harmonics 0..8, got {sorted(harmonics)}")
    return harmonics


def build_tbe_filters(harmonics: dict[int, np.ndarray]) -> np.ndarray:
    """(8, max_len, 2) TBE-domain filter set, mono published IR divided by
    the encode gain, sign-combined into L/R per the m>=0 / m<0 decoder rule."""
    max_len = max(len(v) for v in harmonics.values())
    h = np.zeros((8, max_len, 2), dtype=np.float32)
    for k, (acn, gain) in enumerate(TBE_FROM_ACN):
        l, m = acn_lm(acn)
        mono = (harmonics[acn] / gain).astype(np.float32)
        if m >= 0:
            h[k, : len(mono), 0] = mono
            h[k, : len(mono), 1] = mono
        else:
            h[k, : len(mono), 0] = mono
            h[k, : len(mono), 1] = -mono
    return h


def verify(h: np.ndarray) -> None:
    if not MEASURED_NPZ.exists():
        print(f"no local measurement at {MEASURED_NPZ}; skipping verification "
              "(this is expected on a machine without the SDK/measurement)")
        return
    d = np.load(MEASURED_NPZ)
    meas = d["h"].astype(np.float64)
    print(f"{'TBE':>3}  {'residual_dB':>11}  {'corr':>6}")
    for k in range(8):
        n = int(np.nonzero(h[k, :, 0])[0][-1]) + 1 if np.any(h[k]) else 0
        pub = h[k, :n, 0].astype(np.float64)
        m = meas[k, :n, 0]
        resid = m - pub
        resid_db = 20 * np.log10(
            max(np.sqrt((resid**2).mean()), 1e-30) / max(np.sqrt((m**2).mean()), 1e-30)
        )
        corr = float(np.corrcoef(pub, m)[0, 1]) if n > 1 else float("nan")
        print(f"{k:>3}  {resid_db:>11.1f}  {corr:>6.3f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    harmonics = parse_mit_harmonics(CPP)
    print(f"parsed {len(harmonics)} MIT-published harmonic IRs from {CPP.name}")
    h = build_tbe_filters(harmonics)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        out,
        h=h,
        fs=48000,
        # Timing (advance, block) is a property of the oracle engine's warm-up,
        # not of the filter data; kept identical to the measured capture's
        # convention (docs/PROTOCOL.md) so this npz is a drop-in for
        # render_native.py regardless of which filter source is loaded.
        advance=3584,
        block=512,
        ir_len=h.shape[1],
        source="facebookarchive/Audio360 AmbiBinauralCoefficients3OA.cpp, MIT",
        source_commit="171bfbfa69c4724026ef8d06a0f5155b1a9de32b",
        note="TBE-domain filters derived from Meta's published 3OA Ambisonics "
             "IRs. Reproduces the Audio360 SDK to about -134 dB; see "
             "docs/PROTOCOL.md for the derivation and the measurements.",
    )
    print(f"-> {out}")

    if args.verify:
        print()
        verify(h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
