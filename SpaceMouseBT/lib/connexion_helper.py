#!/usr/bin/env python3
"""
connexion_helper.py — launched as a subprocess by SpaceMouseBT.py.

Registers with the 3DConnexion driver via 3DconnexionClient.framework,
then streams axis events to stdout as JSON lines:
  [tx, ty, tz, pitch, roll, yaw]   (raw int16 values, range ≈ -500..500)

Key requirements:
  1. Separate process — driver uses Mach IPC and checks process identity.
  2. NSApplication.sharedApplication() — driver refuses non-GUI processes.
  3. NSRunLoop pump — CFRunLoop alone is not sufficient.
"""

import ctypes
import ctypes.util
import json
import sys


def _load_objc():
    lib = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    lib.objc_getClass.restype    = ctypes.c_void_p
    lib.sel_registerName.restype = ctypes.c_void_p
    lib.objc_msgSend.restype     = ctypes.c_void_p
    lib.objc_msgSend.argtypes    = [ctypes.c_void_p, ctypes.c_void_p]
    return lib


def main():
    # ── 1. Init NSApplication (driver checks for a real GUI process) ──────
    try:
        objc = _load_objc()
        ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
        NSApp_cls = objc.objc_getClass(b"NSApplication")
        objc.objc_msgSend(NSApp_cls, objc.sel_registerName(b"sharedApplication"))
        sys.stderr.write("NSApplication: ok\n")
        sys.stderr.flush()
    except Exception as exc:
        sys.stderr.write(f"NSApplication init failed (continuing): {exc}\n")
        sys.stderr.flush()

    # ── 2. Load 3DconnexionClient.framework ───────────────────────────────
    fw = "/Library/Frameworks/3DconnexionClient.framework/3DconnexionClient"
    try:
        lib = ctypes.CDLL(fw)
    except OSError:
        sys.stderr.write("ERROR: 3DconnexionClient.framework not found\n")
        sys.stderr.flush()
        sys.exit(1)

    # ── 3. Build NSRunLoop pump via ObjC ──────────────────────────────────
    try:
        objc = _load_objc()

        # Variant with a double argument for dateWithTimeIntervalSinceNow:
        msgSend_f = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        msgSend_f.objc_msgSend.restype  = ctypes.c_void_p
        msgSend_f.objc_msgSend.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_double
        ]

        NSRunLoop_cls     = objc.objc_getClass(b"NSRunLoop")
        NSDate_cls        = objc.objc_getClass(b"NSDate")
        currentRL_sel     = objc.sel_registerName(b"currentRunLoop")
        runUntil_sel      = objc.sel_registerName(b"runUntilDate:")
        dateInterval_sel  = objc.sel_registerName(b"dateWithTimeIntervalSinceNow:")

        ns_rl = objc.objc_msgSend(NSRunLoop_cls, currentRL_sel)

        def pump(seconds=0.05):
            date = msgSend_f.objc_msgSend(NSDate_cls, dateInterval_sel, seconds)
            objc.objc_msgSend(ns_rl, runUntil_sel, date)

        sys.stderr.write("NSRunLoop: ok\n")
        sys.stderr.flush()

    except Exception as exc:
        sys.stderr.write(f"NSRunLoop setup failed, falling back to CFRunLoop: {exc}\n")
        sys.stderr.flush()

        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        cf.CFRunLoopGetCurrent.restype  = ctypes.c_void_p
        cf.CFRunLoopRunInMode.argtypes  = [ctypes.c_void_p, ctypes.c_double, ctypes.c_uint8]
        cf.CFRunLoopRunInMode.restype   = ctypes.c_int32
        rl_mode = ctypes.c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")
        cf.CFRunLoopGetCurrent()

        def pump(seconds=0.05):
            cf.CFRunLoopRunInMode(rl_mode, seconds, False)

    # ── 4. ConnexionDeviceState layout (SDK header, _pack_=2) ────────────
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
            ("axis",     ctypes.c_int16 * 6),   # tx, ty, tz, pitch, roll, yaw
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

    app_name  = b"\x093D Slicer"   # Pascal string: 1-byte length + ASCII text
    client_id = lib.RegisterConnexionClient(
        0x534C3344, app_name, kConnexionClientModePlugin, kConnexionMaskAll,
    )
    sys.stderr.write(f"RegisterConnexionClient → client_id={client_id}\n")
    sys.stderr.flush()

    # ── 6. Run ────────────────────────────────────────────────────────────
    try:
        while True:
            pump(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        lib.UnregisterConnexionClient(client_id)
        lib.CleanupConnexionHandlers()


if __name__ == "__main__":
    main()
