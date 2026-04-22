#!/usr/bin/env bash
# Auto-update script for heating-brain.
# Pulls latest from GitHub, reinstalls deps if requirements changed,
# fixes ownership, and restarts the service only if anything changed.
#
# Run as the user that owns /opt/heating-brain (the one who cloned it),
# not as heating-brain (which has no shell).

set -euo pipefail

REPO_DIR="/opt/heating-brain"
SERVICE_USER="heating-brain"
SERVICE_NAME="heating-brain"

cd "$REPO_DIR"

before=$(git rev-parse HEAD)
git fetch --quiet origin main
after=$(git rev-parse origin/main)

if [ "$before" = "$after" ]; then
    echo "Already up to date at $before"
    exit 0
fi

echo "Updating $before -> $after"
git pull --ff-only --quiet origin main

# Reinstall Python deps only if requirements.txt changed in this pull.
if git diff --name-only "$before" "$after" | grep -q '^app/requirements.txt$'; then
    echo "requirements.txt changed — reinstalling"
    ./venv/bin/pip install --quiet -r app/requirements.txt
fi

# Fix ownership so the service user can read new files.
# (Script runs as root via the systemd oneshot — no sudo needed.)
chown -R "$SERVICE_USER":"$SERVICE_USER" "$REPO_DIR"

echo "Restarting $SERVICE_NAME"
/usr/bin/systemctl restart "$SERVICE_NAME"

echo "Update complete."
