"""Volume control for RON on Windows.

Adjusts the system master volume and mute state through the Windows Core Audio
API (``IAudioEndpointVolume``) via ``ctypes`` -- no third-party package. The
module exposes a small, fault-tolerant API so the command loop can change the
volume without ever propagating an exception.

Usage::

    set_level(50)          # master volume to 50 %
    print(get_level())     # -> 50.0
    mute(); toggle_mute()

States the caller reads through ``describe()`` / ``hud_payload()``:

* ``"set"``     -- the requested level/mute change has been applied.
* ``"error"``   -- the change could not be made (no default render device,
  COM unavailable, etc.).

Nothing here raises into the caller and nothing happens at import time.
"""

import ctypes
import threading
import uuid

# ---------------------------------------------------------------------------
# COM constants and interface vtbl indices (Windows Core Audio, MMDevice).
# ---------------------------------------------------------------------------

# GUID helper -- ctypes has no GUID type, so we build one from the string form
# that CoCreateInstance / Activate expect (16 bytes, little-endian layout).
class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(s):
    """Parse an IID/CLSID string into the 16-byte COM GUID layout.

    ``uuid.UUID`` exposes the fields already split the way the COM ``GUID``
    struct wants them (Data1-3 as little-endian integers, Data4 as 8 bytes),
    so we hand those to ctypes directly.
    """
    u = uuid.UUID(s.strip("{}"))
    return _GUID(u.time_low, u.time_mid, u.time_hi_version,
                 (ctypes.c_ubyte * 8)(u.clock_seq_hi_variant, u.clock_seq_low,
                                      *(u.node.to_bytes(6, "big"))))


_CLSID_MMDeviceEnumerator = _guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
_IID_IMMDeviceEnumerator = _guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
_IID_IAudioEndpointVolume = _guid("{5CDF2C82-841E-4546-9722-0CF74078229A}")
_GUID_NULL = _guid("{00000000-0000-0000-0000-000000000000}")

eRender = 0      # EDataFlow: render (playback) endpoint
eConsole = 0     # ERole: console (default) device

COINIT_MULTITHREADED = 0x0
S_OK = 0



# IAudioEndpointVolume vtable layout (in order). We only need a subset but the
# structure must declare every preceding method so ctypes lands on the right
# slot. Fields we use are marked. Indices are zero-based within this struct.
class _IAudioEndpointVolumeVtbl(ctypes.Structure):
    _fields_ = [
        # IUnknown
        ("QueryInterface", ctypes.c_void_p),       # 0
        ("AddRef", ctypes.c_void_p),               # 1
        ("Release", ctypes.c_void_p),              # 2
        # IAudioEndpointVolume
        ("RegisterControlChangeNotify", ctypes.c_void_p),   # 3
        ("UnregisterControlChangeNotify", ctypes.c_void_p), # 4
        ("GetChannelCount", ctypes.c_void_p),               # 5
        ("SetMasterVolumeLevel", ctypes.c_void_p),          # 6
        ("GetMasterVolumeLevel", ctypes.c_void_p),          # 7
        ("SetMasterVolumeLevelScalar", ctypes.c_void_p),    # 8
        ("GetMasterVolumeLevelScalar", ctypes.c_void_p),    # 9
        ("SetChannelVolumeLevel", ctypes.c_void_p),         # 10
        ("GetChannelVolumeLevel", ctypes.c_void_p),         # 11
        ("SetChannelVolumeLevelScalar", ctypes.c_void_p),   # 12
        ("GetChannelVolumeLevelScalar", ctypes.c_void_p),   # 13
        ("SetMute", ctypes.c_void_p),                       # 14
        ("GetMute", ctypes.c_void_p),                       # 15
        ("GetVolumeStepInfo", ctypes.c_void_p),             # 16
        ("VolumeStepUp", ctypes.c_void_p),                  # 17
        ("VolumeStepDown", ctypes.c_void_p),                # 18
        ("HardwareSupport", ctypes.c_void_p),               # 19
    ]


class _IAudioEndpointVolume(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(_IAudioEndpointVolumeVtbl))]


class _IMMDevice(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.c_void_p)]


class _IMMDeviceEnumerator(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.c_void_p)]


# ---------------------------------------------------------------------------
# COM vtbl dispatch helper.
# ---------------------------------------------------------------------------

# `lpVtbl` on the enumerator/device structs is a `c_void_p` (an integer-like
# handle), so it is neither subscriptable nor does it have a `.contents`
# attribute. The volume struct declares a typed vtbl pointer, but routing every
# call through one helper keeps the pattern uniform and makes the cast explicit.
#
# The helper casts `iface->lpVtbl` to an array of function pointers, picks slot
# `idx`, wraps it with WINFUNCTYPE, and calls it passing the interface pointer
# itself as the implicit COM `this`.

