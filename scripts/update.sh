#!/usr/bin/env bash
# Auto-update script for heating-brain.
# Pulls latest from GitHub, reinstalls deps if requirements changed,
# fixes ownership, and restarts the service only if anything changed.
#
# Runs as root via the systemd oneshot. The repo is owned by
# heating-brain so every git call passes safe.directory inline to
# sidestep git's CVE-2022-24765 ownership check.

set -euo pipefail

REPO_DIR="/opt/heating-brain"
SERVICE_USER="heating-brain"
SERVICE_NAME="heating-brain"

GIT=(git -C "$REPO_DIR" -c "safe.directory=$REPO_DIR")

before=$("${GIT[@]}" rev-parse HEAD)
"${GIT[@]}" fetch --quiet origin main
after=$("${GIT[@]}" rev-parse origin/main)

if [ "$before" = "$after" ]; then
    echo "Already up to date at $before"
    exit 0
fi

echo "Updating $before -> $after"
"${GIT[@]}" pull --ff-only --quiet origin main

# Reinstall Python deps only if requirements.txt changed in this pull.
if "${GIT[@]}" diff --name-only "$before" "$after" | grep -q '^app/requirements.txt$'; then
    echo "requirements.txt changed — reinstalling"
    "$REPO_DIR/venv/bin/pip" install --quiet -r "$REPO_DIR/app/requirements.txt"
fi

# Fix ownership so the service user can read new files.
chown -R "$SERVICE_USER":"$SERVICE_USER" "$REPO_DIR"

echo "Restarting $SERVICE_NAME"
/usr/bin/systemctl restart "$SERVICE_NAME"

echo "Update complete."
