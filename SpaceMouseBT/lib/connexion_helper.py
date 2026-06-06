#!/usr/bin/env python3
"""
connexion_helper.py — 3DConnexion framework subprocess helper for SpaceMouseBT.

The driver requires NSApplication.sharedApplication() — it checks for a GUI
process.  Once that is called, SetConnexionHandlers + RegisterConnexionClient
succeed.  We then pump CFRunLoop (not NSRunLoop, which segfaults on arm64 due
to objc_msgSend argtypes mismatch) to deliver events to the callback.

Emits axis data to stdout as JSON lines: [tx, ty, tz, rx, ry, rz]
Emits diagnostics to stderr.
"""

import ctypes
import ctypes.util
import json
import sys


def main():
    # ── 1. NSApplication (driver checks for a real GUI process) ──────────
    try:
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        objc.objc_getClass.restype    = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_msgSend.restype     = ctypes.c_void_p
        objc.objc_msgSend.argtypes    = [ctypes.c_void_p, ctypes.c_void_p]
        ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
        NSApp = objc.objc_getClass(b"NSApplication")
        objc.objc_msgSend(NSApp, objc.sel_registerName(b"sharedApplication"))
        sys.stderr.write("NSApplication: ok\n")
    except Exception as exc:
        sys.stderr.write(f"NSApplication failed (continuing): {exc}\n")
    sys.stderr.flush()

    # ── 2. Load 3DconnexionClient.framework ───────────────────────────────
    fw = "/Library/Frameworks/3DconnexionClient.framework/3DconnexionClient"
    try:
        lib = ctypes.CDLL(fw)
    except OSError:
        sys.stderr.write("ERROR: framework not found\n")
        sys.stderr.flush()
        sys.exit(1)

    # ── 3. CFRunLoop (arm64-safe; no NSRunLoop needed) ────────────────────
    cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
    cf.CFRunLoopGetCurrent.restype   = ctypes.c_void_p
    cf.CFRunLoopRunInMode.argtypes   = [ctypes.c_void_p, ctypes.c_double, ctypes.c_uint8]
    cf.CFRunLoopRunInMode.restype    = ctypes.c_int32
    rl_mode = ctypes.c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")
    cf.CFRunLoopGetCurrent()   # initialise the run loop for this thread

    # ── 4. ConnexionDeviceState (SDK header layout, _pack_=2) ─────────────
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
            ("axis",     ctypes.c_int16 * 6),   # tx, ty, tz, rx, ry, rz
            ("address",  ctypes.c_uint16),
            ("buttons",  ctypes.c_uint32),
        ]

    kConnexionCmdHandleAxis    = 2
    kConnexionClientModePlugin = 2
    kConnexionMaskAll          = 0x3FFF

    MsgHandler = ctypes.CFUNCTYPE(None, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p)
    DevHandler  = ctypes.CFUNCTYPE(None, ctypes.c_uint32)

    def on_message(connection, msg_type, msg_arg):
        if msg_type == kConnexionCmdHandleAxis and msg_arg:
            try:
                s = ctypes.cast(
                    msg_arg, ctypes.POINTER(ConnexionDeviceState)
                ).contents
                sys.stdout.write(json.dumps([s.axis[i] for i in range(6)]) + "\n")
                sys.stdout.flush()
            except Exception as exc:
                sys.stderr.write(f"callback error: {exc}\n")
                sys.stderr.flush()

    msg_cb = MsgHandler(on_message)
    add_cb = DevHandler(lambda pid: None)
    rem_cb = DevHandler(lambda pid: None)

    # ── 5. Register with the driver ───────────────────────────────────────
    err = lib.SetConnexionHandlers(msg_cb, add_cb, rem_cb, False)
    sys.stderr.write(f"SetConnexionHandlers → {err}\n")
    sys.stderr.flush()
    if err != 0:
        sys.exit(1)

    app_name  = b"\x093D Slicer"   # Pascal string: 1-byte length + ASCII
    client_id = lib.RegisterConnexionClient(
        0x534C3344, app_name, kConnexionClientModePlugin, kConnexionMaskAll,
    )
    sys.stderr.write(f"RegisterConnexionClient → client_id={client_id}\n")
    sys.stderr.flush()
    if not client_id:
        sys.stderr.write("ERROR: registration failed\n")
        sys.stderr.flush()
        sys.exit(1)

    sys.stderr.write("Registered — wiggle the SpaceMouse...\n")
    sys.stderr.flush()

    # ── 6. Pump CFRunLoop — delivers framework events to on_message ───────
    try:
        while True:
            cf.CFRunLoopRunInMode(rl_mode, 0.05, False)
    except KeyboardInterrupt:
        pass
    finally:
        lib.UnregisterConnexionClient(client_id)
        lib.CleanupConnexionHandlers()


if __name__ == "__main__":
    main()
