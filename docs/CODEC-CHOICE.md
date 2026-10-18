# Codec choice

The default profile is AV1. This page explains why, and when to pick another
profile.

## Why AV1 in 2026

AV1 is the royalty-free successor to HEVC. Version 1.0 landed in 2018. Encoder
quality improves every year. SVT-AV1 3.x gives better quality per bit than
x265 at the same encode speed.

Support in the players that matter:

| Player | AV1 in Matroska |
|--------|-----------------|
| VLC 3.0 and newer | Plays. Software decode on any CPU. |
| Jellyfin server | Direct play on clients with AV1 support. |
| Jellyfin web client | Plays in Chrome, Edge and Firefox. |
| mpv and libmpv based players | Plays. |
| Android and TV apps | Plays on recent devices. |

HEVC does not play in most web browsers. That single fact makes AV1 the safer
default for a Jellyfin library.

## Measured numbers

The numbers below come from this machine: AMD Ryzen 5 5600G, 6 cores, 12
threads, ffmpeg 8.0.1, SVT-AV1 3.1.2, x265 4.1.

Input: 30 seconds of 1280x720 at 24 fps, MPEG-2 video at 6 Mbps. The input is
synthetic noise, which is harder to compress than film. Read the numbers as a
speed comparison, not as a quality comparison.

| Encode | Wall time | Output size |
|--------|-----------|-------------|
| SVT-AV1, preset 4, CRF 26 | 18.1 s | 12.3 MB |
| SVT-AV1, preset 6, CRF 30 | 8.4 s | 12.6 MB |
| x265, preset slow, CRF 20 | 27.3 s | 18.3 MB |

A real film encodes faster than these numbers show. Expect 1x to 3x realtime
at preset 4 on a six core CPU. A two hour film takes one to two hours.

## Profiles

Set `video.profile` in the config file.

| Profile | Encoder | Default quality setting | Use when |
|---------|---------|-------------------------|----------|
| `av1` | libsvtav1 | CRF 26, preset 4 | Default. Best size, broad player support. |
| `hevc` | libx265 | CRF 20, preset slow | Your player has no AV1 decode. |
| `h264` | libx264 | CRF 18, preset slow | You need the widest possible support. |
| `copy` | none | n/a | You only want to remux or fix tracks. |

CRF scales differ between codecs. CRF 26 in AV1 and CRF 20 in x265 give
similar quality. Do not copy a CRF value from one profile to another.

## Bit depth

The `av1` and `hevc` profiles encode 10-bit video by default. 10-bit output
removes the banding that 8-bit gradients show, and it costs almost no size.
AV1 and HEVC both support HDR metadata. The tool keeps the source colour
tags, so an HDR10 rip stays HDR10.

## HDR and tone mapping

`video.tonemap` controls what happens to an HDR source:

- `keep`: keep HDR. The tool copies the colour tags.
- `tonemap`: convert to SDR with zscale and the hable tone mapper.
- `auto` (default): tone map only when the target profile is 8-bit, for
  example the `h264` profile.

## Interlaced sources

DVD and some Blu-ray rips are interlaced. The tool reads `field_order` from
ffprobe. When the source is interlaced, it applies `bwdif=mode=send_frame`.
That produces clean progressive video. Set `video.deinterlace = "never"` to
skip the filter.

## Hardware encoders

The default profile uses the CPU. SVT-AV1 on a CPU gives better quality per
bit than any hardware encoder. If you have an Intel Arc, NVIDIA RTX 40 or AMD
RDNA3 card, you can switch to a hardware encoder:

```toml
[video.av1]
encoder = "av1_nvenc"
preset = "p6"
quality_args = ["-rc", "QVBR", "-cq", "30"]
extra_args = ["b:v=0"]
```

`quality_args` replaces the default `-crf` flag. Hardware encoders have no
`crf` option, so you must set this list for them.

The same slot accepts `av1_qsv`, `av1_amf`, `av1_vaapi` or `av1_vulkan`.
Check that your ffmpeg build supports the encoder with
`mkv-transcode doctor`.

## Two-pass sizing

Set `video.target_size_gb` to hit a size target. The tool then runs two
passes. It computes the video bit rate after it subtracts the audio cost.

```toml
[video]
target_size_gb = 4.0
```

The result is approximate. Short clips can miss the target by a wide margin,
because encoder buffers and key frames cost more on a short timeline.
