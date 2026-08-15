# Plan: characterising and replacing the TBE binaural decode

Status: executed. This is the original spec, written before any of it ran,
kept as the record of the intended approach and its reasoning; the
milestone list below tracks what actually happened against it, including
where reality corrected the plan. Everything through phase 6 is done; see
README.md for the current summary and docs/PROTOCOL.md for the measured
results.

## Goal

A native (ARM64, no Rosetta) TBE-to-binaural decoder whose output matches the
Audio360 SDK's own decode to well below audibility, so that the archived,
x86-only, proprietary `libAudio360.dylib` is no longer needed to render TBE
content.

## What we start from

The sibling repo
[immersive-formats-evaluation](https://github.com/mormegil6/immersive-formats-evaluation)
(`pipeline/tbe/`) already drives the SDK headlessly. That harness was built
2026-07-28 to 2026-07-29 (commits `75730e9` to `e3292b0`), for the study's
own revised manuscript, predating this repo by two and a half weeks; what
follows characterises and reimplements what it already made possible:

- `tbe_render` (C++, ~120 lines) feeds raw float32 through a
  `SpatDecoderQueue` with the audio device `DISABLED` and pulls the binaural
  mix through `getAudioMix()`. The output is bit-reproducible across runs.
- The decode is fixed-head (`setListenerRotation(0,0,0)`), 48 kHz, with a
  constant transport latency of 3569 samples (about 74 ms) and an audible tail
  that the tool captures by rendering 1 s past the end of input.
- The ambiX-to-TBE *encode* is already solved as a fixed linear matrix
  (Farina's corrected coefficients, validated against the FB360 Encoder's own
  output to -57.3 and -61.0 dB on two full-length programme items).

That binary is the **oracle** for everything below. It exists, built, in the
study's working copy along with the SDK (1.7.12, and a 1.3.0 zip for
comparison), and reference TBE renders of two programme items (BigBand,
KWARTET) are available for validation.

## The core idea

For a fixed listener orientation there is no time-varying element left in the
decode: it should be a linear, time-invariant (LTI) system with 8 inputs and 2
outputs. An LTI system is completely described by its impulse responses. If
that holds, we do not need to know the virtual loudspeaker geometry, the HRTF
set, or anything else about the SDK's internals to replace it for fixed-head
use: capture the 8x2 impulse-response matrix once, and the native renderer is
16 convolutions.

Geometry only matters for head-tracked rendering, which is deliberately last.

## Phase 0: verify the LTI assumption

Cheap tests against the oracle, each a few renders:

- **Superposition**: render(a) + render(b) vs render(a + b), sample-exact
  comparison.
- **Scaling**: render(0.5 a) vs 0.5 render(a), at levels safely below any
  possible limiter (-24 dBFS and -48 dBFS inputs).
- **Time invariance**: render(delay(a, N)) vs delay(render(a), N) for a few N.
- **Determinism** is already established (bit-reproducible), but re-confirm on
  this machine as part of the harness.

If any of these fail beyond numerical noise, the plan changes (a limiter or
level-dependent stage would mean capturing at matched levels, or worse); the
failure mode itself would be worth documenting in PROTOCOL.md.

## Phase 1: capture the impulse-response matrix

- Probe each of the 8 TBE channels in turn: a unit impulse on channel k, zeros
  elsewhere, rendered with enough trailing silence to capture the full tail.
- Also capture with exponential sweeps and deconvolve, as a cross-check on the
  impulse capture and to measure the noise floor of the whole path.
- Strip the constant 3569-sample transport delay; record it as metadata rather
  than baking it into the IRs.
- Measure and record tail length (energy decay to below -120 dBFS) per IR.

Deliverable: `h[8][2]` as float32 WAV or npy, plus a capture report
(`docs/PROTOCOL.md` starts here).

## Phase 2: validate against the oracle

- Convolve the study's real TBE programme material with the captured matrix
  and compare against the oracle's own renders of the same files.
- Target: residual below -60 dB relative to signal, in line with the encode
  matrix validation (-57 to -61 dB) already accepted in the study. Ideally the
  residual is at numerical noise; if it is merely "very low", characterise
  what remains (level-dependent? programme-dependent?).
- Verify on both programme items and on synthetic material (noise bursts,
  single-channel content, silence).

## Phase 3: the native renderer

- Offline first: Python, numpy/scipy FFT convolution, WAV in and out, CLI
  compatible with the sibling `render_tbe.py` so the study pipeline could
  swap it in directly.
