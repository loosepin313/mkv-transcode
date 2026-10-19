#!/usr/bin/env bash
# Install the system packages that mkv-transcode needs.
# Supports Fedora and Rocky Linux. The script uses dnf only.
#
#   sudo scripts/install-deps.sh              # base packages
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
DISTRO="${ID:-unknown}"
echo "distribution: ${PRETTY_NAME:-unknown}"

if ! command -v dnf >/dev/null 2>&1; then
  echo "dnf not found. This script supports Fedora and Rocky Linux only." >&2
  exit 1
fi

# Package names differ between Fedora and RHEL clones. Keep the list short and
# verify the binaries at the end instead of trusting package names.
#
#   ffprobe  ships inside ffmpeg on both. No distro sells it as its own package.
#   nice     ships inside coreutils.
#   venv     Fedora needs python3-venv. RHEL clones build venvs with ensurepip.
PACKAGES=(ffmpeg coreutils util-linux python3 python3-pip)
case "$DISTRO" in
  fedora) PACKAGES+=(python3-venv) ;;
esac

# ffmpeg with libsvtav1, libx265, libx264 and libopus comes from RPM Fusion on
# both distributions. Fedora's own ffmpeg-free build lacks the patent-encumbered
# encoders, so the hevc and h264 profiles need RPM Fusion too.
if [ "$enable_rpmfusion" = "1" ]; then
  case "$DISTRO" in
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
      echo "unknown distribution $DISTRO, skip RPM Fusion setup" >&2
      ;;
  esac
fi

# dnf5 knows --skip-unavailable, dnf4 knows --skip-broken. One unknown name must
# not stop the rest of the install.
SKIP_OPT=""
if dnf install --help 2>&1 | grep -q -- "--skip-unavailable"; then
  SKIP_OPT="--skip-unavailable"
elif dnf install --help 2>&1 | grep -q -- "--skip-broken"; then
  SKIP_OPT="--skip-broken"
fi

echo "install: ${PACKAGES[*]}"
# shellcheck disable=SC2086
dnf -y $SKIP_OPT install "${PACKAGES[@]}"

# Report what the transaction could not provide, then check the real need.
missing=0
for tool in ffmpeg ffprobe; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "ERROR: $tool is missing." >&2
    missing=1
  fi
done
if [ "$missing" = "1" ]; then
  echo "ffmpeg comes from RPM Fusion. Run this script with --rpmfusion, then" >&2
  echo "check the repo: dnf repolist | grep rpmfusion" >&2
  exit 1
fi

if ! python3 -m venv --help >/dev/null 2>&1; then
  echo "ERROR: python3 -m venv failed. Install python3-venv (Fedora) or" >&2
  echo "python3-pip (Rocky) and try again." >&2
  exit 1
fi

echo
echo "ffmpeg:  $(ffmpeg -version | head -1)"
echo "ffprobe: $(ffprobe -version | head -1)"
echo "python:  $(python3 --version)"

if command -v mkvmerge >/dev/null 2>&1; then
  echo "mkvtoolnix: $(mkvmerge --version | head -1)"
else
  echo "note: mkvtoolnix is optional. Install it with 'dnf install mkvtoolnix' if you" \
       "want to inspect or edit Matroska files by hand."
fi

echo
echo "Done. Check the machine with: mkv-transcode doctor"
