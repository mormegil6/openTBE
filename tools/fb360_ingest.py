"""Extract TBE audio from FB360 Encoder delivery files, for playback here.

Reads both delivery variants (docs/PROTOCOL.md, delivery format):

  mp4   two 4-channel AAC tracks (spatA = TBE 1-4, spatB = TBE 5-8) and an
        optional stereo head-locked track
  mkv   one 10-channel Opus stream (8 TBE + 2 head-locked)

and writes an 8-channel TBE wav plus, when present, a stereo head-locked
wav. The TBE wav then plays through tools/render_native.py or
tools/render_trajectory.py; --binaural renders it directly.

Requires ffmpeg/ffprobe on PATH.

Usage:
    python tools/fb360_ingest.py in.mp4 out_tbe8.wav
    python tools/fb360_ingest.py in.mkv out_tbe8.wav --binaural out_bin.wav
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

sys.path.insert(0, str(Path(__file__).resolve().parent))


def probe(path: Path) -> list[dict]:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip())
    return json.loads(p.stdout)["streams"]


def decode_stream(path: Path, stream_index: int, td: Path) -> tuple[np.ndarray, int]:
    out = td / f"s{stream_index}.wav"
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostats", "-y", "-i", str(path),
         "-map", f"0:{stream_index}", "-c:a", "pcm_f32le", str(out)],
        capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip())
    x, fs = sf.read(out, always_2d=True, dtype="float64")
    return x, fs


def ingest(path: Path) -> tuple[np.ndarray, np.ndarray | None, int]:
    """Return (tbe8, headlocked or None, fs)."""
    audio = [s for s in probe(path) if s.get("codec_type") == "audio"]
    if not audio:
        raise SystemExit(f"{path}: no audio streams")
    chans = [int(s.get("channels", 0)) for s in audio]

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        if chans[0] == 10 and len(audio) == 1:
            x, fs = decode_stream(path, int(audio[0]["index"]), tdp)
            return x[:, :8], x[:, 8:10], fs
        four = [s for s in audio if int(s.get("channels", 0)) == 4]
        stereo = [s for s in audio if int(s.get("channels", 0)) == 2]
        if len(four) == 2:
            a, fs = decode_stream(path, int(four[0]["index"]), tdp)
            b, fs_b = decode_stream(path, int(four[1]["index"]), tdp)
            if fs_b != fs:
                raise SystemExit("sample-rate mismatch between spat tracks")
            n = min(len(a), len(b))
            tbe = np.concatenate([a[:n], b[:n]], axis=1)
            hl = None
            if stereo:
                h, fs_h = decode_stream(path, int(stereo[0]["index"]), tdp)
                if fs_h == fs:
                    hl = h[:n] if len(h) >= n else h
            return tbe, hl, fs
    raise SystemExit(
        f"{path}: unrecognised layout (audio channels {chans}); expected one "
        "10-channel stream or two 4-channel streams")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="FB360 mp4 or mkv")
    ap.add_argument("output", help="8-channel TBE wav to write")
    ap.add_argument("--headlocked-out", default=None)
    ap.add_argument("--binaural", default=None,
                    help="also render fixed-head binaural to this wav")
    ap.add_argument("--subtype", default="FLOAT")
    args = ap.parse_args()

    tbe, hl, fs = ingest(Path(args.input))
    sf.write(args.output, tbe, fs, subtype=args.subtype)
    print(f"{args.input}: TBE {tbe.shape[1]} ch, {len(tbe)/fs:.1f} s "
          f"-> {args.output}")
    if hl is not None and args.headlocked_out:
        sf.write(args.headlocked_out, hl, fs, subtype=args.subtype)
        print(f"  head-locked -> {args.headlocked_out}")

    if args.binaural:
        from render_native import NativeRenderer
        r = NativeRenderer()
        if fs != r.fs:
            raise SystemExit(f"input is {fs} Hz, filters are {r.fs} Hz")
        y = r.render(tbe.astype(np.float32))
        if hl is not None:
            n = min(len(y), len(hl))
            y[:n] += hl[:n]
        sf.write(args.binaural, y, fs, subtype=args.subtype)
        print(f"  binaural -> {args.binaural}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
