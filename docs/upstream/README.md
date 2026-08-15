Snapshots of the two upstream sources the licensing question in docs/PLAN.md
and docs/PROTOCOL.md relies on. Kept here rather than only linked, since both
live on archives that can vanish: GitHub can delete an "archived" org repo
without notice, and Wayback captures are not permanent either.

## audio360-mit/

The coefficient files, the decoder algorithm and LICENSE from
[facebookarchive/Audio360](https://github.com/facebookarchive/Audio360),
commit `171bfbfa69c4724026ef8d06a0f5155b1a9de32b` (2018-10-19), unmodified,
MIT licensed, "Copyright (c) 2018-present, Facebook, Inc.":

- `AmbiBinauralCoefficients{2OA,3OA}.{cpp,hh}` - the published per-harmonic
  impulse responses, both 44.1 and 48 kHz. `tools/generate_mit_filters.py`
  parses the 2OA set to build openTBE's shipped filter set (see PLAN.md).
  A first pass checked the measured impulse-response matrix against these
  by unconstrained least-squares projection (5.4e-4 relative residual
  energy at exact alignment); PLAN.md and PROTOCOL.md describe the more
  precise, zero-fit derivation that superseded it.
- `AmbiSphericalConvolution.{cpp,hh}` - Meta's own Ambisonics-to-binaural
  decoder: convolve each ACN harmonic with its mono IR, then combine
  harmonics with m >= 0 into L and R identically and harmonics with m < 0
  into L as +f and R as -f. This is the algorithm half of the derivation
  in tools/generate_mit_filters.py and tools/phase4_headtrack.py.

## Facebook360AudioTerms_2017-08-11.txt

The Facebook 360 Audio Terms (last updated 11 Aug 2017), the click-wrap terms
that govern the Audio360 SDK download. Recovered from a Wayback capture of
the SDK 1.3.0 download page, dated 2020-06-03:
https://web.archive.org/web/20200603124316/https://facebook360.fb.com/downloads/rendering-sdk-v1-3-0/

No separate license file ships inside the SDK 1.7.12 zip actually used by
this project (checked directly; only ThirdPartyNotices.txt and release
notes are present), so this page is the only recovered source for the
terms. It reaches 1.7.12 only via the Terms' own "updates, upgrades and/or
new versions" clause. Relevant for what it does and does not restrict; see
PLAN.md for the analysis. Note in particular: clause 7 limits the user's
remedies against Facebook, not Facebook's remedies against the user, and
should not be read as any kind of protection.
