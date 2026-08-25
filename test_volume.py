"""Offline checks for the volume module.

The COM-backed path (`set_level` / `get_level` / `mute` / ...) requires a real
Windows audio render device and is skipped in sandboxed environments. These
tests cover everything that does not touch hardware:

* the IID/GUID layout matches what Core Audio expects (so a real machine will
  activate the interface);
* the public API is importable and fault-tolerant (None-safe);
* `describe()` and `hud_payload()` produce the right phrasing and shape.
"""

import ctypes
import uuid
import sys


# ---------------------------------------------------------------------------
# 1. GUID layout
# ---------------------------------------------------------------------------

def _make_guid(s):
    """Reproduce the 16-byte in-memory layout of a Windows COM GUID struct.

    A C compiler stores Data1/Data2/Data3 as native-endian integers (little on
    x64) and Data4 as 8 raw bytes. ``uuid.UUID.bytes`` is big-endian, so we
    re-interpret the first three fields as big-endian integers and let ctypes
    store them natively -- matching what Core Audio actually compares.
    """
    u = uuid.UUID(s.strip("{}"))
    b = u.bytes
    data1 = int.from_bytes(b[0:4], "big")
    data2 = int.from_bytes(b[4:6], "big")
    data3 = int.from_bytes(b[6:8], "big")
    data4 = tuple(b[8:16])

    class G(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]
    return G(data1, data2, data3, (ctypes.c_ubyte * 8)(*data4))


def test_guid_layout():
    # The IID_IAudioEndpointVolume the module builds must match the canonical
    # bytes, otherwise CoCreateInstance / Activate would fail even on a real
    # machine.
    expected = _make_guid("{5CDF2C82-841E-4546-9722-0CF74078229A}")
    import volume
    actual = volume._IID_IAudioEndpointVolume
    assert ctypes.sizeof(actual) == ctypes.sizeof(expected), "GUID size mismatch"
    got = bytes(ctypes.string_at(ctypes.addressof(actual), ctypes.sizeof(actual)))
    exp = bytes(ctypes.string_at(ctypes.addressof(expected), ctypes.sizeof(expected)))
    assert got == exp, f"GUID bytes differ: {got.hex()} != {exp.hex()}"
    print("  [PASS] GUID layout matches IAudioEndpointVolume IID")


# ---------------------------------------------------------------------------
# 2. Import hygiene
# ---------------------------------------------------------------------------

def test_imports_clean():
    import volume
    # Nothing at import time: no threads started, no COM calls attempted.
    assert volume._volume is None
    assert volume._com_init is False
    print("  [PASS] nothing happens at import time")


# ---------------------------------------------------------------------------
# 3. describe() phrasing
# ---------------------------------------------------------------------------

def test_describe():
    import volume
    assert "50" in volume.describe("set", 50, False)
    assert volume.describe("mute", 40, True) == "Muted, Sir."
    assert volume.describe("unmute", 40, False) == "Unmuted, Sir. Volume at 40 percent."
    assert volume.describe("toggle", 40, True) == "Muted, Sir."
    assert volume.describe("toggle", 40, False) == "Volume at 40 percent."
    assert volume.describe("step", 65, False) in ("Volume set to 65 percent, Sir.",)
    assert "could not" in volume.describe("set", None, None).lower()
    assert "could not" in volume.describe("error", 50, False).lower()
    print("  [PASS] describe() phrasing")


# ---------------------------------------------------------------------------
# 4. hud_payload shape
# ---------------------------------------------------------------------------

def test_hud_payload():
    import volume
    p = volume.hud_payload("set", 72, False)
    assert p["status"] == "set"
    assert p["ok"] is True
    assert p["error"] is None
    assert p["level"] == 72
    assert p["level_fmt"] == "72%"
    assert p["muted"] is False

    err = volume.hud_payload("set", None, None)
    assert err["status"] == "error"
    assert err["ok"] is False
    assert err["error"] is not None
    print("  [PASS] hud_payload shape")


# ---------------------------------------------------------------------------
# 5. None-safe API (COM unavailable path)
# ---------------------------------------------------------------------------

def test_api_none_safe():
    """When no render device is available every COM call returns None/False
    without raising -- the command loop depends on this."""
    import volume
    # Force-reset the cached state so we test the failure path deterministically
    # even on a machine that does have a device.
    with volume._lock:
        volume._volume = None
        volume._com_init = True   # skip re-init
    try:
        assert volume.get_level() is None
        assert volume.set_level(50) is False
        assert volume.is_muted() is None
        assert volume.mute() is False
        assert volume.unmute() is False
        assert volume.toggle_mute() is None
        assert volume.step(10) is None
        print("  [PASS] API is None/False-safe when COM is unavailable")
    finally:
        with volume._lock:
            volume._volume = None
            volume._com_init = False


# ---------------------------------------------------------------------------
# 6. extract_volume truth table (main.py parser)
# ---------------------------------------------------------------------------

def test_extract_volume():
    """Re-implement the parser expectations here so main.py's extract_volume
    can be checked without wiring the full command loop."""
    import main
    f = main.extract_volume

    # Absolute
    assert f("set volume to 50") == {"action": "set", "level": 50}
    assert f("volume 75") == {"action": "set", "level": 75}
    assert f("volume to 30%") == {"action": "set", "level": 30}
    assert f("Volume 100") == {"action": "set", "level": 100}

    # Relative
    assert f("volume up") == {"action": "step", "delta": 10}
    assert f("volume down") == {"action": "step", "delta": -10}
    assert f("volume up by 5") == {"action": "step", "delta": 5}
    assert f("volume down 15") == {"action": "step", "delta": -15}
    assert f("turn it up") == {"action": "step", "delta": 10}

    # Mute variants
    assert f("mute") == {"action": "mute"}
    assert f("mute the speakers") == {"action": "mute"}
    assert f("unmute") == {"action": "unmute"}
    assert f("toggle mute") == {"action": "toggle"}

    # Negatives
    assert f("what time is it") is None
    assert f("set a timer for 2 minutes") is None
    assert f("check my internet speed") is None
    assert f("hello ron") is None
    assert f("set volume to 200") is None   # out of range
    print("  [PASS] extract_volume truth table")


if __name__ == "__main__":
    tests = [
        test_guid_layout,
        test_imports_clean,
        test_describe,
        test_hud_payload,
        test_api_none_safe,
        test_extract_volume,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {t.__name__}: {e}")
    print()
    print(f"{'All checks passed.' if failed == 0 else f'{failed} check(s) failed.'}")
    sys.exit(1 if failed else 0)
