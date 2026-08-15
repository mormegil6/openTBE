# Measured behaviour of the Audio360 TBE decode

Everything below is measured against the reference decoder (the oracle): the
study's `tbe_render` helper driving Audio360 SDK 1.7.12 (x86_64, under
Rosetta) through `getAudioMix()` with the audio device disabled. Fixed-head
listener, 48 kHz, float32 throughout. Measured 2026-08-14 on macOS 15.7.8,
Apple Silicon. Reproduce with `tools/phase0_lti.py` (set `OPENTBE_ORACLE_DIR`
to a directory containing the built helper and the SDK's `lib/`).

## Warm-up window and time alignment

The engine discards the beginning of the stream while it starts up, so the
output is time-advanced relative to the input, and any input event inside the
warm-up window is lost entirely (an impulse at sample 0 renders to silence).

The advance is an exact whole number of blocks, so it depends on the block
size; it is not a fixed amount of time:

| block | advance, first nonzero | in blocks | advance, response peak |
|---|---|---|---|
| 256 | 3839 samples | 15.00 | 3795 |
| 512 | 3583 samples | 7.00 | 3539 |
| 1024 | 3071 samples | 3.00 | 3027 |

The one-sample difference between the block counts and the first-nonzero
figures (3583 = 7 x 512 - 1, and so on) is not jitter: the decode's first
impulse-response tap sits at lag 1, so the underlying advance is exactly a
whole number of blocks and the working convention is advance = blocks x
block size (3584 at block 512), which is what the capture code and the
stored metadata use.

Separately from the advance, the lost-input window is 4096 samples and does
not depend on the block size: a W impulse anywhere in samples 512 to 4095
renders to silence at block 512 and at block 1024 alike, and the first
surviving position is 4096. At block 1024 this window is larger than the
3071-sample advance, so guard intervals must clear 4096 samples regardless
of block size, not just the advance.

Consequences for capture: pin the block size (512 here), place probes behind
a guard interval of at least 4096 samples plus margin (8000 used), and store
the advance as metadata rather than baking it into captured responses.

The study's earlier cross-correlation against a DAW render reported a
constant 3569-sample offset at block 512, consistent with the 3583-sample
first-nonzero figure here (the difference is where inside the response each
method triggers).

## The W gate

From the second input block onward, the engine outputs exact digital zero
for as long as channel 0 (W) is exactly zero, no matter what the other
channels carry:

- Noise at 0.5 (and, in verification, 0.9) on non-W channels alone, placed
  past the first block: output identically zero, every sample.
- The same holds for the second-order channels.
- A W pilot restores the full contribution of every channel. Verified at
  1e-5, 5e-6 and 2e-6; channel 1 noise at 0.1 then contributes 0.195 rms
  over the active region (0.097 when averaged over the whole output buffer
  including the 1 s tail).
- Extracting a channel's contribution as render(pilot + probe) minus
  render(pilot) is pilot-invariant to -149 to -151 dB across different
  pilot levels and realisations, i.e. exact at float32 precision.

The gate does not cover input block 0. The first block renders into output
block 0 with no time advance and a linear fade-in ramp, gain s/block for an
event at sample s, even when W is exactly zero throughout: full-band noise
at 0.9 on channels 1 to 7 in the first block escapes at up to |y| = 4.08,
and a W impulse at sample s in the first block yields peak 1.0429 x s/block
(verified at both block 512 and 1024). An impulse exactly at sample 0 gets
ramp gain zero, which is why the naive "impulse at 0 is silent" observation
holds despite the leak.

Closing dynamics, measured mid-stream: when W goes digitally silent, the
output cuts to exact zero at a block boundary between 1 and 2 blocks later
(measured 1.000 and 1.875 blocks at two different W-off positions), hard,
with no fade, taking still-active channels and convolution tails with it.

The behaviour looks like a silence-skip optimisation keyed on W energy,
evaluated per block from block 1 onward. Real TBE content always carries W,
which is why none of this surfaced in the study's renders. For system
identification it means every probe must carry a small W pilot, and
per-channel responses are taken by difference.

## Linearity and time invariance (gate open)

With a 1e-5 W pilot in every signal, block 512:

| test | result |
|---|---|
| determinism, identical input twice | bit-identical |
| scaling, -24 dB input vs scaled output | -131.5 dB residual |
| scaling, -48 dB input vs scaled output | -131.5 dB residual |
| superposition, render(a+b) vs render(a)+render(b) | -144.3 dB residual |
| time shift by 512 (on the block grid) | bit-identical |
| time shift by 4096 (on the block grid) | bit-identical |
| time shift by 1000 (off the block grid) | -153.3 dB residual |

Residuals are RMS relative to the reference, and the -130 to -150 dB values
are the float32 arithmetic floor. On-grid shifts being bit-identical shows
the engine's internal state advances strictly per block.

Conclusion: in the open-gate regime the fixed-head decode is linear and
time-invariant at numerical precision. The plan's core assumption holds, and
the decode is fully characterised by an 8x2 impulse-response matrix.

## The impulse-response matrix (phase 1)

Captured with `tools/phase1_capture.py`: a shared 1e-5 W pilot render as
baseline, a unit impulse added per channel, responses taken by difference.
Every channel was cross-checked by rendering 1 s of noise on that channel
and deconvolving the extracted contribution against the probe; the worst
disagreement between the two estimates is -128.1 dB, i.e. the float floor.

| ch | support (to -120 dB re peak) | peak L | peak R | cross-check |
|---|---|---|---|---|
| 0 | 180 samples | 1.0429 | 1.0429 | -132.3 dB |
| 1 | 183 | 1.5143 | 1.5143 | -134.3 dB |
| 2 | 77 | 0.0908 | 0.0908 | -129.6 dB |
| 3 | 182 | 0.0884 | 0.0884 | -136.1 dB |
| 4 | 185 | 0.6417 | 0.6417 | -128.1 dB |
| 5 | 73 | 0.0853 | 0.0853 | -135.1 dB |
| 6 | 179 | 0.0781 | 0.0781 | -131.8 dB |
| 7 | 84 | 0.0930 | 0.0930 | -136.5 dB |

Observations: every response fits in about 4 ms with no reverb tail
(anechoic HRTF-style processing); the L and R peak magnitudes are exactly
equal on every channel, consistent with a left/right symmetric virtual
loudspeaker layout; channel 1 flips the output L/R balance between -8.91 and
+8.91 dB with its sign against W, so it carries the left/right axis.

The matrix is stored in `data/` as `tbe8_ir_48k_block512.npz`, together with
the capture metadata (advance, block, probe position, SDK version). That
path is gitignored and stays so: the licensing question was settled rather
than deferred, and the decision is that the measured matrix is never
published (docs/PLAN.md, and README.md under "Provenance, and what is not published").

A single-sample W impulse still yields its complete 180-sample response
because the response fits inside the one-to-two-block hold before the gate
closes. Reopening was only measured from cold start, where it appears as
the warm-up advance above.

## End of stream

When the input runs out, the helper feeds silence, W goes digitally silent,
and the gate closes as above. Because roughly an advance worth of decoded
audio is still in flight at that moment, content that runs right up to the
end of the file loses its last samples: on a 10 s music slice cut mid-note,
the oracle's output ends 3145 samples before the end of the programme
renders (gate close measured 0.86 blocks after the input ended). Material
that fades out or ends into silence inside the file is unaffected, which is
why the study's renders and the synthetic phase 2 signals never showed
this. For comparisons, appending a short 1e-5 W pilot tail to the input
holds the gate open until the decode has flushed; phase2_real.py does this
by default.

## Native renderer equivalence (phase 2)

`tools/render_native.py` renders TBE by summing per-channel convolution with
the captured matrix and applying the advance; no SDK involved. Against the
oracle (`tools/phase2_validate.py`):

| signal | residual |
|---|---|
| 3 s independent noise on all 8 channels | -133.6 dB |
| 5 s programme-like, 4 correlated sources, level changes, quiet passage | -132.9 dB |
| impulses at off-grid positions 333, 10007, 30001 past the guard | -147.1 dB |
| 30 s real music (3OA recording encoded to TBE via the study's matrix) | -112.1 dB overall, interior seconds at the -130 floor |
| gate-stress signal (energy on ch 1 while W exactly zero) | differs by design; oracle mutes, convolution does not |

The real-material row is a third-order Ambisonic concert recording (Deus Ex
Machina, 2023, one of the study's programme items, from the author's own
session archive) encoded to 8-channel TBE with the study's ambix_to_tbe.py
and validated with tools/phase2_real.py. Scaling linearity was additionally
re-confirmed at music levels (render of 0.5x input is bit-identical to 0.5
times the render). Per-second residuals sit at the -130 dB float floor
everywhere except the documented edge regimes; the overall -112 dB figure
is fully accounted for by the pilot-tail end artifact at an absolute level
of 3.6e-5.

On any signal whose channels are silent whenever W is silent, whose content
starts after the first 4096 input samples and does not run right up to the
end of the file, which covers real programme material with normal lead-in
and fade-out, the native render and the oracle are numerically
interchangeable. What the native renderer does not emulate: the W gate, the
first-block ramp leak, the 4096-sample lost window, and the end-of-stream
gate cut; all divergences are confined to those regimes, and the last one
is arguably a defect of the oracle rather than of the replacement.

## RESOLVED: the SDK uses the 3OA coefficient set

Everything below in this section was written while openTBE compared its
measurements against `AmbiBinauralCoefficients2OA.cpp`. That was the wrong
file, and it is the single cause of every "weak channel", "drift" and
"disclosed gap" this document used to report.

Meta published two coefficient sets. The SDK decodes TBE with the **3OA**
one. The tap counts settle it before any audio is compared:

| TBE | ACN | measured support | 2OA taps | 3OA taps |
|---|---|---|---|---|
| 0 | 0 | 180 | 180 | **180** |
| 1 | 1 | 183 | 184 | **183** |
| 2 | 3 | 77 | 77 | **77** |
| 3 | 2 | 182 | 181 | **182** |
| 4 | 8 | 185 | 185 | **185** |
| 5 | 4 | 73 | 80 | **73** |
| 6 | 5 | 179 | 179 | **179** |
| 7 | 7 | 84 | 84 | **84** |

Eight of eight match the 3OA column exactly, including the V channel whose
7-tap shortfall against 2OA was previously written up as evidence of a
proprietary revision. Per-channel residual of the measured filters against
each published set:

| TBE | vs 2OA | vs 3OA |
|---|---|---|
| 0 | -38.3 dB | **-135.9 dB** |
| 1 | -25.4 | **-148.9** |
| 2 | -16.0 | **-148.1** |
| 3 | -11.8 | **-145.5** |
| 4 | -24.0 | **-143.8** |
| 5 | -8.3 | **-147.3** |
| 6 | -36.2 | **-145.2** |
| 7 | -22.0 | **-147.8** |

The consequences are large and all in the same direction:

- **There is one filter set, not two.** Generated from Meta's MIT-licensed
  3OA coefficients, it reproduces the SDK at the float floor. Nothing is
  traded away by using the published data.
- **The ACN 6 problem is gone.** The 3OA set publishes harmonic 6, so the
  deconvolution recovery in phase 4 stage 4 is no longer needed for
  accuracy. Head-tracked decode from published data alone measures -132 to
  -134 dB at every tested orientation, pitch and roll included, which is
  better than the recovered-filter configuration reached.
- **The measured data is no longer needed for accuracy at all.** It stays
  local, and its role is now purely an independent cross-check of a result
  that no longer depends on it.

Measured after the switch, all with the shipped MIT-derived filters and no
local measurement in the path: fixed head -134.2, -134.0 and -143.4 dB on
the three phase 2 signals; time-varying rotation -113.7 to -132.4 dB across
the five phase 5 cases.

**Everything in the next section, "The MIT-derived filter set", was written
against the 2OA file and is kept as written**, because the reasoning it
records was sound given the wrong input, and because the failure mode is
worth preserving: two independent local measurements agreeing with each
other far better than either agreed with the published filter was correctly
read as "the published filter is not what this engine runs", and incorrectly
read as "the engine's filters were revised later". The third possibility,
that the engine runs a different published set, went unconsidered. Read that
section as history; the numbers above supersede its numbers.

## The MIT-derived filter set (written against 2OA; superseded above)

`tools/get_mit_filters.py` derives a shippable TBE-domain filter set
from Meta's own published, MIT-licensed Ambisonics-to-binaural coefficients
(docs/upstream/audio360-mit/AmbiBinauralCoefficients2OA.cpp), rather than
from the measured npz, so the shipped default has no provenance question
(see docs/PLAN.md). The derivation uses no free parameters fit to data:

- Meta's own MIT decoder (AmbiSphericalConvolution.cpp) convolves each ACN
  harmonic with a single mono IR, then sends harmonics with m >= 0 to L and
  R identically and harmonics with m < 0 to L as +f and R as -f.
- The study's ambix_to_tbe.py encodes each TBE channel as one ACN harmonic
  times a fixed gain, no mixing: TBE(k) = gain(k) x ACN(acn(k)).
- Composing the two gives, with zero fitting: decoding TBE(k) is
  convolution with published_IR(acn(k)) / gain(k), sign-combined by the m
  rule above.

This is a much stronger and more interpretable finding than the earlier
generic least-squares "linear combination of all 9 harmonics" projection
(a prior pass in this project's history, superseded here): here every TBE
channel maps to exactly one specific published harmonic, with a
theoretically-derived scale rather than a value fit to the measurement, and
the result can be checked per-channel against completely independent
evidence (the m-parity sign rule).

Checked against the local measurement (never published; this check is
possible only on a machine with the SDK):

| TBE ch | ACN (l,m) | L/R sign-rule residual | IR residual vs published | correlation |
|---|---|---|---|---|
| 0 (W) | 0 (0,0) | exact | -38.3 dB | 1.000 |
| 1 (Y) | 1 (1,-1) | -253 dB | -25.4 dB | 0.999 |
| 2 (X) | 3 (1,1) | exact | -16.0 dB | 0.988 |
| 3 (Z) | 2 (1,0) | exact | -11.8 dB | 0.972 |
| 4 (U) | 8 (2,2) | exact | -24.0 dB | 0.999 |
| 5 (V) | 4 (2,-2) | -208 dB | -8.3 dB | 0.942 |
| 6 (T) | 5 (2,-1) | -178 dB | -36.2 dB | 1.000 |
| 7 (S) | 7 (2,1) | exact | -22.0 dB | 0.997 |

The L/R sign rule (predicting R exactly from L via the m>=0/m<0 rule, no
free parameters at all) holds on every channel. On the five m>=0 channels
the measured R ear is bit-identical to L, so the residual is exactly zero
and the table says "exact"; an earlier revision printed figures near -570 dB
for these, which were not measurements but the epsilon floor of the dB
formula (20*log10(1e-30 / rms)), varying only because the denominator did.
The three m<0 channels, where the rule predicts a sign flip rather than
equality, land at -178 to -253 dB, the float floor. Either way the
decoder-algorithm half of the derivation is confirmed exactly. The IR
residual is weaker: 6 of 8 channels correlate at 0.988-1.000 against the
zero-fit prediction (residual -16 to -38 dB), but TBE 3 (Z) and TBE 5 (V)
sit at 0.94-0.97 (residual -8 to -12 dB).

Chasing the two weak channels: V's measured local IR literally truncates to
zero past sample 72 (its last nonzero tap is index 72, consistent with the
73-sample support listed in the phase 1 table above), while the published IR
has small real content further out, and the peak position differs. This is not a
measurement artifact of the impulse-probe method: an independent
noise-deconvolution capture of the same channel (tools/phase1_capture.py's
own cross-check) agrees with the impulse capture to -135.1 dB, i.e. the two
independent local measurements agree with each other far better than
either agrees with the published filter.

**That explanation has since been falsified, and the cause is now open.**
Searching libAudio360.dylib for the MIT-published coefficient arrays finds
all nine of them present verbatim, byte for byte, along with the same
int32 kNumTaps table: the SDK does not carry a different or older revision
of these filters, it carries exactly the ones Meta open-sourced. So "the
SDK's filters drifted" cannot be right.

Nor is it a gain error. Fitting the best single scale factor between each
measured TBE-domain filter and its predicted MIT counterpart leaves the
residual almost unchanged: TBE 3 (Z) -12.5 dB after scaling, TBE 5 (V)
-9.5 dB, TBE 2 (X) -16.1 dB, against TBE 0 (W) at -54.0 dB. The shapes
genuinely differ on some channels while the source coefficients are
identical.

The remaining hypothesis, untested here, is that the SDK's ambisonic decode
does not use the AmbiSphericalConvolution path those arrays belong to. The
binary also contains HrtfStandardTable, HrtfHqTable and matching panners, so
a virtual-loudspeaker-plus-HRTF topology would explain a per-channel shape
difference while leaving the published arrays untouched in the binary.
Establishing that would need more measurement than has been done. Recorded
as an open question rather than papered over: the practical consequence is
unchanged and already disclosed, since the MIT-derived path's accuracy is
measured rather than predicted.

Whole-signal effect, same 30 s real-music slice as above: native render
using ONLY the MIT-derived filters (no SDK, no local measurement) against
the oracle: -34.9 dB. Against the -112.1 dB the measured filters reach,
this is a real, honest accuracy gap, not a rounding difference: on TBE
content with substantial energy on the V/Z-mapped source directions, the
MIT-only path is a close but audible approximation, not a numerically
transparent substitute. `render_native.py` defaults to the measured filters
when present (a machine with the SDK) and falls back to the MIT-derived set
otherwise, printing which one loaded so this is never silent.

## Head-tracked decode (phase 4)

The rotated-listener decode needs no virtual-loudspeaker geometry at all,
dissolving the plan's original phase 4 framing. The chain is: reconstruct
the ACN harmonics from TBE (R enters as zero), rotate the field with a
standard order-2 real-spherical-harmonic rotation matrix
(tools/rotation.py; solved numerically from the basis functions with an
asserted residual rather than hand-coded recurrences), then decode with
the same per-harmonic mono IRs and m-parity L/R combination as the fixed
head case. The oracle side is tools/tbe_render_rot.cpp, the study's helper
with the listener rotation exposed on the command line.

The SDK's rotation conventions, fitted empirically (tools/
phase4_headtrack.py, single-axis probes): in ambiX axes (x front, y left,
z up), field rotation = Rz(-yaw) Ry(-pitch) Rx(+roll), composed in that
order, angles in degrees as passed to setListenerRotation. The fit is
unambiguous: a wrong sign or composition order reads between -12 dB and
+2 dB depending on the angle (the error can exceed the signal itself),
against about -132 dB for the correct convention, a separation of 120 dB
or more everywhere tested.

Results against the oracle, same content and skip conventions as phase 2:

- Identity rotation: -133.0 dB (the ACN-domain refactor of the fixed
  decode is exact).
- Yaw only, any angle tested (+-30, 90, 180): -131.3 to -132.7 dB. Yaw is
  a z-rotation, which never mixes m=0 with other m, so the harmonic TBE
  lacks stays empty and yaw tracking is exact with no extra information.
- Pitch and roll with Meta's published R filter: a uniform -50 dB floor.
  Scanning a scale factor on the R filter gives a clean bowl centred at
  1.0 (zeroing R costs 32 dB), so the SDK really decodes rotated-in R
  energy with a filter close to, but not identical to, the published one:
  a third case of proprietary-vs-published filter drift, much smaller
  than V and Z's.
- The SDK's actual R filter is recoverable without ever being able to
  express R in the input: under a known rotation the R-channel signal is
  known exactly, so the residual against the published-R render
  deconvolves to the filter difference (-32.4 dB relative to the
  published R). One pitch-30 render suffices.
- With the recovered R, the full grid, out-of-sample on 10 of its 11
  orientations (negative pitch, both rolls, two combined rotations):
  every orientation lands between -131.3 and -133.0 dB. That band is a
  property of this grid and content, not a hard bound: independent
  verification at orientations outside the grid (including extremes such
  as pitch 85 and a (120, -40, 25) combination) measured -129.9 to
  -132.4 dB, consistent with the -128 to -136 dB float32 floor seen
  across all other measurements in this document.

The recovered R filter derives from the proprietary binary, so it stays in
data/ (untracked) under the same policy as the phase 1 measurement; the
shipped MIT-derived path uses the published R and honestly carries the
-50 dB pitch/roll floor.

### What a clone without the SDK actually gets

The orientation grid above compares two configurations that both use the
measured filters on the 8 carried channels, differing only in ACN 6. Neither
is what a clone runs, because the measured set is not published either. The
third configuration, all 9 harmonics from Meta's MIT-published coefficients,
was measured on the same content and probe grid:

| configuration | identity | yaw 90 | pitch 30 | roll 30 | 35/20/10 |
|---|---|---|---|---|---|
| measured 8 + recovered ACN 6 | -133.0 | -131.3 | -132.8 | -132.6 | -132.7 |
| measured 8 + published ACN 6 | -133.0 | -131.3 | -50.6 | -50.7 | -52.6 |
| MIT-derived, all 9 (a clone) | -25.8 | -25.8 | -25.9 | -25.9 | -25.9 |

The MIT-derived set is flat across orientation at about -26 dB: with those
filters on the carried channels, that error dominates everywhere and the
ACN 6 choice stops being the limiting factor. So the 80 dB split in the
figure is a property of the measured configuration specifically, not
something a clone can observe. tools/render_trajectory.py prints which of
the three it is running and the accuracy to expect from it.

(The -26 dB here and the -35 dB quoted for the fixed-head MIT-only path are
different test signals, phase 4's rotation content versus phase 2's
programme-like material, not a contradiction.)

## Dynamic rotation (phase 5)

How the engine handles setListenerRotation mid-stream, measured with
tools/tbe_render_traj.cpp (the trajectory-driven helper) and
tools/phase5_dynamic.py:

- An update takes effect at the next processing-block boundary after it is
  issued (a step scheduled at output frame 60000 first alters the output
  at 60421: boundary 60416 plus the IR's soft onset). Rotation acts at
  render time, so there is no input transport delay on rotation changes.
- Across the first block after a change, the engine linearly interpolates
  the rotation matrix from the previous block's matrix to the new one,
  sample by sample, weight n/512 for n = 0..511 from the boundary.
  Equivalently, the two rotated signal streams are crossfaded linearly
  over exactly one block. This model matches the oracle at -132.2 dB
  through the transition; interpolating the rotation itself (slerp, angle
  linear in time) is refuted at -18.8 dB, and a crossfade of the two
  static OUTPUT streams is refuted at -30.0 dB (the crossfade happens
  before the convolution, not after).
- After the one-block crossfade, the remaining bit-difference from the
  new-orientation static render decays within one IR length: total span
  one block plus about 180 samples (14.4 ms at 48 kHz).

tools/render_trajectory.py implements exactly this model natively (same
trajectory-file format as the oracle helper, so one file drives both).
Verified against the oracle: single steps -130.9 to -132.0 dB, chained
steps in consecutive blocks -123.5 dB, a mixed-axis three-step sequence
-127.5 dB, and continuous per-block tracking (a 40-block yaw sweep,
updates every block) -113.7 dB. The continuous case sits slightly above
the float floor: chaining ramps every block accumulates a small
model-detail residual (the engine's ramp endpoint versus block-end state),
still far below audibility.

A note on the chained-steps case, because the first version of it was not
testing what it claimed. An update at output frame f first takes effect at
the block boundary b*512 - 3584 >= f, so for f = 60000 that is frame 60416.
Scheduling the second update at exactly 60416 makes both updates land in
the same block: the engine applies them back to back before that block's
single getAudioMix, the intermediate orientation renders for zero blocks,
and the case silently collapses into the single-step case (it was
bit-identical to it, to 14 significant digits, which is how it was caught).
The second update now sits at 60900, inside (60416, 60928], so it lands on
the next boundary and the intermediate orientation really is rendered for
exactly one block. That is a harder case than a single step, and the
residual is correspondingly higher, -123.5 dB rather than the -132.0 dB
this line used to report.

Remaining open for a realtime player rather than this offline chain:
streaming input, live OSC orientation, and audio output; the decode and
transition mathematics above are the whole of what the SDK does.

## Delivery format (phase 6)

The FB360 Encoder's packaging stage, reconstructed from the commands the
app wrote to its own log (recovered before this project began, together
with a complete worked example: the cache intermediates, all XML sidecars,
and a full output file produced by the real pipeline). The app's authors
knew: embedded in its metadata chain sits the line "Dear hackers, although
you may be able to reverse-engineer our file upload format and metadata,
we are not releasing a spec because it is still in flux and will change
rapidly." The flux ended when the product was abandoned; what follows is
the spec that exists in practice.

Two delivery variants, both reproduced by tools/fb360_package.py with
modern ffmpeg and MP4Box, and both read back by tools/fb360_ingest.py:

mp4 variant:

- track 0: video (copied); tracks 1 and 2: 4-channel AAC (spatA = TBE
  channels 1 to 4, spatB = TBE 5 to 8, ffmpeg with -c:a aac -q:a 2);
  track 3: stereo AAC head-locked.
- MP4Box then attaches a "face" metadata item with an XML resource to the
  movie (encoder metadata) and to each audio track (an fb360
  AudioChannelConfiguration with scheme
  tag:facebook.com,2016-08-16:fb360:audio:channel_layout and values
  tbe_8a, tbe_8b, headlocked).
- Ingest verified against the archived example file: the 8 TBE channels
  come back at exact sample alignment (lag 0 on every channel, in every
  content window tested, at both ends of the file) and in the right order
  (the ingested-vs-stem correlation matrix is the identity). The
  per-channel residual against the original PCM stems is -17.7 to
  -35.1 dB energy-weighted over the whole file, which is the AAC
  generation loss present in every FB360 delivery: it grows by 10 to 30 dB
  under a deliberate one-sample misalignment, is spectrally incoherent
  with the stems, and keeps the full 24 kHz bandwidth (noise shaping, not
  band-limiting). The archived AAC tracks are 2064 samples shorter than
  the PCM stems, entirely within the silent tail.
- Metadata placement needs care, because the surviving evidence
  disagrees with itself. The legacy app's logged command (MP4Box
  0.8.1) set one file-level meta (encoder XML, via the old "tk=0"
  syntax) plus one meta per audio track. The archived example file,
  however, turns out not to come from any logged session: its embedded
  XML names a 2024 homebrew GPAC 2.4, i.e. an unlogged modern rerun, and
  whatever produced it collapsed all four operations into a single
  file-root meta holding only the last XML, with no per-track channel
  labels at all. Under modern GPAC the literal legacy syntax lands the
  file-level meta at movie level instead. fb360_package.py therefore
  targets the logged command's intent, which is the only authoritative
  description of the real 2020 output: one file-root meta (tk-less
  syntax under modern GPAC) plus the three track metas, whose embedded
  XMLs are byte-identical to the Encoder's own sidecar files.

mkv variant (the upload format):

- one Opus stream carrying all 10 channels (8 TBE plus head-locked
  stereo), 48 kHz, about 360 kbps total per the log; 10 discrete channels
  require Opus mapping family 255, which is what fb360_package.py uses.
- ffmpeg muxes it with the video plus three metadata tags: a global fb360
  encoder-metadata XML, a spherical-video RDF (Google spatial-video
  schema) on the video stream, and an fb360 AudioChannelConfiguration
  with value tbe_8.2 on the audio stream.
- The produced stream's OpusHead: mapping family 255, 10 uncoupled
  streams, identity channel mapping, pre-skip 312, which ingest and
  ffmpeg's demux handle at exact alignment (lag 0 through the whole
  chain).

A caution about round-trip numbers: any dB figure here is a measurement
of particular content, not a property of the pipeline, and must not be
used as an acceptance threshold for other material. On the archived
programme material the mp4 chain measures roughly -22 to -37 dB and the
mkv chain -12 to -22 dB per channel; but white noise round-trips at
about -8 dB (mp4) and near 0 dB waveform residual (mkv, because Opus at
36 kbps per channel resynthesises noise, preserving energy and spectrum
but not the waveform), while pure tones do 15 to 25 dB better than the
programme figures. What is content-independent, verified with
distinct-level per-channel probes: channel order and sample alignment
survive both chains exactly, and a package without a head-locked input
carries sample-exact silence in a structurally present track.

Honest unknowns: the original app encoded Opus internally, so its exact
encoder settings beyond the stream parameters are unknown; the 10-channel
ordering (TBE 1 to 8 then head-locked left/right) matches the mp4 track
order and the tbe_8.2 naming but could not be checked against an original
mkv, since none survived in the archive; no output of the legacy MP4Box
0.8.1 survives either, so the file-level-plus-track-metas layout is the
logged command's intent rather than a byte-compared artefact; and
player-side compatibility cannot be tested against Facebook's ingestion,
which no longer exists.

## Encode matrix (phase 7)

The decode half has the Audio360 SDK as its oracle. The encode half has one
too, and it went unnoticed for a while: the FB360 Encoder ships a complete
command-line interface beside its GUI, so the ambiX-to-TBE conversion can be
measured rather than taken on trust from the only published table.

    FB360 Encoder --spatial IN.wav --spatial-format FMT \
                  [--headlocked HL.wav] [--video V] \
                  --output OUT --output-format FMT

    input formats   hhoa, ambix-first, fuma-first, ambix-second,
                    fuma-second, ambix-third
    output formats  fb360-hhoa, fb180-hhoa, yt360-ambix-first,
                    rift-oculus-video, fuma-first, ambix-first, fuma-second,
                    mkv-360, mkv-360-ambix-second, mkv-180,
                    mkv-180-ambix-second

Measured on 2026-08-15, Encoder v3.3.3, x86_64 under Rosetta. Two limits
found by trying rather than by reading: the video-bearing outputs refuse to
run without `--video`, and of the audio-only outputs only the first-order
ones actually produce a file (`fuma-second` is accepted as an enum value,
then reports "unrecognized output format"). So the only lossless path out of
the Encoder is first order, which shapes the method below.

**Structure.** A least-squares fit of the Encoder's TBE-to-ambiX conversion
recovers a strictly diagonal matrix: each TBE channel maps to exactly one
ACN harmonic, with off-mapping energy 3.0e-07, i.e. nothing. This confirms
Farina's mapping and signs independently, and confirms that ACN 6 (R) is
simply absent rather than mixed in anywhere.

**First-order gains, exactly.** The lossless TBE-to-ambiX path (24-bit PCM,
no codec) fits with residual 1.7e-06 (-115 dB) and gives all four
first-order gains as the same number:

    0.4886025, to 7 significant figures, on W, Y, X and Z alike

**Second-order gains, tonally.** With no lossless second-order output, the
probe runs ambiX to TBE through the mkv container with one tone per
harmonic, read back by FFT: 0.6300 to 0.6324 across the four channels, i.e.
0.6308 within codec precision.

**Closed form.** Both agree with `sqrt((2l+1)/(4*pi))`, the N3D
normalisation factor: `sqrt(3/4pi) = 0.48860251`, `sqrt(5/4pi) =
0.63078313`. The gains are that expression, not fitted constants. Worth
noting that W (ACN 0, l=0) takes the l=1 factor, not `sqrt(1/4pi) =
0.2820948`; the measurement is unambiguous, 0.4886 rather than 0.2821, on
both the lossless and the tonal path.

**Against the published table.** Everything above rests on Angelo Farina,
"Ambisonics to TBE conversion" (2017),
https://www.angelofarina.it/TBE-conversion-new.htm. It is the only public
documentation this format has, it is where the channel mapping and the signs
used here come from, and its corrected coefficients are what made the
sibling study's encode work at all. Without that page this layout would have
had to be recovered from nothing.

Measured against it, the table holds up: the mapping is exactly right, the
signs are exactly right, and the gains agree to within 5e-07 on Y, X and Z
and on all four second-order channels. That is the whole table confirmed to
the limit of what the measurement can resolve.

The one exception is the W entry, which reads 0.488704 where the Encoder
applies 0.4886025. The difference is 1.0e-04, or 0.0018 dB, and it is
inaudible by any measure. The other three first-order entries agree to the
last digit, and the closed form predicts one shared value across all four,
so this is almost certainly a digit transposed somewhere between measurement
and web page rather than a different intent. openTBE uses the measured value
(tools/tbe_matrix.py), and records the difference here for the same reason
it records everything else: so the number can be checked rather than
inherited.

The correction is invariant for the decoder, which divides by the same gain
it multiplies by, so phase 4 reproduces bit-identically either way (worst
orientation -131.3 dB before and after). It matters only for encoding, and
for anyone else implementing from the published table.

**Round trip.** tools/ambix_to_tbe.py encode followed by decode returns the
8 carried harmonics with maximum error 2.8e-17, the float floor, and ACN 6
exactly zero.

**End to end.** tools/phase7_encode.py stage 4 encodes one master twice,
once with tools/ambix_to_tbe.py and once with the real Encoder, and compares
them. The only route TBE takes out of the Encoder is a lossy delivery
container, so the same stage also measures what that container costs alone,
by pushing an already-encoded TBE file through it and back. On the stage's
own probe material the two figures are -28.47 dB and -28.47 dB, equal to
within 0.001 dB: the encoder-to-encoder difference is entirely Opus, with no
matrix error resolvable above it.

The absolute value is a property of the probe material, not of the matrix,
so it moves with the signal; only the comparison between the two is
meaningful. An earlier revision of this document quoted -37.8 dB and -35.6
dB from an interactive session on different material, which was reproducible
by nobody, since no script computed it. Stage 4 exists so that it is.

Reproduce with `python tools/phase7_encode.py` (needs the Encoder installed
and ffmpeg on PATH).

## The Encoder's quad-binaural is a different renderer (phase 8)

The Spatial Workstation shipped two binaural renderers, and openTBE
reproduces one of them. Worth stating plainly, because "does openTBE match
the FB360 Encoder" has different answers depending on which half is meant.

The Encoder's only binaural output is **quad-binaural**, which is GUI-only:
it is absent from the CLI enum. Feeding it an 8-channel TBE probe produces
four stereo files, and it names them itself: `_0`, `_90`, `_180`, `_270`. So
quad-binaural is four yaw orientations, 24-bit PCM, and the naming maps
directly to openTBE's fitted convention with no sign flip (the diagonal of a
yaw-vs-yaw comparison is the best cell in every row).

Measured on a 6 s independent-noise probe, W held active throughout:

- **It is LTI.** An 8-input 2-output MIMO identification, solving the 8x8
  cross-spectral matrix per frequency bin, reconstructs the Encoder's output
  to **-49.3 dB**. So quad-binaural is a fixed filter system and could be
  implemented by convolution, exactly like the main decode. Two earlier
  attempts said otherwise and were both wrong: a per-channel cross-spectral
  division ignores the cross-terms, and a time-domain fit built with
  `np.roll` wraps circularly and identifies nothing.
- **Its filters are not the SDK's.** Against openTBE's decode at the
  matching orientation, after best gain and lag alignment, the residual is
  **-0.8 dB**: no match.
- **Nor are they either published set.** Per channel, against both Meta's
  2OA and 3OA coefficients, about 0 dB.
- The transport latency is about 30 samples, not the SDK's 3569.

That is consistent with what the binaries carry. Searching for the published
coefficient arrays as raw float32 finds all nine of both sets inside
`libAudio360.dylib`, and **none** in the Encoder or in the
FB360-Spatialiser VST3. What those two do carry is `HrtfStandardTable`,
`HrtfHqTable` and matching panners, which is the object-panner path rather
than the ambisonic decode. Absence from a raw-float32 search is not proof
(the data could be stored at other precision or transformed), but it points
the same way as the measurement.

Consequence for openTBE: it reproduces the **Audio360 SDK**, the engine that
rendered FB360 playback, to the float floor. It does **not** reproduce the
Encoder's quad-binaural deliverable, and implementing that would mean
shipping filters measured out of proprietary software that match nothing
published, which the provenance rule in README.md forbids. The measurement
above is enough to implement it for anyone who does not share that
constraint.

The FB360 Encoder's *encode* path is a separate question and is matched: see
"Encode matrix (phase 7)".

## Independent verification

Every claim above was re-tested by independent probes (different probe
positions, pilot levels and realisations, signal seeds and W-off timings
than the original capture and validation used). Confirmed throughout, with
two refinements found and folded in above: the first-block ramp leak, and
the block-size-independent 4096-sample lost window. Independent re-capture
of channels 2 and 4 at a different probe position under a different pilot
reproduced the stored matrix to -151 and -152 dB; an independently generated
gate-safe signal matched between the native renderer and the oracle at
-133.6 dB.

The phase 4 claims were verified the same way, with fresh angles, seeds
and content, and all confirmed. Highlights beyond what the section above
already states:

- The rotation matrix solve checks out analytically: N3D orthogonality to
  1.4e-14 over 25 random rotations, the y-90 image of R equal to
  -1/2 R + (sqrt(3)/2) U to 6e-16, and no m=0 mixing under 40 random yaw
  rotations. (A +-90-degree y rotation maps R to the same image either
  way, so that particular analytic case cannot test handedness; the
  random-rotation and oracle checks cover it.)
- Under yaw the rotated R-channel signal is numerically zero (about
  6e-16), so the R filter contributes nothing at all: zeroing it entirely
  reproduces the yaw-137 result to 0.1 dB.
- The recovered R filter is invariant to the probe that recovers it far
  beyond what the section above implies: recoveries differing in content
  seed, level, rotation axis, sign and magnitude agree with each other
  and with the stored filter at -123 to -125 dB, likely limited by the
  float32 storage of the npz. The published R spans 183 taps; the
  recovered filter carries essentially all of its energy in the same span:
  past tap 183 the largest value is 1.7e-08 against a peak of 0.221, which is
  the float32 storage floor, so the deconvolution window's tail is empty
  rather than informative. The norms (published 0.3115, recovered 0.3051)
  compare the populated regions.
- The pitch/roll residual against the published R is L/R-symmetric to
  -82 to -84 dB relative to the residual itself, which puts the
  asymmetric component at the absolute float floor (about -132 dB re
  programme), exactly as an m=0 effect must behave.
