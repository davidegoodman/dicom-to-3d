# SpaceMouseBT — 3DConnexion SpaceMouse for 3D Slicer

A 3D Slicer scripted module that connects a **3DConnexion SpaceMouse Bluetooth** (or USB) device to the 3D Slicer viewport, giving you full 6-DOF navigation: pan, zoom, and rotate in real time.

## Supported devices

| Device | Mode |
|--------|------|
| SpaceMouse Bluetooth | Bluetooth HID |
| SpaceMouse Wireless | Bluetooth HID or USB receiver |
| SpaceMouse Compact BT | Bluetooth HID |
| SpaceMouse USB (any model) | USB HID (auto-detected by pyspacemouse) |

## Requirements

- **3D Slicer** 5.0 or later (Linux, macOS, Windows)
- **Python packages**: `pyspacemouse`, `hidapi` (installed automatically or via the module UI)
- **Linux only**: udev rules so your user can access `/dev/hidraw*`

---

## Quick start (Linux)

### 1. Pair the SpaceMouse via Bluetooth

```bash
bluetoothctl
[bluetooth] scan on
# Wait until your SpaceMouse appears, note its MAC address
[bluetooth] pair   AA:BB:CC:DD:EE:FF
[bluetooth] trust  AA:BB:CC:DD:EE:FF
[bluetooth] connect AA:BB:CC:DD:EE:FF
```

Confirm it's connected:

```bash
ls /dev/hidraw*
# A new hidrawN node should appear
cat /sys/class/hidraw/hidraw*/device/uevent | grep -i name
```

### 2. Install udev rules and Python dependencies

```bash
cd /path/to/dicom-to-3d
chmod +x setup/install.sh
sudo ./setup/install.sh
```

Then **log out and back in** (or `newgrp input`) so the `input` group membership takes effect.

### 3. Load the module in 3D Slicer

1. Open 3D Slicer.
2. Go to **Edit → Application Settings → Modules**.
3. Under **Additional module paths**, add the full path to the `SpaceMouseBT/` folder.
4. Click **OK** and restart Slicer when prompted.
5. Find **SpaceMouse BT** in the module list under the **Navigation** category.

### 4. Connect

1. Open the **SpaceMouse BT** module.
2. Click **Connect SpaceMouse**.
3. The status line should show "connected".

Move the puck — the 3D view responds immediately.

---

## macOS

The 3DConnexion driver installs a kernel extension that exposes devices via a
private HID interface.  `pyspacemouse` auto-detects USB and Bluetooth models on
macOS without any extra steps.  Skip the udev section above; just install the
Python packages and load the module.

```bash
pip install pyspacemouse hidapi
```

---

## Windows

Install the **3DConnexion driver** from the manufacturer's website, then install
the Python packages in Slicer's Python:

```
C:\path\to\Slicer\bin\PythonSlicer.exe -m pip install pyspacemouse hidapi
```

---

## Troubleshooting

### "Could not connect to SpaceMouse"

1. Check that the BT device shows up:  
   `ls -la /dev/hidraw*`

2. Verify permission (should be readable by you):  
   `stat /dev/hidraw2`

3. Check which device is which:  
   `cat /sys/class/hidraw/hidrawN/device/uevent`  
   Look for `HID_NAME=SpaceMouse` or `VENDOR=256f`.

4. Try specifying the path manually in the module's **Manual device path** field:  
   e.g. `/dev/hidraw2`

5. Make sure you're in the `input` group:  
   `groups $USER`  
   If not: `sudo usermod -aG input $USER`, then re-login.

### Device connects but camera doesn't move

- Increase sensitivity sliders.
- Check the **Invert axes** boxes — orientation depends on how you hold the device.
- Try switching between **Orbit** and **Fly** modes.

### `pyspacemouse` doesn't recognise the BT model

The module falls back to a raw HID parser (`hid` library) that works with any
3DConnexion device using the standard report format (report ID 1 = translation,
report ID 2 = rotation, 3 × signed 16-bit per report).  If your device uses a
different report layout, open an issue and include the output of:

```bash
python3 -c "
import hid, time
d = hid.Device(path=b'/dev/hidraw2')
for _ in range(50):
    print(list(d.read(16, 100)))
"
```

---

## Module UI reference

| Control | Description |
|---------|-------------|
| **Connect / Disconnect** | Toggle device connection |
| **Manual device path** | Override auto-detection (e.g. `/dev/hidraw2`) |
| **Translation sensitivity** | Scale applied to pan and zoom |
| **Rotation sensitivity** | Scale applied to pitch / roll / yaw |
| **Invert axes** | Flip any of the 6 DOF axes |
| **Orbit mode** | Rotate / pan around a fixed focal point (default) |
| **Fly mode** | Camera moves as if flying through the scene |
| **View index** | Which 3D view widget to control (0 = primary) |
| **Install Python dependencies** | Runs `pip install pyspacemouse hidapi` |

---

## File layout

```
dicom-to-3d/
├── SpaceMouseBT/
│   └── SpaceMouseBT.py     ← 3D Slicer scripted module
└── setup/
    ├── 99-3dconnexion.rules ← udev rules (Linux)
    └── install.sh           ← installer script
```
