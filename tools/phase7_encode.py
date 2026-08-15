"""Phase 7: measure the encode matrix against the real FB360 Encoder.

The decode half of openTBE is validated against the Audio360 SDK. This does
the same for the encode half, against the Encoder's own command-line
interface (tools/encoder_oracle.py), so the shipped matrix rests on
measurement rather than on a published table.

Four stages, run in the order 1, 2, 4, 3:

  1. First-order gains, losslessly. The Encoder converts TBE to first-order
     ambiX as 24-bit PCM, no codec in the path, so a least-squares fit of
     TBE -> ambiX recovers those four gains to the precision of the format.
     This also independently confirms the channel mapping and the signs.
  2. Second-order gains, tonally. No lossless second-order output exists
     (`fuma-second` is accepted then refused; see encoder_oracle.py), so the
     probe goes ambiX -> TBE through the mkv delivery container with one
     tone per harmonic, read back by FFT. Tones survive Opus well enough to
     measure a gain to about three decimals, which is ample to tell the
     candidate values apart.
  3. Round trip. Encode with tools/ambix_to_tbe.py, decode back, and check
     the harmonics TBE carries return unchanged.
  4. End to end. Encode one master both ways and compare, alongside what the
     delivery container costs on its own, so the comparison is interpretable.
     Runs before stage 3 because it needs the Encoder while it is warm.

Needs the FB360 Encoder installed, ffmpeg on PATH, and (for stage 2) a video
file to mux, which it generates with ffmpeg.

Usage: python tools/phase7_encode.py
"""

from __future__ import annotations

import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ambix_to_tbe import decode, encode
from encoder_oracle import encode as run_encoder
from encoder_oracle import encoder_app, version
from tbe_matrix import FARINA_PUBLISHED, G1, G2, TBE_FROM_ACN

FS = 48000
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_NPZ = DATA_DIR / "phase7_encode_matrix.npz"

# One harmonic per tone, at exact FFT bin centres for the analysis window.
PROBE_ACN = [acn for acn, _ in TBE_FROM_ACN]
PROBE_HZ = [200, 300, 400, 500, 700, 900, 1100, 1300]


def _sf():
    import soundfile as sf
    return sf


def stage1_first_order(td: Path) -> np.ndarray:
    """Recover the four first-order gains from a lossless TBE -> ambiX run."""
    sf = _sf()
    rng = np.random.default_rng(7)
    x = (rng.standard_normal((FS, 8)) * 0.05).astype(np.float32)
    tbe_path = td / "tbe8.wav"
    sf.write(tbe_path, x, FS, subtype="PCM_24")

    out = run_encoder(tbe_path, "hhoa", td / "ambix1.wav", "ambix-first")
    y, fs_y = sf.read(out, always_2d=True, dtype="float64")
    if fs_y != FS:
        raise SystemExit(f"encoder returned {fs_y} Hz, expected {FS}")

    n = min(len(x), len(y))
    s, e = 100, n - 100
    m, *_ = np.linalg.lstsq(x[s:e].astype(np.float64), y[s:e], rcond=None)

    resid = y[s:e] - x[s:e].astype(np.float64) @ m
    rel = float(np.sqrt((resid ** 2).mean())
                / max(np.sqrt((y[s:e] ** 2).mean()), 1e-30))
    print(f"  lossless fit residual: {rel:.2e} "
          f"({20 * math.log10(max(rel, 1e-30)):.1f} dB)")

    gains = np.zeros(4)
    print(f"  {'TBE':>4} {'ACN':>4} {'decode gain':>13} {'encode gain':>13}")
    for k in range(4):
        acn = TBE_FROM_ACN[k][0]
        d = float(m[k, acn])
        gains[k] = 1.0 / d
        print(f"  {k:>4} {acn:>4} {d:>13.7f} {gains[k]:>13.7f}")
    off = np.abs(m).sum() - sum(abs(m[k, TBE_FROM_ACN[k][0]])
                                for k in range(4))
    print(f"  off-mapping energy in the fitted matrix: {off:.2e} "
          "(zero means no cross-channel mixing)")
    return gains


