#!/usr/bin/env bash
# Install the system packages that mkv-transcode needs.
# Supports Fedora and Rocky Linux. The script uses dnf only.
#
#   sudo scripts/install-deps.sh            # base packages + IMDb tooling
#   sudo scripts/install-deps.sh --rpmfusion  # also enable RPM Fusion for ffmpeg
set -euo pipefail

enable_rpmfusion=0
for arg in "$@"; do
  case "$arg" in
    --rpmfusion) enable_rpmfusion=1 ;;
    -h|--help)
      echo "usage: install-deps.sh [--rpmfusion]"
      exit 0
      ;;
  esac
done

. /etc/os-release
echo "distribution: ${PRETTY_NAME:-unknown}"

if ! command -v dnf >/dev/null 2>&1; then
  echo "dnf not found. This script supports Fedora and Rocky Linux only." >&2
  exit 1
fi

# ffmpeg with libsvtav1, libx265, libx264 and libopus comes from RPM Fusion on
# both distributions. Fedora's own ffmpeg-free build lacks the patent-encumbered
# encoders, so the hevc and h264 profiles need RPM Fusion.
if [ "$enable_rpmfusion" = "1" ]; then
  case "${ID:-}" in
    fedora)
      dnf -y install \
        "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm"
      ;;
    rocky|centos|rhel|almalinux)
      dnf -y install epel-release
      dnf -y install \
        "https://mirrors.rpmfusion.org/free/el/rpmfusion-free-release-$(rpm -E %rhel).noarch.rpm"
      ;;
    *)
      echo "unknown distribution ${ID:-}, skip RPM Fusion setup" >&2
      ;;
  esac
fi

dnf -y install \
  ffmpeg \
  ffprobe \
  python3 \
  python3-pip \
  python3-venv \
  nice \
  util-linux

# nice comes from coreutils, ionice from util-linux. Install them explicitly so
# the runner can lower the priority of a batch job.
dnf -y install coreutils >/dev/null 2>&1 || true

if command -v mkvmerge >/dev/null 2>&1; then
  echo "mkvtoolnix: $(mkvmerge --version | head -1)"
else
  echo "note: mkvtoolnix is optional. Install it with 'dnf install mkvtoolnix' if you" \
       "want to inspect or edit Matroska files by hand."
fi

echo
echo "Done. Check the machine with: mkv-transcode doctor"
