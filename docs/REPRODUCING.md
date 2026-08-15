# Reproducing the measurements

openTBE's claim is that its numbers were measured against the original
tools, not inferred. That is only worth anything if someone else can run the
measurements. This is how.

There is a one-command route, then two tiers of detail; the first tier needs
nothing proprietary at all.

---

## The short version

Two scripts, named for what they need:

```bash
python tools/get_mit_filters.py    # always works, needs only this repo
python tools/get_sdk_filters.py    # needs an Audio360 SDK
```

The first gives a working decoder, accurate to about -26 dB against the SDK.
The second takes that to about -131 dB, and walks every prerequisite in
order, stopping at the first thing that is missing and printing the specific
command or link that fixes it. `--check` diagnoses without changing
anything. Everything below is the detail behind those two.

## On not downloading the SDK

`get_sdk_filters.py` will find, build against, and measure an SDK you
already have, but it will not fetch one. Since this repository does print
the archived URLs a few paragraphs down, the line between "documenting where
a copy survives" and "being a distribution channel" deserves stating rather
than implying, because it is softer than it first looks.

Strictly, neither is redistribution: openTBE hosts no Meta code either way,
and the bits would come from a third-party mirror in both cases. So the
honest reasons for the asymmetry are not a bright legal rule:

- **It keeps a decision point where it belongs.** Whether a particular copy
  may be used is a question about your situation, not this project's. An
  explicit step you take yourself preserves that; a silent download removes
  it, and would have openTBE make a call it is not entitled to make.
- **The surviving mirror is a memorial.** Prof. Farina's page has had no
  maintainer since his death. Citing it is normal scholarly practice.
  Pointing automated traffic at it is a different thing.
- **A hardcoded URL rots.** The links below are Wayback captures precisely
  because the live page may not last; a script depending on them would
  break silently and would need updating by people who cannot test it.

None of that makes fetching it *wrong*, and a fork that automated it would
not be doing anything openTBE's documentation does not already enable. It is
a judgement about which side of a genuinely blurry line this project would
rather sit, made explicit so you can disagree with it knowingly.

## Tier 1: no SDK, no Encoder, five minutes

This reproduces everything the project ships, and confirms the internal
consistency of the encode matrix, without any Meta software.

```bash
git clone https://github.com/mormegil6/opentbe.git
cd opentbe
python3 -m venv venv && source venv/bin/activate
pip install numpy scipy soundfile matplotlib

# Build the shipped filter set from Meta's MIT-licensed published
# coefficients, which are snapshotted in docs/upstream/audio360-mit/.
python tools/get_mit_filters.py

# Render a TBE file to binaural, natively.
python tools/render_native.py your_file_tbe8.wav out_binaural.wav

# Encode ambiX to TBE, and confirm the round trip is exact.
python tools/ambix_to_tbe.py master_ambix.wav out_tbe8.wav

# Redraw the publishable figure.
python tools/plot_validation.py
```

`plot_validation.py` will report that it skipped three of four figures,
naming the `data/*.npz` files it wanted. That is correct and expected: those
come from measurements against proprietary software, and the measured data
is deliberately never published (see README.md, "Filter provenance"). Tier 2
is how you generate them yourself.

---

## Tier 2: against the original tools

### What you need

| Component | Used for | Notes |
|---|---|---|
| Audio360 SDK 1.7.12 (`include/`, `libAudio360.dylib`) | every decode measurement (phases 0 to 5) | proprietary, x86_64 macOS |
| FB360 Encoder (Spatial Workstation) | the encode measurement (phase 7) | proprietary, x86_64 macOS, ships a CLI |
| ffmpeg, ffprobe | delivery formats, phase 6 and 7 | `brew install ffmpeg` |
| MP4Box (GPAC) | mp4 delivery, phase 6 | `brew install gpac`, or the copy bundled in the Encoder |

Both Meta components are x86_64 only, so on Apple Silicon they run under
Rosetta. openTBE invokes `arch -x86_64` for you.

### Obtaining them

Meta discontinued the Spatial Workstation and no longer distributes it. The
project is archived at
<https://github.com/facebookarchive/facebook-360-spatial-workstation>, but
that archive carries documentation and helper scripts only: it does **not**
contain the SDK (`include/`, `libAudio360.dylib`) or the Encoder.

openTBE does not redistribute either, and whether a given copy may be used is
a licensing question for whoever obtains it.

The most complete surviving installer mirror was maintained by the late
Prof. Angelo Farina at <https://angelofarina.it/Public/FB360/>. Because that
is a personal academic page with no institutional backing and no maintainer
since his passing, the sibling study archived it in full on the Wayback
Machine, verified byte for byte against the live files:

