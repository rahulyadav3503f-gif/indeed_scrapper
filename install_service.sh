#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Install the Indeed collector as a systemd service named "indeed".
#
# It installs ONLY the "indeed" unit.  The Naukri service is not touched,
# not restarted and not stopped.
#
#   ./install_service.sh            install and enable (does NOT start)
#   ./install_service.sh --start    install, enable and start
# ---------------------------------------------------------------------------
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="indeed"
UNIT_SOURCE="$PROJECT_DIR/indeed.service"
UNIT_TARGET="/etc/systemd/system/${SERVICE_NAME}.service"
START_AFTER_INSTALL="no"

for arg in "$@"; do
    case "$arg" in
        --start) START_AFTER_INSTALL="yes" ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

if [ ! -f "$UNIT_SOURCE" ]; then
    echo "indeed.service is missing from $PROJECT_DIR" >&2
    exit 1
fi

if [ -x "$PROJECT_DIR/venv/bin/python" ]; then
    PYTHON="$PROJECT_DIR/venv/bin/python"
else
    echo "No virtual environment found at $PROJECT_DIR/venv."
    echo "Create one with:"
    echo "    python3 -m venv --system-site-packages $PROJECT_DIR/venv"
    echo "    $PROJECT_DIR/venv/bin/pip install -r $PROJECT_DIR/requirements.txt"
    PYTHON="$(command -v python3)"
    echo "Falling back to $PYTHON for now."
fi

RUN_USER="$(id -un)"
RUN_GROUP="$(id -gn)"

TMP_UNIT="$(mktemp)"
sed -e "s|^WorkingDirectory=.*|WorkingDirectory=${PROJECT_DIR}|" \
    -e "s|^ExecStart=.*|ExecStart=${PYTHON} ${PROJECT_DIR}/find_indeed_job.py|" \
    -e "s|^User=.*|User=${RUN_USER}|" \
    -e "s|^Group=.*|Group=${RUN_GROUP}|" \
    -e "s|^StandardOutput=.*|StandardOutput=append:${PROJECT_DIR}/indeed.log|" \
    -e "s|^StandardError=.*|StandardError=append:${PROJECT_DIR}/indeed.log|" \
    -e "s|^EnvironmentFile=.*|EnvironmentFile=-${PROJECT_DIR}/.env|" \
    "$UNIT_SOURCE" >"$TMP_UNIT"

echo "Installing ${UNIT_TARGET} ..."
sudo cp "$TMP_UNIT" "$UNIT_TARGET"
rm -f "$TMP_UNIT"
sudo chmod 644 "$UNIT_TARGET"

chmod +x "$PROJECT_DIR/indeed"

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

if [ "$START_AFTER_INSTALL" = "yes" ]; then
    sudo systemctl start "$SERVICE_NAME"
    systemctl --no-pager status "$SERVICE_NAME" | head -n 8
else
    cat <<DONE

Installed and enabled, but NOT started.

Live collection is disabled in find_indeed_job.py
(LIVE_COLLECTION_ENABLED = False), so starting the service now would run the
offline fixture pipeline.  Start it when that is what you want:

    ./indeed start
    ./indeed status
    ./indeed log
DONE
fi