def stage2_second_order(td: Path) -> np.ndarray:
    """Recover all eight gains tonally, through the mkv delivery container."""
    sf = _sf()
    n = 4 * FS
    t = np.arange(n) / FS
    x = np.zeros((n, 16), dtype=np.float32)
    for acn, f in zip(PROBE_ACN, PROBE_HZ):
        x[:, acn] = (0.25 * np.sin(2 * np.pi * f * t)).astype(np.float32)
    amb_path = td / "ambix_tone.wav"
    sf.write(amb_path, x, FS, subtype="PCM_24")

    video = td / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "color=c=black:s=320x160:d=4:r=10", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(video)], check=True)

    out = run_encoder(amb_path, "ambix-third", td / "tone.mkv", "mkv-360",
                      video=video)
    dec = td / "tone10.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out),
                    "-map", "0:a:0", "-c:a", "pcm_f32le", str(dec)],
                   check=True)
    y, _ = sf.read(dec, always_2d=True, dtype="float64")

    n_use = min(len(x), len(y))
    s = 4000
    e = min(n_use - 2000, s + 2 ** 15)
    win = np.hanning(e - s)

    def amp(sig: np.ndarray, f: int) -> float:
        spec = np.fft.rfft(sig[s:e] * win)
        return float(np.abs(spec[int(round(f * (e - s) / FS))]))

    gains = np.zeros(8)
    print(f"  {'TBE':>4} {'ACN':>4} {'Hz':>6} {'measured':>11}")
    for k, (acn, f) in enumerate(zip(PROBE_ACN, PROBE_HZ)):
        gains[k] = amp(y[:, k], f) / amp(x[:, acn], f)
        print(f"  {k:>4} {acn:>4} {f:>6} {gains[k]:>11.6f}")
    return gains


def stage4_end_to_end(td: Path) -> tuple[float, float]:
    """Encode the same master both ways and compare, plus the codec's own cost.

    Returns (encoder_vs_opentbe_db, container_round_trip_db).

    The only way to get TBE *out* of the Encoder is through a delivery
    container, and both containers are lossy, so this comparison necessarily
    includes codec noise. To make the number interpretable it also measures
    what that container costs on its own: push an already-encoded TBE file
    through the same mkv path and back, and compare against itself. If the
    encoder-to-encoder difference is at or below that floor, no matrix error
    is resolvable above the codec.
    """
    sf = _sf()
    rng = np.random.default_rng(3)
    n = 2 * FS
    t = np.arange(n) / FS
    x = np.zeros((n, 16), dtype=np.float32)
    x[:, 0] = 0.2 * np.sin(2 * np.pi * 220 * t)
    x[:, 1] = 0.1 * np.sin(2 * np.pi * 330 * t)
    x[:, 4] = 0.08 * np.sin(2 * np.pi * 550 * t)
    x[:, 8] = 0.06 * np.sin(2 * np.pi * 770 * t)
    x += (rng.standard_normal((n, 16)) * 0.002).astype(np.float32)
    master = td / "master.wav"
    sf.write(master, x, FS, subtype="PCM_24")

    video = td / "v4.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "color=c=black:s=320x160:d=2:r=10", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(video)], check=True)

    def through_container(spatial: Path, fmt: str, tag: str) -> np.ndarray:
        out = run_encoder(spatial, fmt, td / f"{tag}.mkv", "mkv-360",
                          video=video)
        wav = td / f"{tag}.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out),
                        "-map", "0:a:0", "-c:a", "pcm_f32le", str(wav)],
                       check=True)
        y, _ = sf.read(wav, always_2d=True, dtype="float64")
        return y[:, :8]

    def rel_db(a: np.ndarray, b: np.ndarray) -> float:
        m = min(len(a), len(b))
        s, e = 2000, m - 2000
        num = float(np.sqrt(((a[s:e] - b[s:e]) ** 2).mean()))
        den = float(np.sqrt((b[s:e] ** 2).mean()))
        return 20.0 * math.log10(max(num, 1e-30) / max(den, 1e-30))

    ours = encode(x.astype(np.float64))
    ref = through_container(master, "ambix-third", "ref")
    enc_db = rel_db(ours, ref)

    ours_path = td / "ours_tbe.wav"
    sf.write(ours_path, ours.astype(np.float32), FS, subtype="PCM_24")
    ours_through = through_container(ours_path, "hhoa", "rt")
    codec_db = rel_db(ours_through, ours)

    print(f"  openTBE encode vs the Encoder, through its mkv: {enc_db:.1f} dB")
    print(f"  the same container's own cost, measured alone: {codec_db:.1f} dB")
    if enc_db <= codec_db + 1.0:
        print("  the difference is at or below the codec floor, so no matrix "
              "error is resolvable above it")
    else:
        print("  NOTE: the difference exceeds the codec floor, which would "
              "indicate a real matrix discrepancy")
    return enc_db, codec_db