- <https://web.archive.org/web/20260511153757/https://angelofarina.it/Public/FB360/> (index)
- <https://web.archive.org/web/20260729102037/https://angelofarina.it/Public/FB360/Mac-new-2023/> (Mac, Intel and Apple Silicon, 493 MB)
- <https://web.archive.org/web/20260729103004/https://angelofarina.it/Public/FB360/Mac-old/> (Mac, older Intel build)
- <https://web.archive.org/web/20260729102634/https://angelofarina.it/Public/FB360/Win/> (Windows VST, 129 MB)

**The SDK is a separate download from the installers.** Every archived
installer package was inspected and none contains it: the 493 MB Mac bundle
holds the installer `.pkg`, Bidule examples and the guide; the Windows
package holds the VST plugins plus ffmpeg, GPAC and Python 2.7. Neither has
`TBE_AudioEngine.h` or `libAudio360.dylib`, and neither does the installed
`/Applications/FB360 Spatial Workstation/`.

The SDK itself is hosted separately, in a different directory of the same
site:

- <https://www.angelofarina.it/Public/Facebook-Spatial-Workstation/Download/SDK/>
  holds `Audio360_SDK_1.7.12-cd52f5f44271.zip` (388 MB, the version every
  measurement here used) and `Audio360_SDK_1.3.0.zip`. Unzipping gives
  `Audio360/include/` and `Audio360/macOS/libAudio360.dylib`, which is the
  directory layout the tooling expects.

**That directory is not archived on the Wayback Machine**, unlike the
installer pages, so it currently exists on one personal server with no
maintainer. Anyone who cares about this format being reproducible in ten
years should mirror it.

**You almost certainly do not need it.** Since the switch to Meta's 3OA
coefficients, the filters openTBE ships reproduce the SDK to about -134 dB,
the arithmetic's own floor, at every tested orientation. The SDK is now
useful for independently re-running the measurements, not for getting an
accurate decode.

**Check your version**, because copies in circulation may differ and a
different engine version may not reproduce these numbers exactly:

```bash
grep -h TBE_AUDIOENGINE_VERSION include/TBE_AudioEngine.h   # expect 1.7.12
lipo -archs lib/libAudio360.dylib                            # expect x86_64
"/Applications/FB360 Spatial Workstation/Encoder/FB360 Encoder.app/Contents/MacOS/FB360 Encoder" --version
```

Everything below was measured against Audio360 1.7.12 and FB360 Encoder
v3.3.3, on macOS 15.7.8, Apple Silicon under Rosetta.

### Building the three oracle helpers

Put the SDK's `include/` and `lib/` in one directory and point
`OPENTBE_ORACLE_DIR` at it. `tbe_render` is the sibling study's helper (used
for the fixed-head oracle); the other two are openTBE's, for rotated and
trajectory rendering.

```bash
export OPENTBE_ORACLE_DIR=/path/to/dir/holding/include/and/lib
mkdir -p bin

# fixed-head oracle, from the sibling repo
clang++ -std=c++14 -arch x86_64 -O2 -I "$OPENTBE_ORACLE_DIR/include" \
    /path/to/immersive-formats-evaluation/pipeline/tbe/tbe_render.cpp \
    -L "$OPENTBE_ORACLE_DIR/lib" -lAudio360 \
    -Wl,-rpath,"$OPENTBE_ORACLE_DIR/lib" -o "$OPENTBE_ORACLE_DIR/tbe_render"

# rotated and trajectory oracles, from this repo
clang++ -std=c++14 -arch x86_64 -O2 -I "$OPENTBE_ORACLE_DIR/include" \
    tools/tbe_render_rot.cpp -L "$OPENTBE_ORACLE_DIR/lib" -lAudio360 \
    -Wl,-rpath,"$OPENTBE_ORACLE_DIR/lib" -o bin/tbe_render_rot

clang++ -std=c++14 -arch x86_64 -O2 -I "$OPENTBE_ORACLE_DIR/include" \
    tools/tbe_render_traj.cpp -L "$OPENTBE_ORACLE_DIR/lib" -lAudio360 \
    -Wl,-rpath,"$OPENTBE_ORACLE_DIR/lib" -o bin/tbe_render_traj
```

If a helper builds but renders silence, the usual cause is the engine's
warm-up: the first ~3584 samples are discarded, so a probe placed at the very
start of the file is swallowed. `tools/phase0_lti.py` measures the exact
offset for a given block size.

### Running the measurements

Each script prints its own numbers and exits non-zero on mismatch. Expected
results, and the derivation of each, are in
[PROTOCOL.md](PROTOCOL.md).

