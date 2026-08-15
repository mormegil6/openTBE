"""Validation figures: plot the accuracy claims in docs/PROTOCOL.md / README.md.

Reads whatever data/*.npz files are present and writes PNGs to figures/:

  filter_comparison.png       magnitude + phase of the shipped filters, all
                               8 TBE channels. Needs
                               data/tbe8_filters_mit.npz, which ships by
                               default (tools/get_mit_filters.py); the
                               measured overlay additionally needs
                               data/tbe8_ir_48k_block512.npz, which only
                               exists on a machine with the SDK
                               (tools/phase1_capture.py). Without it, this
                               figure still renders, MIT-derived curves only.
  orientation_grid.png        yaw/pitch/roll accuracy grid from phase 4
                               (docs/PROTOCOL.md, "Head-tracked decode").
                               Needs data/phase4_orientation_grid.npz,
                               written by tools/phase4_headtrack.py, which
                               needs the SDK oracle.
  phase2_residuals.png        per-test-signal dB bar chart, plus a
                               spectrogram of the native-minus-oracle
                               residual on the programme-like test signal if
                               that array was saved. Needs
                               data/phase2_residuals.npz, written by
                               tools/phase2_validate.py, which needs the SDK
                               oracle.
  phase5_trajectory_residuals.png   per-case dB bar chart for the
                               time-varying rotation tests (docs/PROTOCOL.md,
                               "Dynamic rotation"). Needs
                               data/phase5_trajectory_residuals.npz, written
                               by tools/phase5_dynamic.py, which needs the
                               SDK oracle.

None of this touches the SDK, the oracle, or any *.cpp file: it only reads
already-saved .npz data. On a fresh clone with no SDK access, only
filter_comparison.png (MIT-derived only) can be produced; the other three
need data files that only exist on a machine that has run the SDK-dependent
phase 2 / phase 4 / phase 5 scripts (see docs/PLAN.md, "Licensing check on
redistributing captured IRs" -- the measured data is deliberately never
published, so this is expected on any clone other than the original
author's).

Usage: python tools/plot_validation.py
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.fft import rfft, rfftfreq

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
FIG_DIR = ROOT / "figures"
# Figures that render proprietary-derived data go here, and this path is
# gitignored. See plot_filter_comparison() for why the split exists.
LOCAL_FIG_DIR = FIG_DIR / "local"

MEASURED_IR = DATA_DIR / "tbe8_ir_48k_block512.npz"
MIT_IR = DATA_DIR / "tbe8_filters_mit.npz"
PHASE2_NPZ = DATA_DIR / "phase2_residuals.npz"
PHASE4_NPZ = DATA_DIR / "phase4_orientation_grid.npz"
PHASE5_NPZ = DATA_DIR / "phase5_trajectory_residuals.npz"

# TBE channel index -> letter and the ACN harmonic it carries, from
# docs/PROTOCOL.md ("The MIT-derived filter set").
TBE_LABELS = ["W", "Y", "X", "Z", "U", "V", "T", "S"]

# Okabe-Ito colorblind-safe categorical pair: measured (blue) vs MIT-derived
# (orange), used consistently across figures. Vermillion flags any test
# signal that misses its pass threshold.
COLOR_MEASURED = "#0072B2"
COLOR_MIT = "#E69F00"
COLOR_FLAG = "#D55E00"
CMAP_SEQUENTIAL = "cividis"  # perceptually uniform, colorblind-safe

skipped: list[tuple[str, str]] = []


def note_skip(fig_name: str, reason: str) -> None:
    skipped.append((fig_name, reason))
    print(f"  SKIPPED {fig_name}: {reason}")


def plot_filter_comparison(include_measured: bool = False) -> None:
    # Two different figures, deliberately kept apart:
    #
    #   figures/filter_comparison.png              MIT-derived curves only.
    #       Everything in it descends from Meta's MIT-licensed published
    #       coefficients, so it is publishable and is what a fresh clone
    #       reproduces.
    #   figures/local/filter_comparison_measured.png   adds the measured SDK
    #       curves. Those are a rendering of proprietary-derived filter data,
    #       which this project never publishes (README.md, "Provenance,
    #       and what is not published"), so it is written under
    #       figures/local/, which is
    #       gitignored. Summary statistics ABOUT the measurement (the residual
    #       dB figures in the other three figures) are published; the filter
    #       response itself is not.
    if include_measured:
        fig_path = LOCAL_FIG_DIR / "filter_comparison_measured.png"
        fig_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        fig_path = FIG_DIR / "filter_comparison.png"
    print(f"[{fig_path.name}]")
    if not MIT_IR.exists():
        note_skip(fig_path.name,
                   f"{MIT_IR} not found; run "
                   "'python tools/get_mit_filters.py' first (no SDK "
                   "needed, this ships by default)")
        return

    mit = np.load(MIT_IR)
    h_mit = mit["h"].astype(np.float64)  # (8, len, 2)
    fs = int(mit["fs"])

    have_measured = include_measured and MEASURED_IR.exists()
    if include_measured and not MEASURED_IR.exists():
        note_skip(fig_path.name,
                  f"--include-measured was given but {MEASURED_IR.name} is "
                  "not on this machine (expected on any clone without the "
                  "SDK); nothing to overlay")
        return
    if have_measured:
        h_meas = np.load(MEASURED_IR)["h"].astype(np.float64)
        title_note = "measured (SDK) vs MIT-derived"
    else:
        h_meas = None
        title_note = "MIT-derived filters (what openTBE ships)"
        print("  MIT-derived only: this is the publishable figure, and it is "
              "what any clone reproduces without the SDK. The measured "
              "overlay is a local-only view (--include-measured), since the "
              "measured filters are proprietary-derived and never published; "
              "see README.md, 'Provenance, and what is not published'.")

    n_fft = 4096
    freqs = rfftfreq(n_fft, d=1.0 / fs)
    x_lo = max(freqs[1], 20.0)

    fig, axes = plt.subplots(8, 2, figsize=(11, 20), sharex=True)
    fig.suptitle(f"TBE filter frequency response: {title_note}", fontsize=13)

    for ch in range(8):
        ax_mag, ax_phase = axes[ch]

        mit_ir = h_mit[ch, :, 0]
        H_mit = rfft(mit_ir, n_fft)
        ax_mag.plot(freqs, 20 * np.log10(np.maximum(np.abs(H_mit), 1e-9)),
                    color=COLOR_MIT, lw=1.3, label="MIT-derived")
        ax_phase.plot(freqs, np.unwrap(np.angle(H_mit)),
                      color=COLOR_MIT, lw=1.3)

        if have_measured:
            meas_ir = h_meas[ch, :, 0]
            H_meas = rfft(meas_ir, n_fft)
            ax_mag.plot(freqs,
                       20 * np.log10(np.maximum(np.abs(H_meas), 1e-9)),
                       color=COLOR_MEASURED, lw=1.3, label="measured (SDK)")
            ax_phase.plot(freqs, np.unwrap(np.angle(H_meas)),
                         color=COLOR_MEASURED, lw=1.3)

        ax_mag.set_ylabel(f"ch {ch} ({TBE_LABELS[ch]})", fontsize=9)
        ax_mag.grid(True, alpha=0.25, linewidth=0.6)
        ax_phase.grid(True, alpha=0.25, linewidth=0.6)
        ax_mag.set_xscale("log")
        ax_phase.set_xscale("log")
        ax_mag.set_xlim(x_lo, fs / 2)
        ax_phase.set_xlim(x_lo, fs / 2)

    axes[0, 0].set_title("magnitude (dB)", fontsize=10)
    axes[0, 1].set_title("phase (rad, unwrapped)", fontsize=10)
    axes[0, 0].legend(loc="lower left", fontsize=8)
    axes[-1, 0].set_xlabel("Hz")
    axes[-1, 1].set_xlabel("Hz")
    foot = ("L channel shown; R differs only by the m-parity sign rule "
            "(docs/PROTOCOL.md).")
    if have_measured:
        foot += (" The two curves overlap: the shipped filters reproduce the "
                 "SDK's to -136 dB or better on every channel.")
    fig.text(0.01, 0.005, foot, fontsize=7, color="dimgray")
    fig.tight_layout(rect=(0, 0.015, 1, 0.97))
    fig.savefig(fig_path, dpi=150)
    plt.close(fig)
    print(f"  wrote {fig_path}")


def plot_orientation_heatmap() -> None:
    fig_path = FIG_DIR / "orientation_grid.png"
    print(f"[{fig_path.name}]")
    if not PHASE4_NPZ.exists():
        note_skip(fig_path.name,
                   f"{PHASE4_NPZ} not found; run "
                   "'python tools/phase4_headtrack.py' with an SDK oracle "
                   "available (docs/REPRODUCING.md)")
        return

    d = np.load(PHASE4_NPZ)
    yaw, pitch, roll = d["yaw"], d["pitch"], d["roll"]
    # One curve: the shipped configuration, all nine harmonics from Meta's
    # published 3OA set. Deliberately NOT stage5, which substitutes an ACN 6
    # filter deconvolved from an SDK render and is therefore
    # proprietary-derived; plotting that under a caption crediting published
    # filters would misstate the provenance (the two agree to -133 dB anyway).
    # phase4_headtrack.py always writes this key but leaves it empty when it
    # had no local measurement to contrast against, so test size, not presence.
    # stage3_db_published_r is bit-identical to it (both are the shipped
    # MIT-derived filters), so it is a provenance-correct fallback.
    shipped = d["shipped_db_mit_only"] if "shipped_db_mit_only" in d.files \
        else np.array([])
    vals = shipped if shipped.size else d["stage3_db_published_r"]

    order_i = np.argsort(vals)[::-1]          # worst at top
    labels = [f"yaw {yaw[i]:g}, pitch {pitch[i]:g}, roll {roll[i]:g}"
              for i in order_i]
    v = vals[order_i]
    y_pos = np.arange(len(order_i))

    fig, ax = plt.subplots(figsize=(10, 5.2))
    ax.barh(y_pos, v, height=0.62, color=COLOR_MEASURED, zorder=3)
    for yv, val in zip(y_pos, v):
        ax.annotate(f"{val:.1f} dB", (val, yv), xytext=(7, 0),
                    textcoords="offset points", va="center", ha="left",
                    fontsize=8.5, fontweight="bold", color="white", zorder=4)
    ax.axvline(-100, color="0.35", ls="--", lw=1.2, zorder=5)
    ax.annotate("-100 dB: below hearing", (-100, len(order_i) - 0.4),
                xytext=(-6, 0), textcoords="offset points", ha="right",
                va="center", fontsize=8, color="0.35")
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=8.5)
    ax.invert_yaxis()
    ax.set_xlim(min(-145, float(v.min()) * 1.06), 0)
    ax.set_xlabel("How far openTBE's output is from the Audio360 SDK's own, "
                  "in dB. Further left is better.")
    ax.grid(True, axis="x", alpha=0.25, zorder=0)
    ax.set_title("Head-tracked decode: openTBE against the Audio360 SDK,\n"
                 "at every tested listener orientation", fontsize=12)
    fig.text(0.01, 0.005,
             "Each row is one orientation actually measured. openTBE uses "
             "Meta's own MIT-licensed published filters here; nothing "
             "proprietary is needed to reach these figures. -130 dB is the "
             "float arithmetic's own noise. (docs/PROTOCOL.md, phase 4)",
             fontsize=7, color="dimgray", wrap=True)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(fig_path, dpi=150)
    plt.close(fig)
    print(f"  wrote {fig_path}")


def plot_phase2_residuals() -> None:
    fig_path = FIG_DIR / "phase2_residuals.png"
    print(f"[{fig_path.name}]")
    if not PHASE2_NPZ.exists():
        note_skip(fig_path.name,
                   f"{PHASE2_NPZ} not found; run "
                   "'python tools/phase2_validate.py' with "
                   "OPENTBE_ORACLE_DIR set to a machine with the SDK oracle")
        return

    d = np.load(PHASE2_NPZ)
    names = [str(n) for n in d["test_names"]]
    dbs = d["test_residual_db"]
    fs = int(d["fs"])
    # Threshold per entry, NaN where the entry is characterisation-only or is
    # not a dB residual at all. Older npz files predate this key; fall back to
    # the name-prefix rule they implied.
    if "test_threshold_db" in d.files:
        thresholds = d["test_threshold_db"]
    else:
        thresholds = np.array(
            [np.nan if n.startswith(("gatestress",)) else -100.0
             for n in names])

    # Split into the pass/fail tests (finite threshold) and the
    # characterisation-only ones. The gate-stress test deliberately differs
    # from the oracle (the W gate is not emulated, see phase2_validate.py), so
    # plotting it against the pass line would show a ~99 dB "failure" the
    # script never reports. The bookkeeping scalars (sample indices, region
    # RMS) are not dB at all and are dropped entirely.
    bar_names, bar_vals, bar_thresh = [], [], []
    char_names, char_vals = [], []
    for n, v, t in zip(names, dbs, thresholds):
        if n.startswith(("gatestress_divergence", "gatestress_oracle_rms",
                         "gatestress_native_rms")):
            continue
        if np.isfinite(t):
            bar_names.append(n)
            bar_vals.append(float(v))
            bar_thresh.append(float(t))
        else:
            char_names.append(n)
            char_vals.append(float(v))

    has_spectrogram = ("residual_programme" in d.files
                       and d["residual_programme"].size > 0)
    n_panels = 2 if has_spectrogram else 1
    fig, axes = plt.subplots(n_panels, 1, figsize=(9, 4.4 * n_panels))
    axes = np.atleast_1d(axes)

    ax = axes[0]
    colors = [COLOR_FLAG if v >= t else COLOR_MEASURED
              for v, t in zip(bar_vals, bar_thresh)]
    bars = ax.bar(bar_names, bar_vals, color=colors)
    # A bar chart is zero-baselined, so residuals around -130 dB all render as
    # near-identical full-height bars. Direct-label each one and lift the axis
    # floor just below the deepest bar so the differences are readable.
    for b, v in zip(bars, bar_vals):
        ax.annotate(f"{v:.1f}", (b.get_x() + b.get_width() / 2, v),
                    xytext=(0, -12), textcoords="offset points",
                    ha="center", fontsize=8.5, fontweight="bold")
    thr = bar_thresh[0] if bar_thresh else -100.0
    if len(set(bar_thresh)) == 1:
        ax.axhline(thr, color="gray", ls="--", lw=1)
        # Label the line in place rather than in a legend box, which would sit
        # on top of the bars (they span the full plot height from 0 downwards).
        ax.annotate(f"inaudible past here ({thr:.0f} dB)", (1.0, thr),
                    xycoords=("axes fraction", "data"), xytext=(-4, 4),
                    textcoords="offset points", ha="right", fontsize=7.5,
                    color="0.25",
                    bbox=dict(facecolor="white", edgecolor="none",
                              alpha=0.85, pad=1.5))
    lo = min(bar_vals) if bar_vals else -140.0
    ax.set_ylim(lo * 1.12, 0)
    ax.set_ylabel("difference from the SDK (dB)\nlower is better")
    ax.set_title("Fixed-head decode: how closely openTBE matches the "
                 "Audio360 SDK, by test signal", fontsize=12)
    ax.grid(True, axis="y", alpha=0.25)
    if char_names:
        shown = ", ".join(f"{n} {v:.1f} dB"
                          for n, v in zip(char_names, char_vals))
        ax.set_xlabel(
            f"characterisation-only, not a pass/fail case: {shown} "
            "(the W gate is deliberately not emulated)",
            fontsize=7, color="dimgray")

    if has_spectrogram:
        resid = d["residual_programme"]  # (n, 2), native minus oracle
        mono = resid.mean(axis=1)
        # The stored residual starts GUARD samples in, and its tail is exactly
        # zero out to the end of the padded render. Left as-is, specgram maps
        # those zeros to -inf, matplotlib masks them, and a bit-exact region
        # paints as blank "no data". Trim the exact-zero tail and offset the
        # time axis so it reads in real input time.
        start = 0
        if "residual_start_sample" in d.files:
            rs_names = [str(n) for n in d["residual_start_names"]]
            if "programme" in rs_names:
                start = int(d["residual_start_sample"][
                    rs_names.index("programme")])
        # The residual is stored on the oracle's OUTPUT timeline, which runs
        # ahead of the input by the engine's warm-up advance (3584 samples at
        # block 512, docs/PROTOCOL.md "Warm-up window"). Add it so the axis
        # really is the input timeline it claims to be.
        start += int(d["advance"]) if "advance" in d.files else 3584
        nz = np.nonzero(mono)[0]
        if len(nz):
            mono = mono[: nz[-1] + 1]
        axp = axes[1]
        _, _, _, im = axp.specgram(mono, NFFT=1024, Fs=fs, noverlap=512,
                                   xextent=(start / fs,
                                            (start + len(mono)) / fs),
                                   cmap=CMAP_SEQUENTIAL)
        axp.set_ylabel("Hz")
        axp.set_xlabel("s (input timeline)")
        # Quote this panel's own signal, not the best bar in the chart above.
        prog_db = (bar_vals[bar_names.index("programme")]
                   if "programme" in bar_names else float("nan"))
        axp.set_title("What is left over: openTBE minus the SDK, on the "
                      "programme-like signal\n(exact-zero tail trimmed; the "
                      f"whole of it sits at {prog_db:.1f} dB, i.e. numerical "
                      "noise)", fontsize=10)
        fig.colorbar(im, ax=axp, label="dB/Hz")

    fig.tight_layout()
    fig.savefig(fig_path, dpi=150)
    plt.close(fig)
    print(f"  wrote {fig_path}")


def plot_phase5_trajectory() -> None:
    fig_path = FIG_DIR / "phase5_trajectory_residuals.png"
    print(f"[{fig_path.name}]")
    if not PHASE5_NPZ.exists():
        note_skip(fig_path.name,
                   f"{PHASE5_NPZ} not found; run "
                   "'python tools/phase5_dynamic.py' with "
                   "OPENTBE_ORACLE_DIR set to a machine with the SDK oracle "
                   "and bin/tbe_render_rot, bin/tbe_render_traj built")
        return

    d = np.load(PHASE5_NPZ)
    names = [str(n) for n in d["case_names"]]
    vals = d["case_residual_db"].astype(float)
    # phase5_dynamic.py judges the four discrete-step cases at -100 dB and only
    # the continuous sweep at -90 dB. Drawing one -90 line across all of them
    # would colour a genuinely failing discrete case as passing, so take the
    # per-case thresholds from the data. Older npz files predate the key.
    if "case_threshold_db" in d.files:
        thresh = d["case_threshold_db"].astype(float)
    else:
        thresh = np.array([-90.0 if "continuous" in n else -100.0
                           for n in names])

    fig, ax = plt.subplots(figsize=(10, 5.2))
    colors = [COLOR_FLAG if v >= t else COLOR_MEASURED
              for v, t in zip(vals, thresh)]
    bars = ax.bar(range(len(names)), vals, color=colors)
    # Per-case threshold ticks, drawn only across each bar rather than as one
    # line spanning cases it does not apply to.
    for i, t in enumerate(thresh):
        ax.plot([i - 0.42, i + 0.42], [t, t], color="gray", ls="--", lw=1.2,
                zorder=4)
    for b, v in zip(bars, vals):
        ax.annotate(f"{v:.1f}", (b.get_x() + b.get_width() / 2, v),
                    xytext=(0, -12), textcoords="offset points",
                    ha="center", fontsize=8.5, fontweight="bold")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(["\n".join(textwrap.wrap(n, 22))
                        + f"\n(pass < {t:.0f} dB)"
                        for n, t in zip(names, thresh)],
                       fontsize=7.5)
    ax.set_ylim(vals.min() * 1.12, 0)
    ax.set_ylabel("difference from the SDK (dB)\nlower is better")
    ax.set_title("Moving the listener: how closely openTBE matches the "
                 "Audio360 SDK,\nwhile the orientation changes",
                 fontsize=12)
    ax.plot([], [], color="gray", ls="--", lw=1.2,
            label="per-case pass threshold")
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(True, axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(fig_path, dpi=150)
    plt.close(fig)
    print(f"  wrote {fig_path}")


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--include-measured", action="store_true",
        help="also plot the measured SDK filter response, written to "
             "figures/local/ (gitignored). Needs a local measurement; the "
             "measured filters are proprietary-derived and never published.")
    args = ap.parse_args()

    FIG_DIR.mkdir(exist_ok=True)
    print("openTBE validation figures")
    print(f"  data:    {DATA_DIR}")
    print(f"  figures: {FIG_DIR}")
    print()

    n_expected = 4
    plot_filter_comparison()
    plot_orientation_heatmap()
    plot_phase2_residuals()
    plot_phase5_trajectory()
    if args.include_measured:
        n_expected += 1
        plot_filter_comparison(include_measured=True)

    print()
    if skipped:
        print(f"{len(skipped)} of {n_expected} figure(s) skipped, "
              "missing data:")
        for name, reason in skipped:
            print(f"  - {name}: {reason}")
    else:
        print(f"all {n_expected} figures generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
