# FB360 Encoder cross-check: macOS vs Windows

Two files, the real FB360 Encoder's own output, nothing else. Both were
produced from the identical probe (`tools/mkv_order_probe.py --make`, an
8-channel TBE tone-and-burst signal plus head-locked stereo and a black
video), through the `Facebook 360 / TBE mkv` delivery target, on two
different operating systems.

| File | Built from | Encoder build |
|---|---|---|
| `fb360_encoder_output_mac.mkv` | macOS 10.16, GUI | v3.3.3, git `dfc35c2093d1`, 2020-02-05 |
| `fb360_encoder_output_windows.mkv` | Windows 10, GUI | v3.3.3, git `a0e6e0451b36+`, 2020-04-02 |

The Windows build is the exact one that produced the sibling study's 2023
recordings: its git stamp matches the one embedded in that era's surviving
mp4, hash for hash, `+` included.

Full analysis: [docs/PROTOCOL.md](../../docs/PROTOCOL.md), phase 6.
Short version: the two files are not byte-identical (different container
size, different Opus framing throughout, which is normal, expected
behaviour of two independently built Opus encoders and says nothing about
correctness), but decoded to PCM they are exactly, bit-for-bit the same,
every one of 572,808 samples across all 10 channels.

Why these files are safe to publish, when the SDK's measured decode
filters are not: this is not reverse-engineered data. It is ordinary
output of a tool Meta built and distributed for exactly this purpose, run
on content this project generated. It has nothing in common with the
filters in `docs/PROTOCOL.md`'s withheld measurements, which were
extracted from inside the closed SDK binary.

## Verify it yourself

```bash
shasum -a 256 fb360_encoder_output_mac.mkv fb360_encoder_output_windows.mkv
# ccf1b63e910bab805d21bda1dfa6a17cf83baf3323f21687b820e38291871f35  mac
# 1890160645b25b35666dcb73dd5387d406d9443ca51a3bea989112212a33b7bd  windows

ffmpeg -i fb360_encoder_output_mac.mkv     -map 0:a:0 -c:a pcm_f32le mac.wav
ffmpeg -i fb360_encoder_output_windows.mkv -map 0:a:0 -c:a pcm_f32le win.wav
shasum -a 256 mac.wav win.wav
# both: 384adbecb940c61b39f19d2e0bb664242cd7b92c00d8deabc6a941dc080970f6
```

Or run `python tools/mkv_order_probe.py --analyze <file>` on either, which
reports the channel identity independently by tone, level, and burst time
rather than by trusting the container's own claims.
