#!/usr/bin/env python3
"""
connexion_helper.py — reads SpaceMouse via IOKit HID, bypassing 3DConnexion IPC.

The 3DconnexionClient.framework IPC requires a signed app bundle; plain Python
scripts are rejected.  Instead we use IOHIDManager to read input values directly
from the kernel HID layer.  This works as long as the driver does not hold an
exclusive seizure (kIOHIDOptionsTypeSeizeDevice) on the device.

Emits axis data to stdout as JSON lines: [tx, ty, tz, rx, ry, rz]
Emits diagnostics to stderr (visible when running in Terminal).
"""

import ctypes
import ctypes.util
import json
import sys
import time

_VENDOR_ID = 0x256F          # 3DConnexion
_AXIS_USAGES = {             # Generic Desktop (page 0x01) axis usages
    0x30: 0,  # X  → tx
    0x31: 1,  # Y  → ty
    0x32: 2,  # Z  → tz
    0x33: 3,  # Rx → pitch
    0x34: 4,  # Ry → roll
    0x35: 5,  # Rz → yaw
}


def _cf_str(cf, s: bytes):
    cf.CFStringCreateWithCString.restype  = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    return cf.CFStringCreateWithCString(None, s, 0x0600)   # kCFStringEncodingASCII


def _cf_num_i32(cf, n: int):
    cf.CFNumberCreate.restype  = ctypes.c_void_p
    cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    v = ctypes.c_int32(n)
    return cf.CFNumberCreate(None, 3, ctypes.byref(v))   # kCFNumberSInt32Type = 3


def main():
    cf_path    = ctypes.util.find_library("CoreFoundation")
    iokit_path = ctypes.util.find_library("IOKit")
    cf    = ctypes.CDLL(cf_path)
    iokit = ctypes.CDLL(iokit_path)

    # ── CoreFoundation helpers ─────────────────────────────────────────────
    cf.CFRunLoopGetCurrent.restype   = ctypes.c_void_p
    cf.CFRunLoopRunInMode.argtypes   = [ctypes.c_void_p, ctypes.c_double, ctypes.c_uint8]
    cf.CFRunLoopRunInMode.restype    = ctypes.c_int32
    cf.CFDictionaryCreate.restype    = ctypes.c_void_p
    cf.CFDictionaryCreate.argtypes   = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_long,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]

    # ── IOHIDManager API ──────────────────────────────────────────────────
    iokit.IOHIDManagerCreate.restype  = ctypes.c_void_p
    iokit.IOHIDManagerCreate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    iokit.IOHIDManagerOpen.restype    = ctypes.c_int32
    iokit.IOHIDManagerOpen.argtypes   = [ctypes.c_void_p, ctypes.c_uint32]
    iokit.IOHIDManagerSetDeviceMatching.argtypes   = [ctypes.c_void_p, ctypes.c_void_p]
    iokit.IOHIDManagerRegisterInputValueCallback.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
    ]
    iokit.IOHIDManagerScheduleWithRunLoop.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
    ]
    iokit.IOHIDValueGetIntegerValue.restype  = ctypes.c_long
    iokit.IOHIDValueGetIntegerValue.argtypes = [ctypes.c_void_p]
    iokit.IOHIDValueGetElement.restype       = ctypes.c_void_p
    iokit.IOHIDValueGetElement.argtypes      = [ctypes.c_void_p]
    iokit.IOHIDElementGetUsage.restype       = ctypes.c_uint32
    iokit.IOHIDElementGetUsage.argtypes      = [ctypes.c_void_p]
    iokit.IOHIDElementGetUsagePage.restype   = ctypes.c_uint32
    iokit.IOHIDElementGetUsagePage.argtypes  = [ctypes.c_void_p]

    # ── Device matching dict: VendorID = 0x256F ───────────────────────────
    k = _cf_str(cf, b"VendorID")
    v = _cf_num_i32(cf, _VENDOR_ID)
    keys   = (ctypes.c_void_p * 1)(k)
    vals   = (ctypes.c_void_p * 1)(v)
    match  = cf.CFDictionaryCreate(None, keys, vals, 1, None, None)

    manager = iokit.IOHIDManagerCreate(None, 0)
    iokit.IOHIDManagerSetDeviceMatching(manager, match)

    # ── Axis state + output ───────────────────────────────────────────────
    axis   = [0] * 6
    seen   = set()
    IOHIDValueCallback = ctypes.CFUNCTYPE(
        None, ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p
    )

    def on_value(ctx, result, sender, hid_value):
        elem       = iokit.IOHIDValueGetElement(hid_value)
        usage_page = iokit.IOHIDElementGetUsagePage(elem)
        usage      = iokit.IOHIDElementGetUsage(elem)
        int_val    = iokit.IOHIDValueGetIntegerValue(hid_value)

        idx = _AXIS_USAGES.get(usage) if usage_page == 0x01 else None
        if idx is not None:
            axis[idx] = int_val
            seen.add(idx)
            if len(seen) >= 3:          # emit once we have at least 3 axes
                sys.stdout.write(json.dumps(axis[:]) + "\n")
                sys.stdout.flush()
                seen.clear()
        else:
            # Unknown element — print so we can learn the device's layout
            sys.stderr.write(
                f"  hid: page={usage_page:#04x} usage={usage:#04x} val={int_val}\n"
            )
            sys.stderr.flush()

    cb = IOHIDValueCallback(on_value)
    iokit.IOHIDManagerRegisterInputValueCallback(manager, cb, None)

    # ── Schedule + open ───────────────────────────────────────────────────
    rl      = cf.CFRunLoopGetCurrent()
    rl_mode = ctypes.c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")
    iokit.IOHIDManagerScheduleWithRunLoop(manager, rl, rl_mode)

    ret = iokit.IOHIDManagerOpen(manager, 0)   # 0 = kIOHIDOptionsTypeNone
    if ret == 0:
        sys.stderr.write("IOHIDManagerOpen: success — waiting for input\n")
    elif ret == 0xe00002c5:
        sys.stderr.write(
            "IOHIDManagerOpen: kIOReturnExclusiveAccess — "
            "driver holds exclusive seizure, IOKit bypass not possible\n"
        )
        sys.exit(1)
    else:
        sys.stderr.write(f"IOHIDManagerOpen: error {ret:#010x}\n")
        sys.exit(1)
    sys.stderr.flush()

    sys.stderr.write("Wiggle the SpaceMouse now...\n")
    sys.stderr.flush()

    try:
        while True:
            cf.CFRunLoopRunInMode(rl_mode, 0.05, False)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
