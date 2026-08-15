"""Drive the FB360 Encoder headlessly, as the oracle for the encode half.

The decode half of this project has the Audio360 SDK as its oracle
(tools/oracle.py). The encode half has one too: the FB360 Encoder ships a
full command-line interface next to its GUI, so it can be measured the same
way rather than guessed at.

    FB360 Encoder --spatial IN.wav --spatial-format FMT \\
                  [--headlocked HL.wav] [--video VIDEO] \\
                  --output OUT --output-format FMT

The binary is x86_64, so on Apple Silicon it runs under Rosetta via
`arch -x86_64`, exactly like the SDK helper. It shells out to ffmpeg, and for
mp4 delivery also to MP4Box and a Python 2.7 copy of Google's spatial-media
injector that it bundles; those paths are overridable, which is what makes it
scriptable on a machine where python2 is not on PATH.

Formats it accepts, from its own --help:

    input   hhoa, ambix-first, fuma-first, ambix-second, fuma-second,
            ambix-third
    output  fb360-hhoa, fb180-hhoa, yt360-ambix-first, rift-oculus-video,
            fuma-first, ambix-first, fuma-second, mkv-360,
            mkv-360-ambix-second, mkv-180, mkv-180-ambix-second

Two practical limits, both measured rather than assumed:

  - The video-bearing output formats refuse to run without --video ("Video
    input required for this output format"), so getting TBE *out* of the
    Encoder means muxing a throwaway video and reading the audio back.
  - Of the audio-only outputs, only the first-order ones actually produce a
    file here; `fuma-second` is accepted as an enum value but then reports
    "unrecognized output format". So the only lossless path out of the
    Encoder is first order, and second-order gains have to be measured
    through a lossy container with a tonal probe (tools/phase7_encode.py).

The Encoder is proprietary. Like the SDK oracle, it is used here only to
observe behaviour, and nothing derived from it is published; see
docs/PLAN.md on the licensing position and README.md, "Filter provenance".

The install path is resolved from the OPENTBE_ENCODER environment variable,
falling back to the default macOS install location.
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

DEFAULT_APP = Path(
    "/Applications/FB360 Spatial Workstation/Encoder/FB360 Encoder.app")

INPUT_FORMATS = ("hhoa", "ambix-first", "fuma-first", "ambix-second",
                 "fuma-second", "ambix-third")
OUTPUT_FORMATS = ("fb360-hhoa", "fb180-hhoa", "yt360-ambix-first",
                  "rift-oculus-video", "fuma-first", "ambix-first",
                  "fuma-second", "mkv-360", "mkv-360-ambix-second",
                  "mkv-180", "mkv-180-ambix-second")


def encoder_app(app: Path | str | None = None) -> Path:
    p = Path(app) if app else Path(os.environ.get("OPENTBE_ENCODER",
                                                  DEFAULT_APP))
    if not p.exists():
        raise FileNotFoundError(
            f"FB360 Encoder not found at {p}. Set OPENTBE_ENCODER to the "
            "FB360 Encoder.app bundle (it ships with the FB360 Spatial "
            "Workstation installer).")
    return p


def _binary(app: Path) -> Path:
    exe = app / "Contents" / "MacOS" / "FB360 Encoder"
    if not exe.exists():
        raise FileNotFoundError(f"no Encoder binary inside {app}")
    return exe


def version(app: Path | str | None = None) -> str:
    a = encoder_app(app)
    out = subprocess.run(_wrap([str(_binary(a)), "--version"]),
                         capture_output=True, text=True)
    return (out.stdout + out.stderr).strip()


def _wrap(cmd: list[str]) -> list[str]:
    """Run x86_64 under Rosetta when we are on Apple Silicon."""
    if platform.machine() == "arm64":
        return ["arch", "-x86_64"] + cmd
    return cmd


def encode(
    spatial: Path | str,
    spatial_format: str,
    output: Path | str,
    output_format: str,
    headlocked: Path | str | None = None,
    video: Path | str | None = None,
    app: Path | str | None = None,
    ffmpeg: str = "ffmpeg",
    timeout: int = 600,
) -> Path:
    """Run one conversion and return the output path.

    Raises RuntimeError with the Encoder's own message if it declines: it
    exits 0 even on refusal, printing the reason, so the output file's
    existence is the real success signal.
    """
    if spatial_format not in INPUT_FORMATS:
        raise ValueError(f"unknown input format {spatial_format!r}; "
                         f"expected one of {INPUT_FORMATS}")
    if output_format not in OUTPUT_FORMATS:
        raise ValueError(f"unknown output format {output_format!r}; "
                         f"expected one of {OUTPUT_FORMATS}")
    a = encoder_app(app)
    out = Path(output)
    if out.exists():
        out.unlink()

    cmd = [str(_binary(a)),
           "--spatial", str(Path(spatial).resolve()),
           "--spatial-format", spatial_format,
           "--output", str(out.resolve()),
           "--output-format", output_format,
           "--ffmpeg-path", ffmpeg]
    if headlocked is not None:
        cmd += ["--headlocked", str(Path(headlocked).resolve())]
    if video is not None:
        cmd += ["--video", str(Path(video).resolve())]
        # mp4 delivery also needs MP4Box and the bundled python2 injector.
        mp4box = a / "Contents" / "Data" / "64" / "MP4Box"
        if mp4box.exists():
            cmd += ["--mp4box-path", str(mp4box)]

    proc = subprocess.run(_wrap(cmd), capture_output=True, text=True,
                          timeout=timeout)
    msg = (proc.stdout + proc.stderr).strip()
    if not out.exists():
        raise RuntimeError(
            f"FB360 Encoder produced no output for {spatial_format} -> "
            f"{output_format}: {msg or 'no message'}")
    return out