```bash
export OPENTBE_ORACLE_DIR=/path/to/dir/holding/include/and/lib

python tools/phase0_lti.py          # is the decode linear and time-invariant?
python tools/phase1_capture.py      # measure the 8x2 impulse-response matrix
python tools/phase2_validate.py     # native renderer vs SDK, several signals
python tools/phase4_headtrack.py    # rotation conventions, orientation grid
python tools/phase5_dynamic.py      # time-varying orientation
python tools/phase7_encode.py       # encode matrix vs the FB360 Encoder
python tools/plot_validation.py --include-measured   # all five figures
```

What each should report:

| Script | Expect |
|---|---|
| `phase0_lti.py` | nine LTI tests pass; warm-up advance 3584 at block 512 |
| `phase1_capture.py` | writes `data/tbe8_ir_48k_block512.npz`, two independent methods agreeing to about -128 dB |
| `phase2_validate.py` | -134, -134 and -143 dB on the three pass/fail signals; the gate-stress probe differs by design |
| `phase4_headtrack.py` | fitted convention `signs (-1,-1,1), order zyx`; worst orientation -132.1 dB across the 11-point grid |
| `phase5_dynamic.py` | five cases, -113.7 to -132.0 dB |
| `phase7_encode.py` | first-order gains 0.4886025; agreement with `sqrt((2l+1)/4pi)`; round trip at 1e-17; whole-file encode indistinguishable from the container's own floor |

Phase 3 is not a script: it was the licensing decision that the shipped
filters are generated from Meta's MIT-licensed published coefficients rather
than from the measurement. Phase 6, the delivery formats, is exercised by
`tools/fb360_package.py` and `tools/fb360_ingest.py` and needs no oracle.

### Seeing what this repo does not publish

Nothing here is hidden from you, only from the repository. Every artefact
below is generated on your own machine by the commands above, from your own
copy of the SDK, and every one of them is gitignored so it stays there.
This table is the complete list, so you can check that claim rather than
take it.

| Not published | What it is | Regenerate with |
|---|---|---|
| `data/tbe8_ir_48k_block512.npz` | the measured 8x2 impulse-response matrix: the SDK's actual decode filters | `phase1_capture.py` |
| `data/acn_r_filter_measured.npz` | the SDK's own ACN 6 filter, recovered by deconvolution | `phase4_headtrack.py` |
| `data/phase2_residuals.npz` | per-sample residuals against the SDK, 6 MB | `phase2_validate.py` |
| `data/phase4_orientation_grid.npz` | the per-orientation dB grid behind the headline figure | `phase4_headtrack.py` |
| `data/phase5_trajectory_residuals.npz` | per-case dB for time-varying rotation | `phase5_dynamic.py` |
| `data/phase7_encode_matrix.npz` | the measured encode gains, all three ways | `phase7_encode.py` |
| `figures/local/filter_comparison_measured.png` | the measured SDK filter response, overlaid on the MIT-derived one | `plot_validation.py --include-measured` |

The last one is the interesting one to look at, and it is the single figure
this project deliberately keeps back. Run:

```bash
python tools/plot_validation.py --include-measured
open figures/local/filter_comparison_measured.png
```

and you get the published curve and the SDK's real measured curve on the
same axes, per channel. Since the 3OA correction they lie on top of each
other, to -136 dB or better on all eight, which is what that correction
means in picture form. `figures/local/` is gitignored, so it cannot be
committed by accident.

Note that none of this changes what the renderers use. Since the 3OA
correction they always load the shipped MIT-derived set, which already
matches the SDK to the float floor, and the measured files serve only as an
independent cross-check.

### The one thing you cannot reproduce

Phase 6 compares against a genuine Encoder-produced mp4 that the sibling
study happened to still have. No original Encoder-produced **mkv** survived,
so the mkv channel order rests on the mp4 track layout plus the Encoder's own
metadata rather than on a direct comparison. If you have an untouched
FB360-Encoder mkv from the era, that is the single most useful thing anyone
could contribute; see README.md, "Future work".

---

## Why the measured data is not simply published

It would be easier to ship `data/tbe8_ir_48k_block512.npz` and let anyone
check the figures against it. openTBE does not, and the reasoning is in
README.md under "Filter provenance": residual dB figures are facts about how
the SDK behaves, which is what the observation right covers and what this
project reports freely. The SDK's actual filter response is its engineered
content, and redistributing that as a file would be handing out the
proprietary part in a different container.

So the measurement stays local, the scripts that produce it are public, and
anyone with their own lawful copy of the SDK can regenerate it byte for byte.
That is the trade: reproducibility through method rather than through
redistribution.
