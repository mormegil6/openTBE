"""Re-run openTBE's measurements against your own Audio360 SDK copy.

This is the second of two filter scripts:

    tools/get_tbe_filters.py   what you normally want. Needs only this
                               repository, takes seconds, and produces the
                               filters openTBE ships, which reproduce the SDK
                               to about -134 dB.
    tools/get_sdk_filters.py   this one. Needs an Audio360 SDK, and exists so
                               the claim above can be checked independently
                               rather than believed.

It walks the prerequisites in order, stops at the first one that is not
satisfied, and prints the specific command or link that fixes it. Once
everything is in place it builds the helper binaries and runs the
measurements without further input.

    python tools/get_sdk_filters.py
    python tools/get_sdk_filters.py --sdk /path/to/sdk
    python tools/get_sdk_filters.py --check     # diagnose only, change nothing

Nothing here improves the decode: the renderers ignore the measured filters
(tools/render_native.py defaults to the shipped set unconditionally). What
this produces is an independent cross-check, and the inputs for
tools/plot_validation.py --include-measured.

openTBE does not download the SDK. The reasoning is in docs/REPRODUCING.md,
"On not downloading the SDK". Everything measured lands in data/, which is
gitignored, and is never redistributed.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
BIN = ROOT / "bin"

MEASURED_NPZ = DATA / "tbe8_ir_48k_block512.npz"
R_NPZ = DATA / "acn_r_filter_measured.npz"
HELPERS = ["tbe_render_rot", "tbe_render_traj"]

SEARCH = [
    Path.home() / "Audio360", Path.home() / "audio360",
    Path.home() / "Downloads", Path.home() / "Developer",
    Path("/usr/local/Audio360"), Path("/opt/Audio360"),
]

ARCHIVE_HELP = """\
  Meta discontinued the FB360 Spatial Workstation and no longer distributes
  the SDK. The GitHub archive
    https://github.com/facebookarchive/facebook-360-spatial-workstation
  carries documentation only: it does NOT contain include/ or the dylib.

  The most complete surviving mirror was the late Prof. Angelo Farina's
  page, captured in full on the Wayback Machine:
    https://web.archive.org/web/20260511153757/https://angelofarina.it/Public/FB360/

  Whether a given copy may be used is a licensing question for whoever
  obtains it. openTBE will not fetch it for you; see docs/REPRODUCING.md,
  "On not downloading the SDK"."""


def step(n: int, what: str) -> None:
    print(f"\n[{n}] {what}")


def blocked(problem: str, fix: str) -> int:
    print(f"\n  STOPPED: {problem}")
    print("\n  What to do next:")
    for line in fix.rstrip().splitlines():
        print(f"  {line}" if not line.startswith("  ") else line)
    print("\n  Then run this script again.")
    print("\n  This does not hold openTBE back: tools/get_tbe_filters.py "
          "already gives\n  a decoder accurate to about -134 dB against the "
          "SDK, and needs none\n  of the above. This script only re-measures "
          "that independently.")
    return 1


def looks_like_sdk(p: Path) -> tuple[bool, bool]:
    """(has_headers, has_dylib) for a candidate directory."""
    return ((p / "include" / "TBE_AudioEngine.h").exists(),
            bool(list((p / "lib").glob("libAudio360*"))) if (p / "lib").is_dir()
            else False)


def find_sdk(explicit: str | None) -> tuple[Path | None, list[Path]]:
    """Return (complete SDK, partial candidates worth reporting)."""
    partial: list[Path] = []
    cands: list[Path] = []
    if explicit:
        # only the directory the user named; the search list below is for the
        # no-argument case, and reporting it here would be misleading
        cands = [Path(explicit).expanduser()]
    else:
        env = os.environ.get("OPENTBE_ORACLE_DIR")
        if env:
            cands.append(Path(env).expanduser())
        for base in SEARCH:
            if not base.is_dir():
                continue
            cands.append(base)
            try:
                cands += [c for c in sorted(base.iterdir()) if c.is_dir()]
            except PermissionError:
                pass
    for c in cands:
        h, d = looks_like_sdk(c)
        if h and d:
            return c.resolve(), partial
        if h or d:
            partial.append(c)
    return None, partial


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sdk", default=None,
                    help="directory holding the SDK's include/ and lib/")
    ap.add_argument("--check", action="store_true",
                    help="diagnose prerequisites only, change nothing")
    args = ap.parse_args()

    print("openTBE: measuring the SDK's real filters")
    print("=" * 60)

    # ---- 1. the SDK itself -------------------------------------------------
    step(1, "Looking for an Audio360 SDK")
    sdk, partial = find_sdk(args.sdk)
    if sdk is None:
        if partial:
            print("  Found directories that look close but are incomplete:")
            for p in partial[:4]:
                h, d = looks_like_sdk(p)
                print(f"    {p}")
                print(f"      include/TBE_AudioEngine.h : "
                      f"{'yes' if h else 'MISSING'}")
                print(f"      lib/libAudio360.dylib     : "
                      f"{'yes' if d else 'MISSING'}")
        else:
            print("  None found. Looked at --sdk, OPENTBE_ORACLE_DIR, and:")
            for p in SEARCH:
                print(f"    {p}")
        return blocked(
            "no complete Audio360 SDK on this machine",
            "  You need one directory containing BOTH of:\n"
            "    include/TBE_AudioEngine.h\n"
            "    lib/libAudio360.dylib\n"
            "  then point this script at it:\n"
            "    python tools/get_sdk_filters.py --sdk /path/to/that/dir\n\n"
            + ARCHIVE_HELP)
    print(f"  Found: {sdk}")
    print("    include/TBE_AudioEngine.h : yes")
    print("    lib/libAudio360.dylib     : yes")

    # ---- 2. architecture ---------------------------------------------------
    step(2, "Checking the library architecture")
    dylib = next(iter((sdk / "lib").glob("libAudio360*")))
    archs = ""
    if shutil.which("lipo"):
        archs = subprocess.run(["lipo", "-archs", str(dylib)],
                               capture_output=True, text=True).stdout.strip()
        print(f"  {dylib.name}: {archs or 'unknown'}")
    if archs and "x86_64" not in archs:
        return blocked(
            f"{dylib.name} does not contain x86_64 ({archs})",
            "  openTBE builds the helpers for x86_64 because every known\n"
            "  build of this library is Intel-only. A different build would\n"
            "  need the -arch flag in this script adjusted.")
    if platform.machine() == "arm64":
        print("  Apple Silicon: helpers will be built x86_64 and run under "
              "Rosetta.")

    # ---- 3. a compiler -----------------------------------------------------
    step(3, "Checking for a C++ compiler")
    cxx = shutil.which("clang++") or shutil.which("g++")
    if not cxx:
        return blocked(
            "no clang++ or g++ on PATH",
            "  On macOS, install the Command Line Tools:\n"
            "    xcode-select --install")
    print(f"  {cxx}")

    # ---- 4. the helper binaries -------------------------------------------
    step(4, "Building the oracle helpers")
    if args.check:
        for name in HELPERS:
            print(f"  {name}: "
                  f"{'built' if (BIN / name).exists() else 'would build'}")
    else:
        BIN.mkdir(exist_ok=True)
        for name in HELPERS:
            out = BIN / name
            if out.exists():
                print(f"  {name}: already built")
                continue
            cmd = [cxx, "-std=c++14", "-O2"]
            if platform.system() == "Darwin":
                cmd += ["-arch", "x86_64"]
            cmd += ["-I", str(sdk / "include"), str(HERE / f"{name}.cpp"),
                    "-L", str(sdk / "lib"), "-lAudio360",
                    "-Wl,-rpath," + str(sdk / "lib"), "-o", str(out)]
            p = subprocess.run(cmd, capture_output=True, text=True)
            if p.returncode != 0 or not out.exists():
                detail = "\n".join(
                    "    " + ln for ln in (p.stderr or "").strip()
                    .splitlines()[:6])
                return blocked(
                    f"could not build {name}",
                    "  The compiler said:\n" + detail +
                    "\n\n  The usual cause is an SDK whose headers and "
                    "library do not match,\n  or a library built for a "
                    "different architecture.")
            print(f"  {name}: built")

    if args.check:
        print("\n" + "=" * 60)
        print("All prerequisites satisfied. Run without --check to measure.")
        return 0

    os.environ["OPENTBE_ORACLE_DIR"] = str(sdk)

    # ---- 5 and 6. the measurements ----------------------------------------
    step(5, "Measuring the impulse-response matrix (a minute or two)")
    if subprocess.run([sys.executable, str(HERE / "phase1_capture.py")]
                      ).returncode != 0:
        return blocked(
            "the capture run failed",
            "  The output above is from tools/phase1_capture.py. If it "
            "rendered\n  silence, the usual cause is the engine's warm-up "
            "window; see\n  docs/PROTOCOL.md, 'Warm-up window and time "
            "alignment'.")

    step(6, "Recovering the filter for the channel TBE cannot carry "
            "(several minutes)")
    if subprocess.run([sys.executable, str(HERE / "phase4_headtrack.py")]
                      ).returncode != 0:
        print("\n  That did not finish. The step 5 measurement is still "
              "good; the\n  ACN 6 cross-check simply did not run.")

    print("\n" + "=" * 60)
    have_m, have_r = MEASURED_NPZ.exists(), R_NPZ.exists()
    print(f"  [{'x' if have_m else ' '}] measured filters      "
          f"{MEASURED_NPZ.relative_to(ROOT)}")
    print(f"  [{'x' if have_r else ' '}] recovered ACN 6       "
          f"{R_NPZ.relative_to(ROOT)}")
    if have_m and have_r:
        print("\n  Done. Both cross-checks are on disk. Compare them against")
        print("  the shipped filters with:")
        print("    python tools/get_tbe_filters.py --verify")
        print("    python tools/plot_validation.py --include-measured")
    elif have_m:
        print("\n  Partly done: the impulse-response matrix is measured, "
              "the ACN 6\n  cross-check is not.")
    print("\n  The renderers are unaffected either way: they use the shipped "
          "filters.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
