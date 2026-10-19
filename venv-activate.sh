#!/usr/bin/env bash
# Activate the mkv-transcode virtual environment and mark the prompt.
#
# Source this file. Do not run it:
#
#     source ./venv-activate.sh
#
# Run 'deactivate' to leave the environment.

# Stop when a user runs the file instead of sourcing it. Activation cannot
# change the parent shell from a child process.
if [ -n "${BASH_SOURCE[0]:-}" ] && [ "${BASH_SOURCE[0]}" = "$0" ]; then
    echo "source this file instead: . ${BASH_SOURCE[0]}" >&2
    exit 1
fi

# Find the repo folder from the location of this file, so the script works
# from any directory.
_self="${BASH_SOURCE[0]:-$0}"
_repo="$(cd "$(dirname "$_self")" && pwd)"
_venv="${MKVTC_VENV:-$_repo/.venv}"

# Create the environment when it does not exist yet.
if [ ! -f "$_venv/bin/activate" ]; then
    echo "creating the virtual environment in $_venv"
    python3 -m venv "$_venv" || return 1
    "$_venv/bin/pip" install -q -e "$_repo" || return 1
fi

# Keep the default '(.venv)' prompt off, then add our own marker. The venv
# 'deactivate' function restores PS1 from _OLD_VIRTUAL_PS1.
export VIRTUAL_ENV_DISABLE_PROMPT=1
# shellcheck disable=SC1090,SC1091
. "$_venv/bin/activate"

case "$PS1" in
    *"(mkv-transcode)"*) : ;;
    *)
        _OLD_VIRTUAL_PS1="$PS1"
        export _OLD_VIRTUAL_PS1
        PS1="(mkv-transcode) $PS1"
        export PS1
        ;;
esac

unset _self _repo _venv
echo "mkv-transcode environment is active. Run 'deactivate' to leave it."
