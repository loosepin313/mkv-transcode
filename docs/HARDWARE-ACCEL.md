# Hardware encode with NVENC

ffmpeg can encode on an NVIDIA GPU. This tool drives NVENC through the normal
encoder settings, so you pick an encoder name instead of a CPU library.

## What your GPU can encode

NVENC depends on the GPU generation. `mkv-transcode doctor` checks this for
you.

| GPU | Compute capability | H.264 | HEVC 10-bit | AV1 |
|-----|--------------------|-------|-------------|-----|
| GTX 10 series (Pascal) | 6.1 | yes | no | no |
| GTX 16 series (Turing) | 7.5 | yes | yes | no |
| RTX 30 series (Ampere) | 8.6 | yes | yes | no |
| RTX 40 series (Ada) | 8.9 | yes | yes | yes |

NVENC encodes AV1 only from compute capability 8.9 up. A GTX 1660 Super sits
at 7.5, so it cannot encode AV1. Use `hevc_nvenc` on those cards.

## Install the driver

The driver must come from your distribution, not from a downloaded .run file.

```bash
# Rocky Linux or Fedora
sudo dnf -y install https://download1.rpmfusion.org/nonfree/el/rpmfusion-nonfree-release-10.noarch.rpm
sudo dnf -y install nvidia-driver
sudo reboot
nvidia-smi
```

Check that ffmpeg sees the encoder:

```bash
ffmpeg -hide_banner -encoders | grep nvenc
```

## One GPU

Set the profile to the GPU encoder. The `crf` value maps to NVENC quality
control, so you do not need extra arguments.

```toml
[video]
profile = "hevc"

[video.hevc]
encoder = "hevc_nvenc"
crf = 23
preset = "p5"
pixel_format = "yuv420p10le"
```

`preset` for NVENC takes `p1` to `p7`. `p1` is the fastest, `p7` the slowest.
The CPU presets such as `slow` also work, but `p1` to `p7` are clearer.

The tool writes these arguments for the profile above:

```
-c:v:0 hevc_nvenc -rc vbr -cq 23 -b:v 0 -preset p5 -pix_fmt yuv420p10le
```

NVENC has no `crf`. The tool uses `-rc vbr -cq <crf>` instead. Write your own
`quality_args` if you want constant quality or a fixed bitrate.

## Two GPUs

Give one index per job. The tool sets `CUDA_VISIBLE_DEVICES` for each ffmpeg
process, so two jobs use two cards.

```toml
[gpu]
devices = [0, 1]

[job]
max_parallel = 2
```

Rules:

- The GPU count caps the number of running jobs. With `devices = [0, 1]` and
  `max_parallel = 4`, the watcher still runs only two jobs at a time.
- An empty `devices` list means no pinning. ffmpeg then picks the first GPU,
  and every job shares it.
- `nvidia-smi -L` shows the indexes you should list.

Each job log carries a `gpu: N` line. The status page shows the same value on
the running job.

## Split one job over two GPUs

One ffmpeg process uses one GPU. To put a single film on both cards, turn on the
split. The tool cuts the film into segments by time. Each segment encodes on its
own card at the same time. A stream copy then joins the parts.

```toml
[gpu]
devices = [0, 1]
split = true
min_segment_seconds = 300.0
```

How it works:

- The tool writes one segment per card. A 2 hour film on two cards gives two
  segments of 1 hour.
- Each segment starts with a fresh keyframe, so the join shows no seam.
- Audio and subtitles do not follow the split. Cutting them adds padding at
  every join and makes the file longer than the source. The tool encodes them
  once, in parallel with the video, and muxes them in at the join.
- The join is a stream copy. It costs no quality, and takes a few seconds.
- A split job holds every card, so only one such job runs at a time.
- `min_segment_seconds` stops a short film from being cut into slivers. A film
  shorter than twice that value runs as one piece.

Measured on frank with two GTX 1660 SUPER cards and a 120 second 1080p clip:

| Mode | Wall time |
|------|-----------|
| One ffmpeg process on one card | 61 s |
| Two segments on two cards | 32 s |

Limits of the split path:

- It needs a single pass. `target_size_gb` runs two passes, so the split stays
  off there.
- The job needs `2 * min_segment_seconds` of running time or more.
- The work folder holds one segment per card while the job runs. A 40 GB film
  on two cards needs the same disk as one piece, plus a little.
- Progress on the page is the sum of the segments, so the bar moves smoothly.

## What the status page shows

When the box has a GPU, the page adds one card per GPU under the main cards.
Each card shows:

- The GPU name and index.
- Encoder utilisation as a percent and a bar.
- GPU memory used and total, in MiB.
- The number of NVENC sessions and their fps.
- The temperature in degrees C.
- The title of the job that holds that GPU.

Set `gpu.stats = false` to hide the cards. The page reads `nvidia-smi` and
caches the result for two seconds.

## Checks in doctor

```
OK    gpu                    2 GPU: GPU 0 NVIDIA GeForce GTX 1660 SUPER, GPU 1 NVIDIA GeForce GTX 1660 SUPER
OK    nvenc av1              ...
WARN  gpu.devices            empty, so every job uses GPU 0. List one index per job, for example [0, 1].
```

`doctor` fails when `av1_nvenc` points at a GPU that cannot encode AV1. It
also fails when a listed index does not exist.

## Limits

- NVENC at the same quality number gives a larger file than `libx265` on the
  `slow` preset. Drop `crf` by two or three to match the size.
- `target_size_gb` runs two passes. That path is written for CPU encoders. For
  a size target on NVENC, set `quality_args = ["-b:v", "6000k"]` instead.
- The GPU must be visible to the service user. The systemd unit needs
  `/dev/nvidia*`. The shipped unit does not block these devices.
- HDR tone mapping still runs on the CPU. The tool uses `bwdif` and `zscale`
  filters, not the CUDA versions.