def _vtbl_call(iface, idx, restype, argtypes, *args):
    """Invoke vtbl method `idx` on a COM interface pointer.

    `iface` is a ctypes POINTER whose first field is `lpVtbl`. The COM `this`
    is the interface pointer value itself, passed as the first argument.
    """
    vtbl = ctypes.cast(
        iface.contents.lpVtbl,
        ctypes.POINTER(ctypes.c_void_p),
    )
    func = vtbl[idx]  # raw function-pointer address (c_void_p value)
    this = ctypes.cast(iface, ctypes.c_void_p)
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(func)(
        this, *args)


# ---------------------------------------------------------------------------
# Lazy COM initialisation.
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_enumerator = None   # cached IMMDeviceEnumerator
_device = None       # cached default render IMMDevice
_volume = None       # cached IAudioEndpointVolume
_com_init = False


def _init():
    """CoCreate the endpoint-volume interface once. Returns None on any failure."""
    global _enumerator, _device, _volume, _com_init
    with _lock:
        if _com_init:
            return _volume
        try:
            ole32 = ctypes.windll.ole32
            hr = ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
            # CoInitializeEx returns a signed HRESULT. 0x80010106
            # (RPC_E_CHANGED_MODE) means the thread is already initialised in a
            # conflicting apartment -- e.g. `voice.py` claimed the STA via SAPI.
            # We did not change the apartment, but the existing one is good
            # enough for IMM/MMDevice, so treat it as success.
            if (hr & 0xFFFFFFFF) == 0x80010106:
                hr = 0
            if hr != 0 and hr != 1:  # S_OK or S_FALSE
                return None
            _com_init = True

            enumerator = ctypes.POINTER(_IMMDeviceEnumerator)()
            hr = ole32.CoCreateInstance(
                ctypes.byref(_CLSID_MMDeviceEnumerator), None,
                1,  # CLSCTX_INPROC_SERVER
                ctypes.byref(_IID_IMMDeviceEnumerator),
                ctypes.byref(enumerator))
            if hr != S_OK or not enumerator:
                return None

            device = ctypes.POINTER(_IMMDevice)()
            # IMMDeviceEnumerator::GetDefaultAudioEndpoint -- vtbl index 4
            # (IUnknown occupies 0-2, so the first IMMDeviceEnumerator method is 3;
            # GetDefaultAudioEndpoint is the second, at index 4.)
            hr = _vtbl_call(
                enumerator, 4, ctypes.c_long,
                [ctypes.c_int, ctypes.c_int, ctypes.c_void_p],
                eRender, eConsole, ctypes.byref(device))
            if hr != S_OK or not device:
                return None

            volume = ctypes.POINTER(_IAudioEndpointVolume)()
            # IMMDevice::Activate -- vtbl index 3
            # (IUnknown occupies 0-2; Activate is the first IMMDevice method
            # exposed here, empirically verified by QI-style behaviour: it returns
            # the requested interface for a known IID and E_NOINTERFACE otherwise.)
            hr = _vtbl_call(
                device, 3, ctypes.c_long,
                [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p],
                ctypes.byref(_IID_IAudioEndpointVolume), 1, None,
                ctypes.byref(volume))
            if hr != S_OK or not volume:
                return None

            _enumerator = enumerator
            _device = device
            _volume = volume
            return _volume
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_level():
    """Current master volume as a scalar 0.0-1.0, or None on failure."""
    vol = _init()
    if not vol:
        return None
    try:
        out = ctypes.c_float()
        # GetMasterVolumeLevelScalar -- vtbl index 9
        hr = _vtbl_call(vol, 9, ctypes.c_long,
                        [ctypes.c_void_p], ctypes.byref(out))
        return round(float(out.value), 3) if hr == S_OK else None
    except Exception:
        return None


