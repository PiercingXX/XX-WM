#!/bin/sh
# Piercing WM dev-loop deploy: rsync launcher/src/ to the phone and restart
# the shell service. Usage:
#   PIERCING_DEVICE=192.168.1.20 [PIERCING_USER=user] scripts/deploy.sh [--dry-run]
# GitHub.com/PiercingXX

set -u

REPO_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)

DEVICE=${PIERCING_DEVICE:-}
USER_NAME=${PIERCING_USER:-user}
DRY_RUN=0

for arg in "$@"; do
    case $arg in
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            echo "Usage: PIERCING_DEVICE=<host> [PIERCING_USER=user] $0 [--dry-run]"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

if [ -z "$DEVICE" ]; then
    echo "PIERCING_DEVICE is required (hostname or IP of the phone)." >&2
    exit 1
fi

TARGET="${USER_NAME}@${DEVICE}"
RSYNC_FLAGS="-az --delete"

# Restart the shell service — systemd user unit or OpenRC, whichever runs
# shellcheck disable=SC2016  # expands remotely, not here
RESTART_CMD='
if command -v systemctl >/dev/null 2>&1 && [ "$(ps -p 1 -o comm=)" = systemd ]; then
    systemctl --user restart piercing-shell
elif command -v rc-service >/dev/null 2>&1; then
    doas rc-service piercing-shell restart 2>/dev/null || sudo rc-service piercing-shell restart
else
    echo "no known service manager — restart the shell manually" >&2
    exit 1
fi'

if [ "$DRY_RUN" = 1 ]; then
    # Echo mode: show exactly what would run, touch nothing
    echo "[dry-run] rsync $RSYNC_FLAGS $REPO_DIR/launcher/src/ ${TARGET}:piercing-shell/src/"
    echo "[dry-run] ssh $TARGET '<restart piercing-shell via systemd-user or OpenRC>'"
    exit 0
fi

echo "Deploying launcher/src/ -> ${TARGET}:~/piercing-shell/src/"
# shellcheck disable=SC2086  # RSYNC_FLAGS is intentionally word-split
rsync $RSYNC_FLAGS "$REPO_DIR/launcher/src/" "${TARGET}:piercing-shell/src/" || exit 1

echo "Restarting piercing-shell on ${DEVICE}..."
# shellcheck disable=SC2029  # client-side expansion is intended
ssh "$TARGET" "$RESTART_CMD"
