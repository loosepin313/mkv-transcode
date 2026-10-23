#!/usr/bin/env bash
# Install mkv-transcode into a virtual environment and install the systemd units.
#
#   sudo scripts/install.sh                 # install the app only
#   sudo scripts/install.sh --systemd       # also install and enable the units
#   sudo scripts/install.sh --user          # install units in the user manager
#
# Layout:
#   /opt/mkv-transcode            virtual environment with the package
#   /usr/local/bin/mkv-transcode  small launcher for the venv binary
#   /etc/mkv-transcode/config.toml  configuration (created only when missing)
#   /etc/systemd/system/          system units, with --systemd
set -euo pipefail

prefix=/opt/mkv-transcode
launcher=/usr/local/bin/mkv-transcode
config_dir=/etc/mkv-transcode
service_user=mkvtranscode
install_systemd=0
install_user=0

for arg in "$@"; do
  case "$arg" in
    --systemd) install_systemd=1 ;;
    --user) install_user=1 ;;
    --prefix=*) prefix="${arg#*=}" ;;
    -h|--help)
      sed -n '2,14p' "$0"
      exit 0
      ;;
  esac
done

here="$(cd "$(dirname "$0")/.." && pwd)"
python3 -m venv "$prefix"
"$prefix/bin/pip" install --quiet --upgrade pip
"$prefix/bin/pip" install --quiet "$here"

mkdir -p "$(dirname "$launcher")"
cat > "$launcher" <<EOF
#!/bin/sh
exec "$prefix/bin/mkv-transcode" "\$@"
EOF
chmod 0755 "$launcher"
echo "installed $launcher"

if [ ! -f "$config_dir/config.toml" ]; then
  mkdir -p "$config_dir"
  "$launcher" config-file > "$config_dir/config.toml"
  echo "wrote $config_dir/config.toml"
fi

if [ "$install_user" = "1" ]; then
  mkdir -p "$HOME/.config/systemd/user"
  for unit in "$here"/systemd/user/*.service "$here"/systemd/user/*.path; do
    [ -e "$unit" ] || continue
    install -m 0644 "$unit" "$HOME/.config/systemd/user/"
    echo "installed $HOME/.config/systemd/user/$(basename "$unit")"
  done
  systemctl --user daemon-reload
  echo "run: systemctl --user enable --now mkv-transcode.service"
  exit 0
fi

if [ "$install_systemd" = "1" ]; then
  if ! id "$service_user" >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/mkv-transcode --shell /sbin/nologin "$service_user"
    echo "created service user $service_user"
  fi
  mkdir -p /etc/systemd/system
  for unit in "$here"/systemd/*.service "$here"/systemd/*.path; do
    [ -e "$unit" ] || continue
    install -m 0644 "$unit" /etc/systemd/system/
    echo "installed /etc/systemd/system/$(basename "$unit")"
  done
  systemctl daemon-reload
  echo "next steps:"
  echo "  1. edit $config_dir/config.toml and set the paths"
  echo "  2. create the incoming and transcoded directories, owned by $service_user"
  echo "  3. systemctl enable --now mkv-transcode.service"
  echo "  4. systemctl enable --now mkv-transcode-status.service   # page on port 8090"
fi
