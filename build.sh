#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
build_dir="$(mktemp -d "${TMPDIR:-/tmp}/ping-pong-build.XXXXXX")"
trap 'rm -rf -- "$build_dir"' EXIT
export PIP_CACHE_DIR="$build_dir/pip-cache"
export PYINSTALLER_CONFIG_DIR="$build_dir/pyinstaller-cache"
python3 -m venv "$build_dir/venv"
"$build_dir/venv/bin/python" -m pip install -r "$project_dir/requirements-desktop.txt" 'pyinstaller>=6.16,<7'
"$build_dir/venv/bin/python" -m PyInstaller --clean --noconfirm --onefile \
    --name ping-pong --distpath "$build_dir/dist" --workpath "$build_dir/work" \
    --specpath "$build_dir" "$project_dir/app.py"
# Replace the release only after a successful build, without truncating a running binary.
install -m 755 "$build_dir/dist/ping-pong" "$project_dir/.ping-pong.new"
mv -f -- "$project_dir/.ping-pong.new" "$project_dir/ping-pong"
printf 'Built %s/ping-pong\n' "$project_dir"