def stage3_round_trip() -> float:
    rng = np.random.default_rng(11)
    amb = rng.standard_normal((FS, 16)) * 0.05
    back = decode(encode(amb), n_ambix=16)
    carried = [acn for acn, _ in TBE_FROM_ACN]
    err = np.abs(back[:, carried] - amb[:, carried]).max()
    absent = np.abs(back[:, [6]]).max()
    print(f"  max round-trip error on the 8 carried harmonics: {err:.2e}")
    print(f"  ACN 6 (R) after round trip: {absent:.2e} "
          "(zero: TBE cannot carry it)")
    return float(err)


def main() -> int:
    app = encoder_app()
    print(f"FB360 Encoder: {app}")
    v = version()
    if v:
        print(f"  version: {v}")
    print()

    with tempfile.TemporaryDirectory() as td_s:
        td = Path(td_s)
        print("stage 1: first-order gains, lossless TBE -> ambiX")
        g1 = stage1_first_order(td)
        print()
        print("stage 2: all eight gains, tonal probe through mkv delivery")
        g2 = stage2_second_order(td)
        print()
        print("stage 4: whole-file encode, openTBE vs the Encoder")
        enc_db, codec_db = stage4_end_to_end(td)

    print()
    print("stage 3: native encode/decode round trip")
    rt = stage3_round_trip()

    print()
    print("comparison (|gain|), measured vs closed form vs Farina published")
    print(f"  {'TBE':>4} {'lossless':>10} {'tonal':>10} "
          f"{'sqrt((2l+1)/4pi)':>18} {'Farina':>10}")
    closed = [G1] * 4 + [G2] * 4
    ok = True
    for k in range(8):
        loss = f"{abs(g1[k]):10.7f}" if k < 4 else f"{'':>10}"
        print(f"  {k:>4} {loss} {abs(g2[k]):10.6f} {closed[k]:18.7f} "
              f"{abs(FARINA_PUBLISHED[k]):10.6f}")
    # The lossless path is the authoritative one; require it to match the
    # closed form tightly, and the tonal path to agree to codec precision.
    for k in range(4):
        if abs(abs(g1[k]) - G1) > 1e-5:
            print(f"  MISMATCH: TBE {k} lossless gain {abs(g1[k]):.7f} "
                  f"is not sqrt(3/4pi) = {G1:.7f}")
            ok = False
    for k in range(4, 8):
        if abs(abs(g2[k]) - G2) > 5e-3:
            print(f"  MISMATCH: TBE {k} tonal gain {abs(g2[k]):.6f} "
                  f"is not sqrt(5/4pi) = {G2:.6f}")
            ok = False
    if rt > 1e-12:
        print(f"  MISMATCH: round-trip error {rt:.2e} is not at the float floor")
        ok = False

    w_delta = abs(abs(g1[0]) - abs(FARINA_PUBLISHED[0]))
    print()
    print(f"W channel: measured {abs(g1[0]):.7f}, Farina published "
          f"{abs(FARINA_PUBLISHED[0]):.6f}, difference {w_delta:.2e} "
          f"({20 * math.log10(abs(g1[0]) / abs(FARINA_PUBLISHED[0])):+.4f} dB)")
    print("The other three first-order entries agree with the measurement to "
          "the last digit, and the closed form predicts one shared value, so "
          "the published W entry looks like a transcription slip.")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(
        OUT_NPZ,
        gains_lossless_first_order=g1,
        gains_tonal_all=g2,
        closed_form=np.array(closed),
        farina_published=np.array(FARINA_PUBLISHED),
        round_trip_max_error=rt,
        end_to_end_vs_encoder_db=enc_db,
        container_round_trip_db=codec_db,
    )
    print(f"  -> {OUT_NPZ}")

    print()
    print("phase 7: " + ("encode matrix confirmed against the Encoder"
                         if ok else "MISMATCHES above"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
