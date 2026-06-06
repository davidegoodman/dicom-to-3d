"""
SpaceMouseBT - 3DConnexion SpaceMouse Bluetooth integration for 3D Slicer.

Drop the SpaceMouseBT/ folder into any path listed under:
  Edit → Application Settings → Modules → Additional module paths

Then find "SpaceMouse BT" in the Navigation category.
"""

import logging
import math
import os
import threading
import time

import qt
import vtk
import slicer
from slicer.ScriptedLoadableModule import (
    ScriptedLoadableModule,
    ScriptedLoadableModuleLogic,
    ScriptedLoadableModuleWidget,
)

logger = logging.getLogger(__name__)

# 3DConnexion vendor ID and known Bluetooth product IDs.
# Used when pyspacemouse cannot auto-detect the device.
_3DX_VENDOR_ID = 0x256F
_BT_PRODUCT_IDS = {
    0xC652: "SpaceMouse Bluetooth",
    0xC657: "SpaceMouse Compact BT",
    0xC62E: "SpaceMouse Wireless (BT mode)",
    0xC62F: "SpaceMouse Wireless (receiver)",
}


# ---------------------------------------------------------------------------
# Module descriptor
# ---------------------------------------------------------------------------

class SpaceMouseBT(ScriptedLoadableModule):
    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = "SpaceMouse BT"
        self.parent.categories = ["Navigation"]
        self.parent.dependencies = []
        self.parent.contributors = ["dicom-to-3d project"]
        self.parent.helpText = (
            "Connect a 3DConnexion SpaceMouse (Bluetooth or USB) to control "
            "the 3D Slicer viewport.  Provides 6-DOF navigation: pan, zoom, "
            "and rotate the 3D view in real time."
        )
        self.parent.acknowledgementText = ""


# ---------------------------------------------------------------------------
# Widget (UI)
# ---------------------------------------------------------------------------