- The `--verify` idea from the sibling tool carries over: check the output is
  not a trivial passthrough of input channels, and optionally diff against a
  reference render.
- A realtime C implementation (partitioned convolution) is possible later but
  is not needed for the archival/pipeline use case.

## Phase 4 (later, optional): head-tracked decode

Two candidate routes, both open:

- **Structural recovery.** Working hypothesis from the study notes: the decode
  is virtual loudspeakers plus HRTF convolution. Given the measured IR matrix
  and a candidate HRTF database, fit loudspeaker directions and gains. If the
  HRTF set can be identified, rotation becomes an ambisonic-domain rotation
  before a fixed decode. Which HRTF set TBE uses is unknown; nothing in the
  SDK headers or archived docs names it.
- **Grid capture.** Capture IR matrices at a grid of listener orientations
  (the oracle's `setListenerRotation` makes this scriptable) and interpolate.
  Brute-force, storage-heavy, but makes no structural assumptions.

An honest unknown for either route: whether publishing IRs measured from the
proprietary engine is redistributable. The protocol writeup certainly is; the
IR data itself needs a licensing look before it goes in a public repo.

## Correction, 2026-08-15

Everything below was written against Meta's **2OA** coefficient file. The
SDK decodes TBE through the **3OA** path. That single wrong filename is the
origin of every "drift", "weak channel" and "disclosed gap" recorded in the
milestones below, and of the licensing tradeoff they describe. With the
right file the shipped filters reproduce the SDK to about -134 dB, no
measurement is needed for accuracy, and the ACN 6 recovery is a cross-check
rather than a necessity. See docs/PROTOCOL.md, "RESOLVED: the SDK uses the
3OA coefficient set". The milestones are kept as written, as the record of
what was actually believed at each step.

## Milestones

- [x] Capture harness that drives the oracle from this repo (subprocess, like
      `render_tbe.py`): `tools/oracle.py`
- [x] Phase 0 LTI verification, results written to docs/PROTOCOL.md. All nine
      tests pass; two surprises found and documented there (a block-dependent
      warm-up advance, and a mute gate keyed on the W channel that every
      probe must hold open with a small pilot)
- [x] Phase 1 IR matrix captured, tail/latency documented
      (`tools/phase1_capture.py`; every response under 4 ms, noise
      cross-check at the float floor)
- [x] Phase 2 validation, native vs oracle at -133 dB on synthetic
      programme material (`tools/phase2_validate.py`). The study's original
      TBE masters are no longer on disk, so the planned BigBand/KWARTET
      comparison became: a 3OA concert recording from the author's archive
      (Deus Ex Machina 2023, one of the study's items), encoded to TBE with
      the study's matrix and validated at -112 dB overall, interior at the
      -130 dB floor (tools/phase2_real.py)
- [x] Phase 3 native offline renderer (`tools/render_native.py`, WAV CLI);
      a realtime C implementation stays open
- [x] Licensing check on redistributing captured IRs, and the publication
      route decided. Meta published the underlying binaural filter family
      under the MIT license in github.com/facebookarchive/Audio360
      (renderer/src/AmbiBinauralCoefficients2OA.cpp, snapshotted at
      docs/upstream/audio360-mit/); the measured IR matrix projects onto
      those filters with 5.4e-4 relative residual energy at exact
      alignment. But MIT is a license on Meta's files, not a fact about
      independently measured data: the measured npz was captured from the
      proprietary binary, and its numerical agreement with the MIT filters
      is evidence, not a license grant. Decided split, both parts final:
        - the shipped filter set is generated FROM the MIT source files
          directly (not from the measurement), so it is unambiguously MIT
          with no provenance asterisk;
        - the capture and validation scripts are published (original code
          calling the SDK's public API, ordinary permitted use, no MIT
          question at all);
        - the measured npz itself is never published. It stays local,
          described as validation methodology only; anyone with their own
          SDK copy reproduces it with the published scripts.
      This removes the technology-transfer-office question from the
      critical path (nothing derived from the binary is being published).
      The EU observation right (Art. 5(3), Directive 2009/24/EC; SAS
      C-406/10) covers the measurement act itself, independent of this.
      Not legal advice; see docs/upstream/ for the primary sources
- [x] Build the MIT-source filter generator
      (tools/get_mit_filters.py). Correction to the earlier finding: a
      rigorous, zero-fit derivation (Meta's own decoder algorithm composed
      with the study's own encode gains, no parameter fit to data anywhere)
      gives a more precise and more honest picture than the licensing
      investigation's generic least-squares projection. The L/R sign
      structure matches to -178 dB or better on all 8 channels; 6 of 8
      channels' impulse responses match closely (-16 to -38 dB, corr
      0.988-1.000); TBE channels 3 (Z) and 5 (V) only reach -8 to -12 dB
      (corr 0.94-0.97), traced to the proprietary SDK's own filters for
      those two harmonics having drifted from what Meta later
      open-sourced, not a derivation error (two independent local
      measurement methods on the same channel agree with each other to
      -135 dB while disagreeing with the published filter -- see
      docs/PROTOCOL.md). Whole-signal effect on real music: -34.9 dB
      MIT-only vs oracle, against -112.1 dB for the measured filters --
      a real, disclosed accuracy gap, not a rounding difference.
      render_native.py now defaults to the measured filters when present,
      the MIT-derived set otherwise, and prints which one loaded
- [x] Phase 4: head-tracked decode, done and verified at static
      orientations (tools/phase4_headtrack.py, tools/rotation.py,
      tools/tbe_render_rot.cpp). Neither of the two anticipated routes was
      needed: no virtual-speaker geometry exists to recover (the decode is
      per-harmonic IR convolution) and no grid capture was needed (rotation
      is standard order-2 real-SH math, conventions fitted against the
      oracle). The MIT renderer/ lead was inspected and contains no
      rotation code, but its decoder algorithm is what made the per-
      harmonic architecture clear. Yaw is exact by structure; pitch/roll
      needed the SDK's actual R filter, recovered by deconvolution from a
      single rotated oracle render. Whole grid at -131 to -133 dB with the
      recovered R (out-of-sample on 10 of 11 orientations); the MIT-only
      path carries an honest -50 dB pitch/roll floor
- [x] Phase 5: dynamic rotation. The SDK's transition behaviour is fully
      characterised (updates land at the next block boundary; the rotation
      matrix interpolates linearly across exactly one block; slerp and
      output-crossfade models refuted) and tools/render_trajectory.py
      reproduces it: discrete and chained steps at the float floor,
      continuous per-block tracking at -113.7 dB. What remains for true
      realtime head tracking is engineering (streaming, OSC input, audio
      out), not reverse engineering: the decode and transition maths are
      complete
- [x] Phase 6: delivery packaging and ingest. Reverse-engineer the FB360
      Encoder's output format: the 8 TBE channels split into two 4-channel
      stems (spatA/spatB) plus optional head-locked stereo, encoded and
      muxed with video as AAC-mp4 (mp4box face-metadata variant) or
      Opus-mkv (fb360 tag variant), plus the matching demuxer so existing
      FB360 files can be played through this renderer. No proprietary DSP
      is involved, only codecs, splitting and container metadata, all
      reproducible with modern ffmpeg/GPAC. Grounding assets, recovered
      from the Encoder's own logs and cache before this project started:
      a complete worked example (muxed.mp4 produced by the real Encoder,
      both spat stems, the head-locked stem, all five XML sidecars, both
      muxing command lines, the full Encoder log) in the author's archive.
      Done: tools/fb360_package.py produces both variants (mp4 with the
      face/XML metadata via MP4Box, mkv with a 10-channel mapping-family-
      255 Opus stream and the fb360 tags) and tools/fb360_ingest.py reads
      both back. Verified against the real Encoder-produced example:
      ingest at exact sample alignment with only codec generation loss;
      repack structurally faithful to the logged 2020 command's intent
      (the one archived example file's own metadata layout differs, and
      turns out to come from an unlogged modern-GPAC rerun rather than
      the original app; see docs/PROTOCOL.md, metadata placement); both
      variants round-trip, with per-channel order and sample alignment
      exact and codec-loss figures that are properties of the test
      content, not the pipeline. Remaining caveats in docs/PROTOCOL.md
      (original Opus encoder settings, the 10-channel order assumption,
      and untestable Facebook-side ingestion)

## Pointers

- Oracle and SDK live in the study's working copy (not in this repo, and the
  SDK is proprietary so it never will be): `pipeline/tbe/` with `tbe_render`,
  `include/`, `lib/libAudio360.dylib`, plus archived SDK zips (1.7.12, 1.3.0).
- The full history of how the oracle came to be (the DAW failure that
  motivated it, the header spelunking, the abandoned Encoder-CLI route) is in
  `docs/TBE_reverse_engineering_design_notes.md`, which is kept untracked.
