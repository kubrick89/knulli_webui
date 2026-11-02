#!/bin/sh
# Install (or uninstall with --uninstall) Knulli WebUI on a Knulli/Batocera device.
# Run it on the device, for example over SSH as root.
set -e

APP_DIR="/userdata/system/knulli-webui"
SERVICE_DIR="/userdata/system/services"
SERVICE="knulli_webui"
SRC="$(cd "$(dirname "$0")" && pwd)"

has_services() { command -v batocera-services >/dev/null 2>&1; }

if [ "$1" = "--uninstall" ]; then
    if has_services; then
        batocera-services stop "$SERVICE" 2>/dev/null || true
        batocera-services disable "$SERVICE" 2>/dev/null || true
    else
        "$SERVICE_DIR/$SERVICE" stop 2>/dev/null || true
    fi
    rm -f "$SERVICE_DIR/$SERVICE"
    rm -rf "$APP_DIR"
    echo "Knulli WebUI removed."
    exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is not available on this system." >&2
    exit 1
fi

mkdir -p "$APP_DIR" "$SERVICE_DIR"
# Stop any running instance before updating the files
[ -x "$SERVICE_DIR/$SERVICE" ] && "$SERVICE_DIR/$SERVICE" stop >/dev/null 2>&1 || true

cp -r "$SRC/knulli_webui/." "$APP_DIR/"
[ -f "$APP_DIR/config.json" ] || cp "$SRC/config.example.json" "$APP_DIR/config.json"
cp "$SRC/services/$SERVICE" "$SERVICE_DIR/$SERVICE"
chmod +x "$SERVICE_DIR/$SERVICE" "$APP_DIR/webui.py"

if has_services; then
    batocera-services enable "$SERVICE"
    batocera-services start "$SERVICE"
else
    echo "batocera-services not found: starting directly (add '$SERVICE_DIR/$SERVICE start' to /userdata/system/custom.sh to start it automatically at boot)."
    "$SERVICE_DIR/$SERVICE" start
fi

IP="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)"
echo
echo "Knulli WebUI installed. Open: http://${IP:-<device-ip>}:8088"
