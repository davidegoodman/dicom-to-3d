#!/usr/bin/env bash
# install.sh — set up SpaceMouseBT for 3D Slicer on Linux
#
# Run once (with sudo for the udev step):
#   chmod +x setup/install.sh
#   sudo ./setup/install.sh
#
# Non-root tasks (pip install) are run as the invoking user.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULES_SRC="$SCRIPT_DIR/99-3dconnexion.rules"
RULES_DST="/etc/udev/rules.d/99-3dconnexion.rules"

echo "=== SpaceMouseBT installer ==="

# ── 1. udev rules ──────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo ""
    echo "WARNING: not running as root — skipping udev rules installation."
    echo "Re-run with sudo to install rules, or manually:"
    echo "  sudo cp '$RULES_SRC' '$RULES_DST'"
    echo "  sudo udevadm control --reload-rules && sudo udevadm trigger"
else
    echo ""
    echo "Installing udev rules → $RULES_DST"
    cp "$RULES_SRC" "$RULES_DST"
    udevadm control --reload-rules
    udevadm trigger
    echo "  udev rules installed."

    # Add current user to 'input' group if needed
    REAL_USER="${SUDO_USER:-$USER}"
    if ! id -nG "$REAL_USER" | grep -qw input; then
        echo "  Adding $REAL_USER to the 'input' group (log out and back in)."
        usermod -aG input "$REAL_USER"
    else
        echo "  $REAL_USER is already in the 'input' group."
    fi
fi

# ── 2. Python dependencies ──────────────────────────────────────────────────
echo ""
echo "Installing Python dependencies (pyspacemouse, hidapi)…"

# Detect Slicer's Python if available
SLICER_PYTHON=""
for candidate in \
    "$HOME/Applications/Slicer/bin/PythonSlicer" \
    "/opt/slicer/bin/PythonSlicer" \
    "/usr/local/bin/PythonSlicer" \
    "$(which PythonSlicer 2>/dev/null || true)"
do
    if [[ -x "$candidate" ]]; then
        SLICER_PYTHON="$candidate"
        break
    fi
done

if [[ -n "$SLICER_PYTHON" ]]; then
    echo "  Using Slicer Python: $SLICER_PYTHON"
    "$SLICER_PYTHON" -m pip install --quiet pyspacemouse hidapi
else
    echo "  Slicer Python not found — installing into system/user Python."
    echo "  You can also click 'Install Python dependencies' inside the module."
    python3 -m pip install --user --quiet pyspacemouse hidapi
fi
echo "  Python dependencies installed."

# ── 3. Bluetooth pairing reminder ──────────────────────────────────────────
echo ""
echo "─────────────────────────────────────────────────────────────────────"
echo "Bluetooth pairing (if not done already):"
echo ""
echo "  bluetoothctl"
echo "    [bluetooth] scan on"
echo "    [bluetooth] pair   <MAC>"
echo "    [bluetooth] trust  <MAC>"
echo "    [bluetooth] connect <MAC>"
echo ""
echo "Then launch 3D Slicer and load the SpaceMouse BT module."
echo "─────────────────────────────────────────────────────────────────────"
echo ""
echo "Done."
