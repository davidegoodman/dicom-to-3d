#!/usr/bin/env python3
"""
connexion_helper.py — SpaceMouseBT helper (diagnostic + reader)

Tries two paths in order:

  Phase 1 — Direct HID via hidapi
    Works if the 3DConnexion driver does not hold kIOHIDOptionsTypeSeizeDevice
    on the Bluetooth device.  The BT device may be less locked than a USB one.

  Phase 2 — 3DconnexionClient.framework IPC
    Works if the driver dispatches events to registered clients.
    Logs EVERY callback invocation (not just axis events) so we can see
    if the callback fires at all, and with what message type.
"""

import ctypes
import ctypes.util
import json
import sys
import time


# ── Phase 1: Direct HID ───────────────────────────────────────────────────

def _s16(lo, hi):
    v = lo | (hi << 8)
    return v - 0x10000 if v >= 0x8000 else v


def try_hid():
    try:
        import hid
    except ImportError:
        sys.stderr.write("Phase 1: hid module not installed — skipping\n")
        sys.stderr.flush()
        return False

    devices = [d for d in hid.enumerate() if d.get("vendor_id") == 0x256F]
    if not devices:
        sys.stderr.write("Phase 1: no 3DConnexion HID devices visible to hid.enumerate()\n")
        sys.stderr.flush()
        return False

    for d in devices:
        pid  = d.get("product_id", 0)
        name = d.get("product_string", "?")
        path = d.get("path", b"").decode(errors="replace")
        sys.stderr.write(f"Phase 1: found pid={pid:#06x}  '{name}'  {path}\n")
    sys.stderr.flush()

    for d in devices:
        try:
            dev = hid.Device(path=d["path"])
            sys.stderr.write(f"Phase 1: opened '{d.get('product_string','?')}' — wiggle now\n")
            sys.stderr.flush()
            SCALE = 1.0 / 350.0
            while True:
                data = dev.read(16, timeout_ms=100)
                if not data or len(data) < 7:
                    continue
                rid = data[0]
                a = _s16(data[1], data[2])
                b = _s16(data[3], data[4])
                c = _s16(data[5], data[6])
                if rid == 1:
                    axis = [a * SCALE, b * SCALE, c * SCALE, 0.0, 0.0, 0.0]
                elif rid == 2:
                    axis = [0.0, 0.0, 0.0, a * SCALE, b * SCALE, c * SCALE]
                else:
                    sys.stderr.write(f"  raw report: id={rid} data={list(data[:8])}\n")
                    sys.stderr.flush()
                    continue
                if any(axis):
                    sys.stdout.write(json.dumps(axis) + "\n")
                    sys.stdout.flush()
        except Exception as exc:
            sys.stderr.write(f"Phase 1: could not open '{d.get('product_string','?')}': {exc}\n")
            sys.stderr.flush()

    return False   # all open attempts failed


# ── Phase 2: 3DconnexionClient.framework ─────────────────────────────────

def try_framework():
    # 2a. NSApplication (driver checks for a GUI process)
    try:
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        objc.objc_getClass.restype    = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_msgSend.restype     = ctypes.c_void_p
        objc.objc_msgSend.argtypes    = [ctypes.c_void_p, ctypes.c_void_p]
        ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
        NSApp = objc.objc_getClass(b"NSApplication")
        objc.objc_msgSend(NSApp, objc.sel_registerName(b"sharedApplication"))
        sys.stderr.write("Phase 2: NSApplication ok\n")
    except Exception as exc:
        sys.stderr.write(f"Phase 2: NSApplication failed (continuing): {exc}\n")
    sys.stderr.flush()

    # 2b. Load framework
    fw = "/Library/Frameworks/3DconnexionClient.framework/3DconnexionClient"
    try:
        lib = ctypes.CDLL(fw)
    except OSError:
        sys.stderr.write("Phase 2: framework not found\n")
        sys.stderr.flush()
        return

    # 2c. Callback — log EVERYTHING, no message-type filter
    class ConnexionDeviceState(ctypes.Structure):
        _pack_ = 2
        _fields_ = [
            ("version",  ctypes.c_uint16),
            ("client",   ctypes.c_uint16),
            ("command",  ctypes.c_uint16),
            ("param",    ctypes.c_int16),
            ("value",    ctypes.c_int32),
            ("time",     ctypes.c_uint64),
            ("report",   ctypes.c_uint8 * 8),
            ("buttons8", ctypes.c_uint16),
            ("axis",     ctypes.c_int16 * 6),
            ("address",  ctypes.c_uint16),
            ("buttons",  ctypes.c_uint32),
        ]

    MsgHandler = ctypes.CFUNCTYPE(None, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p)
    DevHandler  = ctypes.CFUNCTYPE(None, ctypes.c_uint32)

    def on_message(connection, msg_type, msg_arg):
        # Log unconditionally — we need to know if this ever fires
        sys.stderr.write(f"Phase 2: callback msg_type={msg_type} msg_arg={msg_arg}\n")
        sys.stderr.flush()
        if msg_type == 2 and msg_arg:   # kConnexionCmdHandleAxis = 2
            try:
                s = ctypes.cast(msg_arg, ctypes.POINTER(ConnexionDeviceState)).contents
                SCALE = 1.0 / 350.0
                sys.stdout.write(
                    json.dumps([s.axis[i] * SCALE for i in range(6)]) + "\n"
                )
                sys.stdout.flush()
            except Exception as exc:
                sys.stderr.write(f"Phase 2: parse error: {exc}\n")
                sys.stderr.flush()

    msg_cb = MsgHandler(on_message)
    add_cb = DevHandler(lambda pid: sys.stderr.write(f"Phase 2: device added pid={pid}\n") or sys.stderr.flush())
    rem_cb = DevHandler(lambda pid: sys.stderr.write(f"Phase 2: device removed pid={pid}\n") or sys.stderr.flush())

    # 2d. Register (TakeOver = deliver regardless of focus)
    kTakeOver = 1
    kMaskAll  = 0x3FFF

    err = lib.SetConnexionHandlers(msg_cb, add_cb, rem_cb, True)   # True = framework thread
    sys.stderr.write(f"Phase 2: SetConnexionHandlers → {err}\n")
    sys.stderr.flush()
    if err != 0:
        return

    app_name  = b"\x093D Slicer"
    client_id = lib.RegisterConnexionClient(0x534C3344, app_name, kTakeOver, kMaskAll)
    sys.stderr.write(f"Phase 2: RegisterConnexionClient → {client_id}\n")
    sys.stderr.flush()
    if not client_id:
        return

    sys.stderr.write("Phase 2: registered — wiggle the SpaceMouse...\n")
    sys.stderr.flush()

    try:
        while True:
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        lib.UnregisterConnexionClient(client_id)
        lib.CleanupConnexionHandlers()


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    sys.stderr.write("=== Phase 1: direct HID ===\n")
    sys.stderr.flush()
    if try_hid():
        return

    sys.stderr.write("\n=== Phase 2: 3DconnexionClient.framework ===\n")
    sys.stderr.flush()
    try_framework()


if __name__ == "__main__":
    main()