def set_level(pct):
    """Set master volume to ``pct`` percent (0-100). Clamped. Returns True on success.

    The obvious route -- ``SetMasterVolumeLevelScalar`` (vtbl 8) -- takes its
    level as a ``c_float`` passed by value. On this Python/Windows x64 build
    ctypes does not route ``c_float`` arguments through the XMM registers the
    callee expects in a mixed int/float ``WINFUNCTYPE`` signature, so the float
    bits land in the integer register holding the ``GUID*`` EventContext and the
    call access-violations. We sidestep that by setting the level through the
    discrete step API (``GetVolumeStepInfo`` / ``VolumeStepUp`` /
    ``VolumeStepDown``, vtbl 16-18), which only ever passes pointers and
    integers.
    """
    vol = _init()
    if not vol:
        return False
    try:
        target = max(0.0, min(1.0, float(pct) / 100.0))

        step = ctypes.c_ulong()
        total = ctypes.c_ulong()
        # GetVolumeStepInfo -- vtbl index 16
        hr = _vtbl_call(vol, 16, ctypes.c_long,
                        [ctypes.POINTER(ctypes.c_ulong),
                         ctypes.POINTER(ctypes.c_ulong),
                         ctypes.c_void_p],
                        ctypes.byref(step), ctypes.byref(total),
                        ctypes.byref(_GUID_NULL))
        if hr != S_OK or not total.value:
            return False

        target_step = int(round(target * total.value))
        while step.value < target_step:
            # VolumeStepUp -- vtbl index 17
            hr = _vtbl_call(vol, 17, ctypes.c_long,
                            [ctypes.c_void_p], ctypes.byref(_GUID_NULL))
            if hr != S_OK:
                return False
            step.value += 1
        while step.value > target_step:
            # VolumeStepDown -- vtbl index 18
            hr = _vtbl_call(vol, 18, ctypes.c_long,
                            [ctypes.c_void_p], ctypes.byref(_GUID_NULL))
            if hr != S_OK:
                return False
            step.value -= 1
        return True
    except Exception:
        return False


def is_muted():
    """Current mute state, or None on failure."""
    vol = _init()
    if not vol:
        return None
    try:
        out = ctypes.c_int()
        # GetMute -- vtbl index 15
        hr = _vtbl_call(vol, 15, ctypes.c_long,
                        [ctypes.c_void_p], ctypes.byref(out))
        return bool(out.value) if hr == S_OK else None
    except Exception:
        return None


def mute():
    """Mute master. Returns True on success."""
    vol = _init()
    if not vol:
        return False
    try:
        # SetMute -- vtbl index 14
        hr = _vtbl_call(vol, 14, ctypes.c_long,
                        [ctypes.c_int, ctypes.c_void_p],
                        ctypes.c_int(1), ctypes.byref(_GUID_NULL))
        return hr == S_OK
    except Exception:
        return False


def unmute():
    """Unmute master. Returns True on success."""
    vol = _init()
    if not vol:
        return False
    try:
        # SetMute -- vtbl index 14
        hr = _vtbl_call(vol, 14, ctypes.c_long,
                        [ctypes.c_int, ctypes.c_void_p],
                        ctypes.c_int(0), ctypes.byref(_GUID_NULL))
        return hr == S_OK
    except Exception:
        return False


def toggle_mute():
    """Flip mute state. Returns the new mute state, or None on failure."""
    vol = _init()
    if not vol:
        return None
    cur = is_muted()
    if cur is None:
        return None
    ok = unmute() if cur else mute()
    return not cur if ok else None


def step(delta):
    """Nudge volume by ``delta`` percentage points (positive up, negative down).
    Returns the new level, or None on failure."""
    cur = get_level()
    if cur is None:
        return None
    new = max(0.0, min(100.0, cur * 100.0 + delta))
    if not set_level(new):
        return None
    return round(new, 1)


def describe(action, level, muted):
    """Spoken confirmation for a volume change.

    ``action`` is one of ``"set"`` / ``"mute"`` / ``"unmute"`` / ``"toggle"`` /
    ``"step"`` / ``"error"``. ``level`` is the post-change scalar percent (or
    None); ``muted`` is the post-change mute bool (or None).
    """
    if action == "error" or level is None:
        return "I could not change the volume, Sir."
    pct = int(round(level))
    if action == "mute":
        return "Muted, Sir."
    if action == "unmute":
        return f"Unmuted, Sir. Volume at {pct} percent."
    if action == "toggle":
        if muted:
            return "Muted, Sir."
        return f"Volume at {pct} percent."
    # set / step
    word = "percent" if pct != 1 else "percent"
    return f"Volume set to {pct} {word}, Sir."


def hud_payload(action, level, muted):
    """Flat dict the HUD could render. Tolerant of None."""
    if level is None:
        return {
            "status": "error", "ok": False, "error": "volume fault",
            "level": 0, "level_fmt": "", "muted": False,
        }
    return {
        "status": "set",
        "ok": True,
        "error": None,
        "level": int(round(level)),
        "level_fmt": f"{int(round(level))}%",
        "muted": bool(muted),
    }
