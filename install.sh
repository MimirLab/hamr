#!/bin/sh
set -eu
if [ "$#" -gt 2 ]; then
    printf '%s\n' 'Usage: install.sh [APP_DIRECTORY [COMMAND_DIRECTORY]]' >&2
    exit 2
fi
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app_dir=${1:-"$HOME/.local/share/hamr-app"}
command_dir=${2:-"$HOME/.local/bin"}
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
mkdir -p "$command_dir"
for command in hamr audiobook; do
    if [ ! -L "$command_dir/$command" ]; then
        ln -s "$app_dir/venv/bin/$command" "$command_dir/$command"
    fi
done
printf 'hamr '
"$app_dir/venv/bin/hamr" --version