class SpaceMouseBTWidget(ScriptedLoadableModuleWidget):
    POLL_INTERVAL_MS = 16  # ~60 fps drain rate

    def __init__(self, parent=None):
        ScriptedLoadableModuleWidget.__init__(self, parent)
        self.logic = SpaceMouseBTLogic()
        self._timer = qt.QTimer()
        self._timer.setInterval(self.POLL_INTERVAL_MS)
        self._timer.connect("timeout()", self._applyMotion)

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------

    def setup(self):
        ScriptedLoadableModuleWidget.setup(self)

        # ---- Status row ----
        statusRow = qt.QHBoxLayout()
        self._statusLabel = qt.QLabel("Status: disconnected")
        self._statusLabel.setStyleSheet("font-weight: bold;")
        statusRow.addWidget(self._statusLabel)
        statusRow.addStretch(1)
        self.layout.addLayout(statusRow)

        # ---- Connect / Disconnect ----
        self._connectBtn = qt.QPushButton("Connect SpaceMouse")
        self._connectBtn.setToolTip(
            "Open the first available 3DConnexion device (Bluetooth or USB)."
        )
        self._connectBtn.connect("clicked()", self._onConnectClicked)
        self.layout.addWidget(self._connectBtn)

        # ---- Optional: manual HID path ----
        manualGroup = qt.QGroupBox("Manual device path (optional)")
        manualLayout = qt.QHBoxLayout()
        self._devicePathEdit = qt.QLineEdit()
        self._devicePathEdit.setPlaceholderText(
            "e.g. /dev/hidraw2  — leave blank for auto-detect"
        )
        manualLayout.addWidget(self._devicePathEdit)
        manualGroup.setLayout(manualLayout)
        self.layout.addWidget(manualGroup)

        # ---- Sensitivity ----
        sensGroup = qt.QGroupBox("Sensitivity")
        sensLayout = qt.QFormLayout()

        self._transSens = qt.QSlider(qt.Qt.Horizontal)
        self._transSens.setRange(1, 100)
        self._transSens.setValue(30)
        self._transSens.setToolTip("Scale applied to pan / zoom motion.")
        sensLayout.addRow("Translation:", self._transSens)

        self._rotSens = qt.QSlider(qt.Qt.Horizontal)
        self._rotSens.setRange(1, 100)
        self._rotSens.setValue(30)
        self._rotSens.setToolTip("Scale applied to rotation motion.")
        sensLayout.addRow("Rotation:", self._rotSens)

        sensGroup.setLayout(sensLayout)
        self.layout.addWidget(sensGroup)

        # ---- Invert axes ----
        invertGroup = qt.QGroupBox("Invert axes")
        invertGrid = qt.QGridLayout()
        self._invX   = qt.QCheckBox("Pan X")
        self._invY   = qt.QCheckBox("Pan Y")
        self._invZ   = qt.QCheckBox("Zoom")
        self._invPit = qt.QCheckBox("Pitch")
        self._invRol = qt.QCheckBox("Roll")
        self._invYaw = qt.QCheckBox("Yaw")
        for col, cb in enumerate([self._invX, self._invY, self._invZ]):
            invertGrid.addWidget(cb, 0, col)
        for col, cb in enumerate([self._invPit, self._invRol, self._invYaw]):
            invertGrid.addWidget(cb, 1, col)
        invertGroup.setLayout(invertGrid)
        self.layout.addWidget(invertGroup)

        # ---- Motion mode ----
        modeGroup = qt.QGroupBox("Motion mode")
        modeLayout = qt.QVBoxLayout()
        self._modeFly    = qt.QRadioButton("Fly (move camera toward model)")
        self._modeOrbit  = qt.QRadioButton("Orbit (rotate around focal point)")
        self._modeOrbit.setChecked(True)
        modeLayout.addWidget(self._modeFly)
        modeLayout.addWidget(self._modeOrbit)
        modeGroup.setLayout(modeLayout)
        self.layout.addWidget(modeGroup)

        # ---- 3D view selector ----
        viewGroup = qt.QGroupBox("Target 3D view")
        viewLayout = qt.QHBoxLayout()
        self._viewIndex = qt.QSpinBox()
        self._viewIndex.setRange(0, 7)
        self._viewIndex.setValue(0)
        self._viewIndex.setToolTip(
            "Index of the 3D view widget to control (0 = first / default)."
        )
        viewLayout.addWidget(qt.QLabel("View index:"))
        viewLayout.addWidget(self._viewIndex)
        viewLayout.addStretch(1)
        viewGroup.setLayout(viewLayout)
        self.layout.addWidget(viewGroup)

        # ---- Dependency note ----
        noteLabel = qt.QLabel(
            '<small>Requires <b>pyspacemouse</b> and <b>hidapi</b>. '
            'Run <tt>install.sh</tt> or click Install below.</small>'
        )
        noteLabel.setWordWrap(True)
        self.layout.addWidget(noteLabel)

        self._installBtn = qt.QPushButton("Install Python dependencies")
        self._installBtn.connect("clicked()", self._onInstallClicked)
        self.layout.addWidget(self._installBtn)

        self.layout.addStretch(1)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _onInstallClicked(self):
        slicer.util.pip_install("pyspacemouse hidapi")
        slicer.util.infoDisplay(
            "Installation complete. You may need to restart 3D Slicer.",
            windowTitle="SpaceMouse BT",
        )

    def _onConnectClicked(self):
        if self.logic.isRunning():
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        manual_path = self._devicePathEdit.text.strip() or None
        ok, msg = self.logic.start(device_path=manual_path)
        if ok:
            self._timer.start()
            self._connectBtn.setText("Disconnect SpaceMouse")
            self._statusLabel.setText(f"Status: connected — {msg}")
        else:
            self._statusLabel.setText("Status: connection failed")
            slicer.util.errorDisplay(
                f"Could not connect to SpaceMouse.\n\n{msg}\n\n"
                "Checklist:\n"
                "  1. Pair & connect via Bluetooth (bluetoothctl)\n"
                "  2. Install udev rules (setup/install.sh)\n"
                "  3. Ensure pyspacemouse is installed\n"
                "  4. Try specifying /dev/hidrawN manually",
                windowTitle="SpaceMouse BT",
            )

    def _disconnect(self):
        self._timer.stop()
        self.logic.stop()
        self._connectBtn.setText("Connect SpaceMouse")
        self._statusLabel.setText("Status: disconnected")

    # ------------------------------------------------------------------
    # Motion application (main thread, ~60 fps)
    # ------------------------------------------------------------------

    def _applyMotion(self):
        if not self.logic.isRunning():
            return

        tx, ty, tz, pitch, roll, yaw = self.logic.drainMotion()
        if tx == ty == tz == pitch == roll == yaw == 0.0:
            return

        t_scale = self._transSens.value / 1000.0
        r_scale = self._rotSens.value / 1000.0

        if self._invX.checked:   tx    = -tx
        if self._invY.checked:   ty    = -ty
        if self._invZ.checked:   tz    = -tz
        if self._invPit.checked: pitch = -pitch
        if self._invRol.checked: roll  = -roll
        if self._invYaw.checked: yaw   = -yaw

        view_idx = self._viewIndex.value
        threeDWidget = slicer.app.layoutManager().threeDWidget(view_idx)
        if not threeDWidget:
            return

        renderWindow = threeDWidget.threeDView().renderWindow()
        renderer = renderWindow.GetRenderers().GetFirstRenderer()
        if not renderer:
            return
        camera = renderer.GetActiveCamera()

        # ---- Rotation ----
        if pitch != 0 or roll != 0 or yaw != 0:
            if self._modeOrbit.checked:
                camera.Azimuth(yaw   * r_scale * 60)
                camera.Elevation(pitch * r_scale * 60)
                camera.Roll(roll    * r_scale * 60)
            else:
                # Fly: rotate in place
                camera.Yaw(yaw     * r_scale * 60)
                camera.Pitch(-pitch * r_scale * 60)
                camera.Roll(roll    * r_scale * 60)

        # ---- Translation ----
        if tx != 0 or ty != 0 or tz != 0:
            dist = camera.GetDistance()
            pan_scale = dist * t_scale * 2.0

            if tz != 0:
                # Zoom: dolly along view direction
                factor = 1.0 + tz * t_scale * 3.0
                camera.Dolly(max(factor, 0.01))

            if tx != 0 or ty != 0:
                # Pan: move both position and focal point
                pos   = list(camera.GetPosition())
                focal = list(camera.GetFocalPoint())
                up    = list(camera.GetViewUp())

                view_dir = _normalize([focal[i] - pos[i] for i in range(3)])
                right     = _normalize(_cross(view_dir, up))
                true_up   = _cross(right, view_dir)

                delta = [
                    (-tx * right[i] + ty * true_up[i]) * pan_scale
                    for i in range(3)
                ]
                camera.SetPosition([pos[i]   + delta[i] for i in range(3)])
                camera.SetFocalPoint([focal[i] + delta[i] for i in range(3)])

        renderer.ResetCameraClippingRange()
        renderWindow.Render()

    # ------------------------------------------------------------------

    def cleanup(self):
        self._timer.stop()
        self.logic.stop()


