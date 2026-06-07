#!/usr/bin/env python3
"""
connexion_helper.py — SpaceMouseBT event reader (two-phase)

Phase 1 — IOKit HID (pure ctypes, no Python hid module needed)
  Reads input values directly from the kernel HID layer.  For Bluetooth
  devices the driver may intercept at the Bluetooth stack level rather than
  seizing the IOHIDDevice, so IOHIDManagerOpen may succeed even while the
  driver is running.

Phase 2 — 3DconnexionClient.framework IPC
  Falls back to the driver's IPC channel.  Calls SetConnexionClientMask
  after registration (required by some SDK versions to activate delivery).
  Logs every callback invocation unconditionally.
"""

import ctypes
import ctypes.util
import json
import sys
import time


# ─── Phase 1: IOKit HID ────────────────────────────────────────────────────

def try_iokit():
    cf_path    = ctypes.util.find_library("CoreFoundation")
    iokit_path = ctypes.util.find_library("IOKit")
    cf    = ctypes.CDLL(cf_path)
    iokit = ctypes.CDLL(iokit_path)

    # CoreFoundation helpers
    cf.CFRunLoopGetCurrent.restype           = ctypes.c_void_p
    cf.CFRunLoopRunInMode.argtypes           = [ctypes.c_void_p, ctypes.c_double, ctypes.c_uint8]
    cf.CFRunLoopRunInMode.restype            = ctypes.c_int32
    cf.CFDictionaryCreateMutable.restype     = ctypes.c_void_p
    cf.CFDictionaryCreateMutable.argtypes    = [ctypes.c_void_p, ctypes.c_long,
                                                 ctypes.c_void_p, ctypes.c_void_p]
    cf.CFDictionarySetValue.argtypes         = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    cf.CFStringCreateWithCString.restype     = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes    = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFNumberCreate.restype                = ctypes.c_void_p
    cf.CFNumberCreate.argtypes               = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]

    # IOHIDManager API
    iokit.IOHIDManagerCreate.restype                      = ctypes.c_void_p
    iokit.IOHIDManagerCreate.argtypes                     = [ctypes.c_void_p, ctypes.c_uint32]
    iokit.IOHIDManagerOpen.restype                        = ctypes.c_int32
    iokit.IOHIDManagerOpen.argtypes                       = [ctypes.c_void_p, ctypes.c_uint32]
    iokit.IOHIDManagerSetDeviceMatching.argtypes          = [ctypes.c_void_p, ctypes.c_void_p]
    iokit.IOHIDManagerRegisterInputValueCallback.argtypes = [ctypes.c_void_p,
                                                              ctypes.c_void_p,
                                                              ctypes.c_void_p]
    iokit.IOHIDManagerScheduleWithRunLoop.argtypes        = [ctypes.c_void_p,
                                                              ctypes.c_void_p,
                                                              ctypes.c_void_p]
    iokit.IOHIDValueGetIntegerValue.restype               = ctypes.c_long
    iokit.IOHIDValueGetIntegerValue.argtypes              = [ctypes.c_void_p]
    iokit.IOHIDValueGetElement.restype                    = ctypes.c_void_p
    iokit.IOHIDValueGetElement.argtypes                   = [ctypes.c_void_p]
    iokit.IOHIDElementGetUsage.restype                    = ctypes.c_uint32
    iokit.IOHIDElementGetUsage.argtypes                   = [ctypes.c_void_p]
    iokit.IOHIDElementGetUsagePage.restype                = ctypes.c_uint32
    iokit.IOHIDElementGetUsagePage.argtypes               = [ctypes.c_void_p]

    # Matching dict: VendorID = 0x256F (3DConnexion)
    kASCII = 0x0600
    vendor_key = cf.CFStringCreateWithCString(None, b"VendorID", kASCII)
    v_int      = ctypes.c_int32(0x256F)
    vendor_val = cf.CFNumberCreate(None, 3, ctypes.byref(v_int))   # kCFNumberSInt32Type = 3
    match      = cf.CFDictionaryCreateMutable(None, 0, None, None)
    cf.CFDictionarySetValue(match, vendor_key, vendor_val)

    manager = iokit.IOHIDManagerCreate(None, 0)
    iokit.IOHIDManagerSetDeviceMatching(manager, match)

    # Axis state (Generic Desktop page 0x01, usages 0x30–0x35)
    _AXES  = {0x30: 0, 0x31: 1, 0x32: 2, 0x33: 3, 0x34: 4, 0x35: 5}
    axis   = [0] * 6
    SCALE  = 1.0 / 350.0

    IOHIDValueCB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int32,
                                     ctypes.c_void_p, ctypes.c_void_p)

    def on_value(ctx, result, sender, hid_value):
        elem  = iokit.IOHIDValueGetElement(hid_value)
        page  = iokit.IOHIDElementGetUsagePage(elem)
        usage = iokit.IOHIDElementGetUsage(elem)
        val   = iokit.IOHIDValueGetIntegerValue(hid_value)

        idx = _AXES.get(usage) if page == 0x01 else None
        if idx is not None:
            axis[idx] = val
            if any(axis):
                sys.stdout.write(json.dumps([x * SCALE for x in axis]) + "\n")
                sys.stdout.flush()
        else:
            # Print unknown elements — helps map the BT device's actual layout
            sys.stderr.write(f"  HID element: page={page:#04x} usage={usage:#04x} val={val}\n")
            sys.stderr.flush()

    cb = IOHIDValueCB(on_value)
    iokit.IOHIDManagerRegisterInputValueCallback(manager, cb, None)

    rl      = cf.CFRunLoopGetCurrent()
    rl_mode = ctypes.c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")
    iokit.IOHIDManagerScheduleWithRunLoop(manager, rl, rl_mode)

    ret = iokit.IOHIDManagerOpen(manager, 0)   # kIOHIDOptionsTypeNone = 0
    if ret == 0:
        sys.stderr.write("Phase 1 (IOKit): success — wiggle the SpaceMouse\n")
        sys.stderr.flush()
        while True:
            cf.CFRunLoopRunInMode(rl_mode, 0.05, False)
        return True   # not reached; loop exits on KeyboardInterrupt
    elif ret == 0xe00002c5:
        sys.stderr.write("Phase 1 (IOKit): kIOReturnExclusiveAccess — driver holds device\n")
    else:
        sys.stderr.write(f"Phase 1 (IOKit): IOHIDManagerOpen error {ret:#010x}\n")
    sys.stderr.flush()
    return False


