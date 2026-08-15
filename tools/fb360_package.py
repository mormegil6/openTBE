"""Package TBE audio and video into the FB360 Encoder's delivery formats.

Reproduces, with modern open tools, what the FB360 Encoder's packaging
stage did (recovered from the Encoder's own logs; docs/PROTOCOL.md,
delivery format):

  mp4 variant   spatial audio split into two 4-channel AAC tracks
                (spatA = TBE 1-4, spatB = TBE 5-8) plus stereo head-locked,
                muxed with the video by ffmpeg, then MP4Box attaches one
                "face" metadata item per audio track (an fb360
                AudioChannelConfiguration XML: tbe_8a, tbe_8b, headlocked)
                and an encoder-metadata XML at file level.
  mkv variant   all 10 channels (8 TBE + 2 head-locked) in a single Opus
                stream (mapping family 255, the only family that carries
                10 discrete channels), muxed by ffmpeg with fb360 global
                and per-stream tags plus Google spherical-video RDF.

Input spatial audio is an 8-channel TBE wav, or a 16-channel (or higher)
ambiX wav which is first encoded to TBE with the same published matrix the
study uses. Head-locked stereo is optional; silence is synthesised when
absent, since both variants structurally carry the head-locked track.

Requires ffmpeg and MP4Box on PATH.

Usage:
    python tools/fb360_package.py audio.wav video.mp4 out.mp4
    python tools/fb360_package.py audio.wav video.mp4 out.mkv --variant mkv
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase4_headtrack import TBE_FROM_ACN

ENCODER_META = """<fb360>
  <audio>
    <encoder_metadata name="opentbe fb360_package (FB360 Encoder compatible)"
                      version="0.1">
      <spatial sample_rate="{fs}" bit_depth="24" format="{src_format}"/>
      <headlocked sample_rate="{fs}" bit_depth="24"/>
    </encoder_metadata>
  </audio>
</fb360>
"""

CHANNEL_XML = """<fb360>
 <audio>
  <AudioChannelConfiguration schemeIdUri="tag:facebook.com,2016-08-16:fb360:audio:channel_layout" value="{value}" />
 </audio>
</fb360>
"""

SPHERICAL_XML = """<?xml version="1.0"?>
<rdf:SphericalVideo xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:GSpherical="http://ns.google.com/videos/1.0/spherical/">
  <GSpherical:Spherical>true</GSpherical:Spherical>
  <GSpherical:Stitched>true</GSpherical:Stitched>
  <GSpherical:StitchingSoftware>opentbe</GSpherical:StitchingSoftware>
  <GSpherical:ProjectionType>equirectangular</GSpherical:ProjectionType>
  <GSpherical:StereoMode>mono</GSpherical:StereoMode>
