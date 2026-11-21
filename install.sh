#!/bin/sh
set -eu
if [ "$#" -gt 2 ]; then
    printf '%s\n' 'Usage: install.sh [APP_DIRECTORY [COMMAND_DIRECTORY]]' >&2
    exit 2
fi
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app_dir=${1:-"$HOME/.local/share/hamr-app"}
command_dir=${2:-"$HOME/.local/bin"}
app_dir=$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$app_dir")
command_dir=$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$command_dir")
manual_root=${HAMR_MAN_DIR:-"$command_dir/../share/man"}
manual_root=$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$manual_root")
if [ "$#" -eq 0 ]; then
    # Retain an active legacy venv: saved launchers may pin its absolute path.
    legacy_app="$HOME/.local/share/audiobook-app"
    primary_target=$(readlink "$command_dir/hamr" || true)
    legacy_target=$(readlink "$command_dir/audiobook" || true)
    if [ -x "$legacy_app/venv/bin/python" ] && {
        [ "$primary_target" = "$legacy_app/venv/bin/hamr" ] || {
            [ -z "$primary_target" ] && [ "$legacy_target" = "$legacy_app/venv/bin/audiobook" ];
        };
    }; then
        app_dir=$legacy_app
    fi
fi
app_version=$(python3 -c 'import sys,tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["project"]["version"])' "$project_dir/pyproject.toml")
wheel="$project_dir/dist/hamr-${app_version}-py3-none-any.whl"
if [ ! -f "$wheel" ]; then
    printf 'Missing current wheel: %s\nBuild it with uv build --wheel.\n' "$wheel" >&2
    exit 2
fi
for command in hamr audiobook; do
    if [ -e "$command_dir/$command" ] || [ -L "$command_dir/$command" ]; then
        if [ "$(readlink "$command_dir/$command" || true)" != "$app_dir/venv/bin/$command" ]; then
            printf 'Existing %s command belongs to another installation; no environment or link changed.\n' "$command" >&2
            exit 2
        fi
    fi
done
for command in hamr audiobook; do
    page="$manual_root/man1/$command.1"
    target="$app_dir/venv/share/man/man1/$command.1"
    if [ -e "$page" ] || [ -L "$page" ]; then
        if [ "$(readlink "$page" || true)" != "$target" ]; then
            printf 'Existing manual belongs to another installation: %s\nSet HAMR_MAN_DIR to a different manual directory.\n' "$page" >&2
            exit 2
        fi
    fi
done
requirements_file=$(mktemp)
trap 'rm -f "$requirements_file"' 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
uv export --project "$project_dir" --locked --no-dev --no-emit-project --format requirements-txt > "$requirements_file"
# One exact sync replaces the former distribution and avoids overlapping records
# for the same Python modules. Hash the local wheel as well as its dependencies.
python3 - "$wheel" >> "$requirements_file" <<'PY'
import hashlib, sys
from pathlib import Path
wheel = Path(sys.argv[1]).resolve()
with wheel.open('rb') as stream:
    checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
print(f'\nhamr @ {wheel.as_uri()} --hash=sha256:{checksum}')
PY
uv venv --allow-existing --python 3.12 "$app_dir/venv"
uv pip sync --python "$app_dir/venv/bin/python" --require-hashes "$requirements_file"
installed_version=$("$app_dir/venv/bin/hamr" --version)
if [ "$installed_version" != "$app_version" ]; then
    printf 'Installed version %s differs from expected %s.\n' "$installed_version" "$app_version" >&2
    exit 2
fi
mkdir -p "$command_dir" "$manual_root/man1"
for command in hamr audiobook; do
    if [ ! -L "$command_dir/$command" ]; then
        ln -s "$app_dir/venv/bin/$command" "$command_dir/$command"
    fi
    page="$manual_root/man1/$command.1"
    target="$app_dir/venv/share/man/man1/$command.1"
    if [ ! -f "$target" ]; then
        printf 'Installed wheel is missing %s\n' "$target" >&2
        exit 2
    fi
    if [ ! -L "$page" ]; then
        ln -s "$target" "$page"
    fi
done
printf 'hamr %s\n' "$installed_version"
if [ "$(command -v hamr || true)" != "$command_dir/hamr" ]; then
    python3 -c 'import shlex,sys; quote=chr(34); print("Use the installed command at " + shlex.quote(sys.argv[1] + "/hamr") + " or run:\n  export PATH=" + shlex.quote(sys.argv[1]) + ":" + quote + "$PATH" + quote)' "$command_dir" >&2
fi
if ! command -v man >/dev/null 2>&1; then
    printf 'Manual installed in %s/man1. Install your distribution’s man package to browse it.\n' "$manual_root" >&2
elif ! man -w -M "$manual_root" 1 hamr >/dev/null 2>&1; then
    printf 'Manual installed in %s/man1. Open the hamr.1 file with man -l.\n' "$manual_root" >&2
elif [ ! "$(man -w 1 hamr 2>/dev/null || true)" -ef "$manual_root/man1/hamr.1" ]; then
    python3 -c 'import shlex,sys; print("Open the manual with: man -M " + shlex.quote(sys.argv[1]) + " hamr")' "$manual_root" >&2
fi