# ─── Phase 2: 3DconnexionClient.framework ─────────────────────────────────

def try_framework():
    # 2a. NSApplication — driver requires a GUI process context
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

    # 2c. Callbacks — log every invocation with no filter
    class ConnexionDeviceState(ctypes.Structure):
        _pack_ = 2
        _fields_ = [
            ("version",  ctypes.c_uint16), ("client",   ctypes.c_uint16),
            ("command",  ctypes.c_uint16), ("param",    ctypes.c_int16),
            ("value",    ctypes.c_int32),  ("time",     ctypes.c_uint64),
            ("report",   ctypes.c_uint8 * 8),
            ("buttons8", ctypes.c_uint16),
            ("axis",     ctypes.c_int16 * 6),
            ("address",  ctypes.c_uint16), ("buttons",  ctypes.c_uint32),
        ]

    MsgHandler = ctypes.CFUNCTYPE(None, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p)
    DevHandler  = ctypes.CFUNCTYPE(None, ctypes.c_uint32)

    def on_message(connection, msg_type, msg_arg):
        sys.stderr.write(f"Phase 2: on_message msg_type={msg_type} arg={msg_arg}\n")
        sys.stderr.flush()
        if msg_type == 2 and msg_arg:
            try:
                s = ctypes.cast(msg_arg, ctypes.POINTER(ConnexionDeviceState)).contents
                SCALE = 1.0 / 350.0
                sys.stdout.write(json.dumps([s.axis[i] * SCALE for i in range(6)]) + "\n")
                sys.stdout.flush()
            except Exception as exc:
                sys.stderr.write(f"Phase 2: parse error: {exc}\n")
                sys.stderr.flush()

    msg_cb = MsgHandler(on_message)
    add_cb = DevHandler(lambda pid: (sys.stderr.write(f"Phase 2: device added {pid}\n"), sys.stderr.flush()))
    rem_cb = DevHandler(lambda pid: (sys.stderr.write(f"Phase 2: device removed {pid}\n"), sys.stderr.flush()))

    # 2d. Register
    err = lib.SetConnexionHandlers(msg_cb, add_cb, rem_cb, True)
    sys.stderr.write(f"Phase 2: SetConnexionHandlers → {err}\n")
    sys.stderr.flush()
    if err != 0:
        return

    app_name  = b"\x093D Slicer"
    client_id = lib.RegisterConnexionClient(
        0x534C3344, app_name,
        1,       # kConnexionClientModeTakeOver
        0x3FFF,  # kConnexionMaskAll
    )
    sys.stderr.write(f"Phase 2: RegisterConnexionClient → {client_id}\n")
    sys.stderr.flush()
    if not client_id:
        return

    # 2e. SetConnexionClientMask — required by some driver versions to
    #     activate event delivery after registration
    try:
        lib.SetConnexionClientMask.restype  = None
        lib.SetConnexionClientMask.argtypes = [ctypes.c_uint16, ctypes.c_uint32]
        lib.SetConnexionClientMask(ctypes.c_uint16(client_id), ctypes.c_uint32(0x3FFF))
        sys.stderr.write("Phase 2: SetConnexionClientMask called\n")
    except Exception as exc:
        sys.stderr.write(f"Phase 2: SetConnexionClientMask failed: {exc}\n")
    sys.stderr.flush()

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


# ─── Main ─────────────────────────────────────────────────────────────────

def main():
    sys.stderr.write("=== Phase 1: IOKit HID (direct kernel access) ===\n")
    sys.stderr.flush()
    try:
        if try_iokit():
            return
    except KeyboardInterrupt:
        return

    sys.stderr.write("\n=== Phase 2: 3DconnexionClient.framework ===\n")
    sys.stderr.flush()
    try_framework()


if __name__ == "__main__":
    main()
