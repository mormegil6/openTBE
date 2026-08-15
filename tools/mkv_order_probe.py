"""Measure the channel order of the FB360 Encoder's Matroska output.

The Encoder's mkv delivery ("FB360 Matroska", CLI name mkv-360) carries one
10-channel Opus stream. Which channel is which was an inference for most of
this project's life: no Encoder-produced mkv survived in the sibling study's
archive, so the ordering rested on the mp4 track layout plus the Encoder's
own tbe_8.2 metadata tag. This script turned it into a measurement.

Method: feed the Encoder an 8-channel TBE input (CLI format "hhoa", GUI name
"Spatial Workstation 8 channel") plus a stereo head-locked bed, where every
channel carries three identifiers that each survive Opus on their own: a
unique tone frequency, a unique level on a -3 dB ladder, and a 25 ms noise
burst at a unique time. Read the produced mkv back and every output channel
names itself three times over.

Measured 2026-08-15, Encoder v3.3.3 under Rosetta, through the app's GUI and
its CLI separately, since the two are known to differ elsewhere (phase 8:
quad-binaural exists only in the GUI). Both give the same answer:

    channels 0-7   TBE 1-8
    channels 8-9   head-locked left, right
    OpusHead       mapping family 255, 10 uncoupled streams, identity
                   mapping, pre-skip 312

which is exactly what tools/fb360_package.py writes and tools/fb360_ingest.py
assumes. Full record: docs/PROTOCOL.md, phase 6.

    python tools/mkv_order_probe.py --make DIR      write the probe inputs
    python tools/mkv_order_probe.py --analyze FILE  identify each channel
    python tools/mkv_order_probe.py --cli DIR       make, encode and analyze
                                                    in one go (needs the
                                                    Encoder app and ffmpeg)

For a GUI run: --make, then load the three files exactly as the Encoder
window groups them (spatial: probe_hhoa.wav as "Spatial Workstation 8
channel", "From Pro Tools" unchecked; head-locked: probe_headlocked.wav;
video: probe_video.mp4, monoscopic), select the FB360 Matroska output, and
--analyze whatever it saves.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

FS = 48000
DUR = 12.0
N = int(FS * DUR)

# Non-harmonically related, all comfortably inside Opus's reliable band.
TBE_FREQS = [425.0, 550.0, 725.0, 950.0, 1225.0, 1550.0, 1900.0, 2300.0]
HL_FREQS = [2750.0, 3250.0]
BURST_T0, BURST_STEP = 2.0, 0.8
BASE_AMP, LADDER_DB = 0.35, -3.0

EXPECT = ([(f"TBE {i + 1}", f) for i, f in enumerate(TBE_FREQS)]
          + [("HL L", HL_FREQS[0]), ("HL R", HL_FREQS[1])])


def _channel(freq: float, step: int, seed: int) -> np.ndarray:
    t = np.arange(N) / FS
    amp = BASE_AMP * 10 ** (LADDER_DB * step / 20.0)
    x = amp * np.sin(2 * np.pi * freq * t)
    n0 = int((BURST_T0 + BURST_STEP * step) * FS)
    n1 = n0 + int(0.025 * FS)
    x[n0:n1] += np.random.default_rng(seed).standard_normal(n1 - n0) * 0.4
    return np.clip(x, -0.98, 0.98)


def make(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    hhoa = np.stack([_channel(f, k, 100 + k)
                     for k, f in enumerate(TBE_FREQS)], axis=1)
    sf.write(out_dir / "probe_hhoa.wav", hhoa, FS, subtype="PCM_24")
    hl = np.stack([_channel(f, 8 + k, 300 + k)
                   for k, f in enumerate(HL_FREQS)], axis=1)
    sf.write(out_dir / "probe_headlocked.wav", hl, FS, subtype="PCM_24")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=1920x960:r=30",
                    "-t", str(DUR), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(out_dir / "probe_video.mp4")], check=True)
    (out_dir / "probe_reference.json").write_text(json.dumps(
        dict(fs=FS, dur=DUR, tbe_freqs=TBE_FREQS, hl_freqs=HL_FREQS,
             burst_t0=BURST_T0, burst_step=BURST_STEP,
             level_ladder_db_per_step=LADDER_DB, base_amp=BASE_AMP),
        indent=2))
    print(f"probe inputs written to {out_dir}")


def _opus_head(path: Path) -> str:
    with tempfile.TemporaryDirectory() as td:
        mka = Path(td) / "a.mka"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-i", str(path), "-map", "0:a:0", "-c", "copy",
                        str(mka)], check=True)
        raw = mka.read_bytes()
    i = raw.find(b"OpusHead")
    if i < 0:
        return "no OpusHead found (not an Opus stream?)"
    h = raw[i:i + 40]
    return (f"channels {h[9]}, pre-skip {int.from_bytes(h[10:12], 'little')}, "
            f"mapping family {h[18]}, streams {h[19]}, coupled {h[20]}, "
            f"map {list(h[21:21 + h[9]])}")


def analyze(path: Path) -> int:
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "audio.wav"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-i", str(path), "-map", "0:a:0", "-c:a", "pcm_f32le",
                        str(wav)], check=True)
        x, fs = sf.read(str(wav), dtype="float64")
    n_ch = x.shape[1]
    fs_i = int(fs)
    print(f"{path}: {n_ch} channels at {fs} Hz, {len(x) / fs:.2f} s")
    print(f"  OpusHead: {_opus_head(path)}")
    print(f"{'ch':>4} {'tone Hz':>8} {'level dB':>9} {'burst s':>8}   verdict")

    ok = True
    for c in range(n_ch):
        seg = x[fs_i // 2: fs_i + fs_i // 2, c]        # 0.5..1.5 s, tones only
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        f_peak = np.fft.rfftfreq(len(seg), 1 / fs)[int(np.argmax(spec))]
        level = 20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-12)
        env = np.abs(x[:, c])
        k = fs_i // 100
        smooth = np.convolve(env, np.ones(k) / k, mode="same")
        # search only where bursts can be; convolve's edges spike spuriously
        lo, hi = fs_i // 2, len(env) - fs_i // 2
        burst_t = float((lo + np.argmax((env - 4 * smooth)[lo:hi])) / fs)
        name, f_exp = EXPECT[c] if c < len(EXPECT) else ("??", 0.0)
        t_exp = BURST_T0 + BURST_STEP * c
        good = abs(f_peak - f_exp) < 15.0 and abs(burst_t - t_exp) < 0.1
        ok &= good
        verdict = (f"= {name}" if good else
                   f"expected {name}: {f_exp:.0f} Hz, burst {t_exp:.1f} s")
        print(f"{c:>4} {f_peak:>8.1f} {level:>9.1f} {burst_t:>8.3f}   "
              f"{verdict}")

    if ok and n_ch == 10:
        print("channel order matches TBE 1-8 + head-locked L/R exactly")
        return 0
    if ok:
        print(f"all {n_ch} present channels match, but 10 were expected")
    else:
        print("MISMATCH, see rows above")
    return 1


def cli(work_dir: Path) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from encoder_oracle import encode, version
    make(work_dir)
    print(f"running {version()}")
    out = encode(spatial=work_dir / "probe_hhoa.wav", spatial_format="hhoa",
                 headlocked=work_dir / "probe_headlocked.wav",
                 video=work_dir / "probe_video.mp4",
                 output=work_dir / "cli_out.mkv", output_format="mkv-360")
    return analyze(out)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--make", metavar="DIR", help="write the probe inputs")
    g.add_argument("--analyze", metavar="FILE",
                   help="identify each channel of a produced mkv")
    g.add_argument("--cli", metavar="DIR",
                   help="make, encode via the Encoder CLI, and analyze")
    args = ap.parse_args()
    if args.make:
        make(Path(args.make))
        return 0
    if args.analyze:
        return analyze(Path(args.analyze))
    return cli(Path(args.cli))


if __name__ == "__main__":
    raise SystemExit(main())
