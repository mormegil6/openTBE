"""Real-spherical-harmonic rotation for the TBE head-tracked decode.

The decode chain for a rotated listener is: reconstruct the ambiX ACN/SN3D
harmonics from TBE (each TBE channel is one harmonic times a fixed gain;
harmonic ACN 6 (R) is absent from TBE and enters as zero), rotate the
sound field by the inverse of the head rotation, then apply the fixed
Ambisonics-to-binaural decode (one mono IR per harmonic, L/R combined by
the sign of m, exactly Meta's published decoder algorithm).

The order-2 rotation matrix is not hand-coded from recurrence formulas.
It is solved numerically from the basis functions themselves: evaluate the
9 ambiX SN3D basis functions at a few hundred random directions, rotate
the directions, and solve the (overdetermined) linear system for the
matrix mapping unrotated to rotated coefficients. For band-limited order-2
functions this is exact to float precision, and the solve's residual is
asserted, so a convention error in the basis functions cannot pass
silently.

Axis convention: ambiX (x front, y left, z up). The mapping from the SDK's
yaw/pitch/roll arguments to a rotation matrix in these axes is fixed
empirically against the oracle in phase4_headtrack.py, not assumed.
"""

from __future__ import annotations

import numpy as np


def sh_basis_acn_sn3d(dirs: np.ndarray) -> np.ndarray:
    """Evaluate the 9 ambiX (ACN, SN3D) basis functions at unit vectors.

    dirs: (n, 3) array of unit vectors in ambiX axes (x front, y left, z up).
    Returns (n, 9).
    """
    x, y, z = dirs[:, 0], dirs[:, 1], dirs[:, 2]
    s3 = np.sqrt(3.0)
    return np.stack(
        [
            np.ones_like(x),          # ACN 0  W
            y,                        # ACN 1  Y  (l=1, m=-1)
            z,                        # ACN 2  Z  (l=1, m=0)
            x,                        # ACN 3  X  (l=1, m=+1)
            s3 * x * y,               # ACN 4  V  (l=2, m=-2)
            s3 * y * z,               # ACN 5  T  (l=2, m=-1)
            0.5 * (3 * z * z - 1),    # ACN 6  R  (l=2, m=0)
            s3 * x * z,               # ACN 7  S  (l=2, m=+1)
            0.5 * s3 * (x * x - y * y),  # ACN 8  U  (l=2, m=+2)
        ],
        axis=1,
    )


def sh_rotation_matrix(rot3: np.ndarray, n_dirs: int = 600,
                       seed: int = 0) -> np.ndarray:
    """The 9x9 matrix M such that coefficients transform as a' = M @ a when
    the sound field is rotated by rot3 (a 3x3 rotation matrix in ambiX axes).

    Solved from the defining property of the rotated basis; block structure
    (no mixing across l) and solve exactness are asserted.
    """
    rng = np.random.default_rng(seed)
    v = rng.standard_normal((n_dirs, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)

    # A field with coefficients a evaluates as f(u) = Y(u) @ a. Rotating the
    # field by R means f'(u) = f(R^T u), so Y(u) @ a' = Y(R^T u) @ a for all
    # u, giving M from the least-squares solve below.
    Yu = sh_basis_acn_sn3d(v)                    # (n, 9)
    Yru = sh_basis_acn_sn3d(v @ rot3)            # evaluates Y(R^T u) rowwise
    M, res, rank, _ = np.linalg.lstsq(Yu, Yru, rcond=None)
    M = M.T
    check = float(np.abs(Yru - Yu @ M.T).max())
    if check > 1e-10:
        raise RuntimeError(f"SH rotation solve residual {check:.2e}; "
                           "basis or rotation matrix is inconsistent")
    # no mixing between different orders
    if float(np.abs(M[0, 1:]).max()) > 1e-10 or \
       float(np.abs(M[1:4, 4:]).max()) > 1e-10 or \
       float(np.abs(M[4:, 1:4]).max()) > 1e-10:
        raise RuntimeError("SH rotation matrix mixes orders; convention bug")
    return M


def rot_axis(axis: str, deg: float) -> np.ndarray:
    """Rotation matrix about one ambiX axis: 'x' front, 'y' left, 'z' up."""
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    if axis == "z":
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)
    raise ValueError(axis)


def listener_rotation_matrix(yaw: float, pitch: float, roll: float,
                             signs: tuple[int, int, int] = (1, 1, 1),
                             order: str = "zyx") -> np.ndarray:
    """3x3 field-rotation matrix for the SDK's yaw/pitch/roll listener args.

    The listener turning one way rotates the sound field the other way, and
    the SDK's sign and composition conventions are not documented beyond
    "yaw negative is left, pitch positive is up". The signs tuple and axis
    order are therefore free parameters here; phase4_headtrack.py fits them
    against the oracle once and records the result in docs/PROTOCOL.md.
    Axis letters map yaw to z (up), pitch to y (left), roll to x (front).
    """
    angle = {"z": signs[0] * yaw, "y": signs[1] * pitch, "x": signs[2] * roll}
    m = np.eye(3)
    for ax in order:
        m = m @ rot_axis(ax, angle[ax])
    return m
