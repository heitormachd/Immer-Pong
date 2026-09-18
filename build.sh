#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v docker >/dev/null 2>&1; then
    printf 'Docker is required. Install Docker and the Docker Compose plugin, then retry.\n' >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    printf 'The Docker Compose plugin is required (docker compose).\n' >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    printf 'Cannot access Docker. Start the Docker daemon and check your Docker permissions.\n' >&2
    exit 1
fi

cd -- "$project_dir"
printf '%s\n' \
    'Building and starting both local servers:' \
    '  Main: http://localhost:8080/ — data/' \
    '  Test: http://localhost:8081/ — test_data/' \
    'Logs appear below. Press Ctrl+C to stop both servers.' \
    'Ports 8080 and 8081 must be free of standalone servers.'
exec docker compose -f "$project_dir/compose.yaml" up --build --abort-on-container-exit