# ---------------------------------------------------------------------------
# Logic (device I/O on a background thread)
# ---------------------------------------------------------------------------

class SpaceMouseBTLogic(ScriptedLoadableModuleLogic):
    """Reads SpaceMouse state on a background thread; accumulates deltas."""

    def __init__(self):
        ScriptedLoadableModuleLogic.__init__(self)
        self._running  = False
        self._thread   = None
        self._lock     = threading.Lock()
        # accumulated: tx, ty, tz, pitch, roll, yaw
        self._motion   = [0.0] * 6
        self._dev_name = ""

    # ------------------------------------------------------------------

    def isRunning(self):
        return self._running

    def start(self, device_path=None):
        """Open device; return (ok: bool, message: str)."""
        try:
            import pyspacemouse
        except ImportError:
            return False, (
                "pyspacemouse is not installed.\n"
                "Click 'Install Python dependencies' or run:\n"
                "  pip install pyspacemouse hidapi"
            )

        try:
            if device_path:
                success = pyspacemouse.open(path=device_path)
            else:
                success = pyspacemouse.open()
        except Exception as exc:
            # pyspacemouse raises on failure; also try BT HID fallback
            bt_result = self._try_bt_fallback(device_path)
            if bt_result:
                return bt_result
            return False, str(exc)

        if not success:
            bt_result = self._try_bt_fallback(device_path)
            if bt_result:
                return bt_result
            return False, (
                "pyspacemouse.open() returned False — device not found.\n"
                "Try specifying the hidraw path manually."
            )

        self._dev_name = "auto-detected"
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_pyspacemouse,
            args=(pyspacemouse,),
            daemon=True,
        )
        self._thread.start()
        return True, f"pyspacemouse ({self._dev_name})"

    def _try_bt_fallback(self, forced_path):
        """
        Scan /dev/hidraw* for a 3DConnexion BT device and open it directly
        via the hid library.  Returns (True, msg) on success, None on failure.
        """
        try:
            import hid
        except ImportError:
            return None

        candidates = []
        if forced_path:
            candidates = [forced_path]
        else:
            import glob as _glob
            candidates = sorted(_glob.glob("/dev/hidraw*"))

        for path in candidates:
            try:
                info = self._hid_info_for_path(path, hid)
                if not info:
                    continue
                if info.get("vendor_id") != _3DX_VENDOR_ID:
                    continue
                pid = info.get("product_id", 0)
                name = _BT_PRODUCT_IDS.get(pid, f"3DConnexion device (0x{pid:04x})")
                dev = hid.Device(path=path.encode())
                dev.nonblocking = False
                self._dev_name = name
                self._running = True
                self._thread = threading.Thread(
                    target=self._poll_hid_raw,
                    args=(dev,),
                    daemon=True,
                )
                self._thread.start()
                return True, f"{name} via raw HID ({path})"
            except Exception:
                continue
        return None

    @staticmethod
    def _hid_info_for_path(path, hid):
        """Return device info dict for a given /dev/hidraw path, or None."""
        try:
            for dev_info in hid.enumerate():
                if dev_info.get("path", b"").decode(errors="replace") == path:
                    return dev_info
        except Exception:
            pass
        return None

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        try:
            import pyspacemouse
            pyspacemouse.close()
        except Exception:
            pass

    def drainMotion(self):
        """Return accumulated motion and reset to zero (thread-safe)."""
        with self._lock:
            m = tuple(self._motion)
            self._motion = [0.0] * 6
        return m

    # ------------------------------------------------------------------
    # Background poll loops
    # ------------------------------------------------------------------

    def _poll_pyspacemouse(self, psm):
        while self._running:
            try:
                state = psm.read()
                if state:
                    with self._lock:
                        self._motion[0] += state.x
                        self._motion[1] += state.y
                        self._motion[2] += state.z
                        self._motion[3] += state.pitch
                        self._motion[4] += state.roll
                        self._motion[5] += state.yaw
            except Exception as exc:
                logger.warning("SpaceMouse read error: %s", exc)
                time.sleep(0.05)

    def _poll_hid_raw(self, dev):
        """
        Parse 3DConnexion HID reports directly.

        Report layout (typical for many models, 7 bytes):
          byte 0: report-id
          bytes 1-2: axis-0 (little-endian signed 16-bit)
          bytes 3-4: axis-1
          bytes 5-6: axis-2

        Report ID 1 → translation (x, y, z)
        Report ID 2 → rotation (pitch, roll, yaw)
        """
        SCALE = 1.0 / 350.0
        while self._running:
            try:
                data = dev.read(7, timeout_ms=50)
                if not data or len(data) < 7:
                    continue
                report_id = data[0]
                a = _s16(data[1], data[2])
                b = _s16(data[3], data[4])
                c = _s16(data[5], data[6])
                with self._lock:
                    if report_id == 1:
                        self._motion[0] += a * SCALE   # tx
                        self._motion[1] += b * SCALE   # ty
                        self._motion[2] += c * SCALE   # tz
                    elif report_id == 2:
                        self._motion[3] += a * SCALE   # pitch
                        self._motion[4] += b * SCALE   # roll
                        self._motion[5] += c * SCALE   # yaw
            except Exception as exc:
                logger.warning("SpaceMouse HID read error: %s", exc)
                time.sleep(0.05)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _s16(lo, hi):
    """Combine two bytes into a signed 16-bit integer (little-endian)."""
    val = lo | (hi << 8)
    return val - 0x10000 if val >= 0x8000 else val


def _normalize(v):
    mag = math.sqrt(sum(x * x for x in v))
    return [x / mag for x in v] if mag > 1e-9 else v


def _cross(a, b):
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
