#!/bin/sh
# Arachnid through Docker, for Mac and Linux.  Only Docker is needed - no Python, no npm.
#   ./arachnid.sh scan  ~/code/my-site        (or: cd ~/code/my-site && /path/to/arachnid.sh scan)
#   ./arachnid.sh dashboard ~/code/my-site    then open http://127.0.0.1:8765
#   ./arachnid.sh fix ~/code/my-site     ./arachnid.sh undo ~/code/my-site     ./arachnid.sh help
set -e
IMAGE="${ARACHNID_IMAGE:-ghcr.io/meetraval09/arachnid:latest}"
PORT="${ARACHNID_PORT:-8765}"

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is not installed. Get Docker Desktop: https://www.docker.com/products/docker-desktop/"; exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "Docker is not running. Start Docker Desktop, wait until it says it is running, and try again."; exit 1
fi

cmd="${1:-help}"; [ $# -gt 0 ] && shift
if [ "$cmd" = "update" ]; then exec docker pull "$IMAGE"; fi

project="$PWD"
if [ $# -gt 0 ] && [ -d "$1" ]; then project="$1"; shift; fi
project="$(cd "$project" && pwd -P)"

# One storage box (Docker volume) per project, so results and undo backups survive between runs
if command -v sha1sum >/dev/null 2>&1; then h="$(printf %s "$project" | sha1sum | cut -c1-12)"
else h="$(printf %s "$project" | shasum | cut -c1-12)"; fi
vol="arachnid-$h"

tty=""; [ -t 0 ] && [ -t 1 ] && tty="-it"
case "$cmd" in
    help|version|selftest|accuracy)      # these need no project
        exec docker run --rm $tty "$IMAGE" "$cmd" ;;
esac
ports=""; [ "$cmd" = "dashboard" ] && ports="-p 127.0.0.1:$PORT:8765"     # reachable from this computer only
envs=""; [ -n "$GITHUB_TOKEN" ] && envs="-e GITHUB_TOKEN"                  # optional: the dashboard's GitHub panel
exec docker run --rm $tty --user "$(id -u):$(id -g)" $ports $envs -e ARACHNID_PORT="$PORT" -e ARACHNID_PROJECT_NAME="$(basename "$project")" \
    -v "$project:/project" -v "$vol-reports:/app/reports" -v "$vol-backups:/app/backups" \
    "$IMAGE" "$cmd" "$@"