</rdf:SphericalVideo>"""


def run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed:\n{p.stderr.strip()[-2000:]}")


def load_spatial(path: Path) -> tuple[np.ndarray, int, str]:
    x, fs = sf.read(path, always_2d=True, dtype="float64")
    if x.shape[1] >= 16:
        m = np.zeros((16, 8))
        for out_ch, (acn, gain) in enumerate(TBE_FROM_ACN):
            m[acn, out_ch] = gain
        return x[:, :16] @ m, fs, "ambix_16"
    if x.shape[1] == 8:
        return x, fs, "tbe_8"
    raise SystemExit(f"{path}: {x.shape[1]} channels; expected 8 (TBE) or >=16 (ambiX)")


def package_mp4(tbe: np.ndarray, hl: np.ndarray, fs: int, src_format: str,
                video: Path, out: Path) -> None:
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        sf.write(tdp / "spatA.wav", tbe[:, :4], fs, subtype="PCM_24")
        sf.write(tdp / "spatB.wav", tbe[:, 4:], fs, subtype="PCM_24")
        sf.write(tdp / "headlocked.wav", hl, fs, subtype="PCM_24")
        run(["ffmpeg", "-v", "error", "-nostats", "-y",
             "-i", str(video),
             "-f", "wav", "-i", str(tdp / "spatA.wav"),
             "-f", "wav", "-i", str(tdp / "spatB.wav"),
             "-f", "wav", "-i", str(tdp / "headlocked.wav"),
             "-map", "0:V:0", "-map", "1", "-map", "2", "-map", "3",
             "-shortest", "-c:v", "copy", "-c:a", "aac", "-q:a", "2",
             "-f", "mp4", str(tdp / "encoded.mp4")])
        for name, value in (("tbe8a", "tbe_8a"), ("tbe8b", "tbe_8b"),
                            ("headlocked", "headlocked")):
            (tdp / f"{name}.xml").write_text(CHANNEL_XML.format(value=value))
        (tdp / "moov.xml").write_text(
            ENCODER_META.format(fs=fs, src_format=src_format))
        # The legacy Encoder ran MP4Box 0.8.1 with "face:tk=0" for the
        # file-level meta; under modern GPAC that syntax lands at movie
        # level instead, and the tk-less form is what reaches file root.
        # This reproduces the legacy command's intent: one file-root meta
        # (encoder metadata) plus one meta per audio track.
        run(["MP4Box", "-noprog",
             "-set-meta", "face:tk=2", "-set-xml", f"{tdp}/tbe8a.xml:tk=2",
             "-set-meta", "face:tk=3", "-set-xml", f"{tdp}/tbe8b.xml:tk=3",
             "-set-meta", "face:tk=4", "-set-xml", f"{tdp}/headlocked.xml:tk=4",
             "-set-meta", "face", "-set-xml", f"{tdp}/moov.xml",
             "-out", str(out), str(tdp / "encoded.mp4")])


def package_mkv(tbe: np.ndarray, hl: np.ndarray, fs: int, src_format: str,
                video: Path, out: Path, opus_bitrate: str = "360k") -> None:
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        ten = np.concatenate([tbe, hl], axis=1)
        sf.write(tdp / "ten.wav", ten, fs, subtype="PCM_24")
        # One 10-channel Opus stream; family 255 carries discrete channels,
        # matching the 10-channel streams the original Encoder produced.
        run(["ffmpeg", "-v", "error", "-nostats", "-y",
             "-i", str(tdp / "ten.wav"),
             "-c:a", "libopus", "-b:a", opus_bitrate,
             "-mapping_family", "255",
             "-f", "ogg", str(tdp / "audio.opus")])
        run(["ffmpeg", "-v", "error", "-nostats", "-y",
             "-i", str(video), "-i", str(tdp / "audio.opus"),
             "-map", "0:V:0", "-map", "1", "-shortest",
             "-c:v", "copy", "-c:a", "copy",
             "-metadata:g",
             "fb360=" + ENCODER_META.format(fs=fs, src_format=src_format),
             "-metadata:s:v:0", "spherical-video=" + SPHERICAL_XML,
             "-metadata:s:a:0",
             "fb360=" + CHANNEL_XML.format(value="tbe_8.2"),
             "-f", "matroska", str(out)])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio", help="8-channel TBE wav, or >=16-channel ambiX wav")
    ap.add_argument("video", help="video file; its first video stream is copied")
    ap.add_argument("output", help=".mp4 or .mkv path")
    ap.add_argument("--headlocked", default=None, help="stereo wav (optional)")
    ap.add_argument("--variant", choices=("mp4", "mkv"), default=None,
                    help="default: from the output extension")
    args = ap.parse_args()

    out = Path(args.output)
    variant = args.variant or ("mkv" if out.suffix.lower() == ".mkv" else "mp4")
    tbe, fs, src_format = load_spatial(Path(args.audio))
    if args.headlocked:
        hl, fs_hl = sf.read(args.headlocked, always_2d=True, dtype="float64")
        if fs_hl != fs:
            raise SystemExit("head-locked sample rate differs from spatial audio")
        n = min(len(hl), len(tbe))
        tbe, hl = tbe[:n], hl[:n, :2]
    else:
        hl = np.zeros((len(tbe), 2))

    if variant == "mp4":
        package_mp4(tbe, hl, fs, src_format, Path(args.video), out)
    else:
        package_mkv(tbe, hl, fs, src_format, Path(args.video), out)
    print(f"-> {out} ({variant} variant, {len(tbe)/fs:.1f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
