#!/usr/bin/env python3
"""
connexion_helper.py — launched as a subprocess by SpaceMouseBT.py.

Registers with the 3DConnexion driver via 3DconnexionClient.framework,
then streams axis events to stdout as JSON lines:
  [tx, ty, tz, pitch, roll, yaw]   (raw int16 values, range ≈ -500..500)

Running as a separate process gives us our own Mach port, which is what
the driver actually uses to deliver events.  Trying to register from inside
the Slicer/Qt process doesn't work because the driver checks process identity.
"""

import ctypes
import ctypes.util
import json
import sys
import time


def main():
    fw = "/Library/Frameworks/3DconnexionClient.framework/3DconnexionClient"
    try:
        lib = ctypes.CDLL(fw)
    except OSError:
        sys.stderr.write("3DconnexionClient.framework not found\n")
        sys.stderr.flush()
        sys.exit(1)

    cf_name = ctypes.util.find_library("CoreFoundation")
    cf = ctypes.CDLL(cf_name)
    cf.CFRunLoopGetCurrent.restype  = ctypes.c_void_p
    cf.CFRunLoopRunInMode.argtypes  = [ctypes.c_void_p, ctypes.c_double, ctypes.c_uint8]
    cf.CFRunLoopRunInMode.restype   = ctypes.c_int32
    rl_mode = ctypes.c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")

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
                sys.stdout.write(
                    json.dumps([s.axis[i] for i in range(6)]) + "\n"
                )
                sys.stdout.flush()
            except Exception as exc:
                sys.stderr.write(f"callback error: {exc}\n")
                sys.stderr.flush()

    msg_cb = MsgHandler(on_message)
    add_cb = DevHandler(lambda pid: None)
    rem_cb = DevHandler(lambda pid: None)

    # Ensure this thread's CFRunLoop is initialised before registering.
    cf.CFRunLoopGetCurrent()

    err = lib.SetConnexionHandlers(msg_cb, add_cb, rem_cb, False)
    if err != 0:
        sys.stderr.write(f"SetConnexionHandlers error: {err}\n")
        sys.stderr.flush()
        sys.exit(1)

    app_name  = b"\x093D Slicer"   # Pascal string: 1-byte length + text
    client_id = lib.RegisterConnexionClient(
        0x534C3344,             # 'SL3D' — app four-char signature
        app_name,
        kConnexionClientModePlugin,
        kConnexionMaskAll,
    )
    sys.stderr.write(f"registered client_id={client_id}\n")
    sys.stderr.flush()

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
