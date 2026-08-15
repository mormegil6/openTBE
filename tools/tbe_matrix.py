"""The ambiX <-> TBE channel matrix, measured against the real FB360 Encoder.

TBE (the Spatial Workstation calls it HHOA, Hybrid Higher-Order Ambisonics)
carries 8 of the 9 second-order ambisonic harmonics. Each TBE channel is one
ACN harmonic times a fixed gain: there is no mixing across channels, so the
whole encode is a diagonal 16x8 matrix and the decode is its reciprocal.

ACN 6 is absent. That is the harmonic conventionally written R, the
second-order zonal (height) term, and it is why TBE is 8 channels rather than
9. Nothing in the format can carry it, but rotation feeds it under pitch and
roll, so a head-tracked decode still needs its filter. Meta's published 3OA
set includes it (docs/PROTOCOL.md, phase 4).

Provenance of the gains, in order of authority:

1. Measured here against the FB360 Encoder itself (tools/phase7_encode.py,
   docs/PROTOCOL.md "Encode matrix"). The Encoder's own CLI converts TBE to
   first-order ambiX losslessly (24-bit PCM), so the four first-order gains
   are recoverable exactly:

       all four first-order gains = 0.4886025, to 7 significant figures

   The second-order gains are only reachable through a lossy delivery
   container, so they were measured with a tonal probe (each harmonic on its
   own frequency, read back by FFT), giving 0.6308 +- 0.001.

2. Both agree with sqrt((2l+1)/(4*pi)), the N3D normalisation factor:

       l=1: sqrt(3/(4pi))  = 0.48860251...
       l=2: sqrt(5/(4pi))  = 0.63078313...

   so the gains are that closed form, not fitted constants. Note the W
   channel (ACN 0, l=0) takes the l=1 factor, not sqrt(1/(4pi)) = 0.2820948;
   measurement is unambiguous about this (0.4886, not 0.2821).

3. Angelo Farina, "Ambisonics to TBE conversion" (2017),
   https://www.angelofarina.it/TBE-conversion-new.htm. The only public
   documentation this format has, and the source of the channel mapping and
   the signs below. The measurement confirms that table: mapping exactly
   right, signs exactly right, gains agreeing to within 5e-07 on Y, Z and X
   and on all four second-order channels.

   The single exception is W, published as 0.488704 where the Encoder
   applies 0.4886025, a difference of 1.0e-04 or 0.0018 dB, inaudible by
   any measure. The other three first-order entries agree to the last digit
   and the closed form predicts one shared value across all four, so this
   reads as a digit transposed on the way to the page rather than a
   different intent. The measured value is used here, and the difference is
   written down so it can be checked rather than inherited.

The practical size of that correction is 0.0018 dB on one channel, far below
anything audible and far below the accuracy openTBE discloses elsewhere. It
is fixed anyway, because the point of this project is that the numbers come
from measurement rather than from repetition.
"""

from __future__ import annotations

import math

import numpy as np

# sqrt((2l+1)/4pi), the measured gain for each order present in TBE.
G1 = math.sqrt(3.0 / (4.0 * math.pi))      # 0.48860251190292
G2 = math.sqrt(5.0 / (4.0 * math.pi))      # 0.63078313050504

# TBE channel (0-indexed) -> (ambiX ACN index, encode gain).
# Mapping and signs: Farina (2017), corrected form. Gains: measured, above.
# The sibling study's pipeline/tbe/ambix_to_tbe.py uses the published values;
# the two differ only in the W entry.
TBE_FROM_ACN: list[tuple[int, float]] = [
    (0, +G1),    # TBE 0  <- W
    (1, -G1),    # TBE 1  <- Y
    (3, +G1),    # TBE 2  <- X
    (2, +G1),    # TBE 3  <- Z   (ACN 6 / R deliberately not mixed in)
    (8, -G2),    # TBE 4  <- U
    (4, -G2),    # TBE 5  <- V
    (5, -G2),    # TBE 6  <- T
    (7, +G2),    # TBE 7  <- S
]

N_TBE = len(TBE_FROM_ACN)
# Second order needs 9 ambiX channels; higher-order input is accepted and
# truncated, which is exact in ACN/SN3D because reducing order is exactly
# dropping the trailing channels.
N_AMBIX_MIN = 9

# Farina's published gains, kept for tools/phase7_encode.py's comparison and
# so the difference above is checkable rather than merely asserted.
FARINA_PUBLISHED: list[float] = [
    +0.488704, -0.488603, +0.488603, +0.488603,
    -0.630783, -0.630783, -0.630783, +0.630783,
]


def encode_matrix(n_ambix: int = 16) -> np.ndarray:
    """(n_ambix, 8) matrix M with tbe = ambix @ M."""
    if n_ambix < N_AMBIX_MIN:
        raise ValueError(
            f"need at least {N_AMBIX_MIN} ambiX channels (second order), "
            f"got {n_ambix}")
    m = np.zeros((n_ambix, N_TBE))
    for k, (acn, gain) in enumerate(TBE_FROM_ACN):
        m[acn, k] = gain
    return m


def decode_matrix(n_ambix: int = 16) -> np.ndarray:
    """(8, n_ambix) matrix D with ambix = tbe @ D, reciprocal gains.

    ACN 6 (R) stays zero: TBE cannot carry it, so it is not recoverable.
    """
    d = np.zeros((N_TBE, n_ambix))
    for k, (acn, gain) in enumerate(TBE_FROM_ACN):
        d[k, acn] = 1.0 / gain
    return d
