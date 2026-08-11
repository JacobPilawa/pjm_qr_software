from __future__ import annotations

import ctypes
import os
import platform
import threading
from dataclasses import dataclass
from pathlib import Path


DEFAULT_LIBRARY_CANDIDATES = (
    Path("/Applications/NDI Scan Converter.app/Contents/Frameworks/libndi.dylib"),
    Path("/Applications/NDI Video Monitor.app/Contents/Frameworks/libndi.dylib"),
    Path("/usr/local/lib/libndi.dylib"),
)


class _Source(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("url", ctypes.c_char_p)]


class _FindCreate(ctypes.Structure):
    _fields_ = [
        ("show_local_sources", ctypes.c_bool),
        ("groups", ctypes.c_char_p),
        ("extra_ips", ctypes.c_char_p),
    ]


class _ReceiverCreate(ctypes.Structure):
    _fields_ = [
        ("source", _Source),
        ("color_format", ctypes.c_int),
        ("bandwidth", ctypes.c_int),
        ("allow_video_fields", ctypes.c_bool),
        ("receiver_name", ctypes.c_char_p),
    ]


class _VideoFrameV2(ctypes.Structure):
    _fields_ = [
        ("xres", ctypes.c_int),
        ("yres", ctypes.c_int),
        ("fourcc", ctypes.c_int),
        ("frame_rate_n", ctypes.c_int),
        ("frame_rate_d", ctypes.c_int),
        ("picture_aspect_ratio", ctypes.c_float),
        ("frame_format_type", ctypes.c_int),
        ("timecode", ctypes.c_int64),
        ("data", ctypes.POINTER(ctypes.c_uint8)),
        ("line_stride_in_bytes", ctypes.c_int),
        ("metadata", ctypes.c_char_p),
        ("timestamp", ctypes.c_int64),
    ]


FRAME_TYPE_VIDEO = 1
COLOR_FORMAT_BGRX_BGRA = 0
BANDWIDTH_HIGHEST = 100
FOURCC_BGRX = int.from_bytes(b"BGRX", "little")
FOURCC_BGRA = int.from_bytes(b"BGRA", "little")


@dataclass(frozen=True)
class NDISourceInfo:
    id: str
    label: str
    url: str
    kind: str = "ndi"
    active: bool = False


class NDIFinder:
    """Small ctypes wrapper around the installed NDI 6 runtime.

    The NDI library is not copied or redistributed. The QR console loads it from
    the user's existing NDI Tools installation.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._library: ctypes.CDLL | None = None
        self._finder: int | None = None
        self.error = ""
        self._load()

    @property
    def available(self) -> bool:
        return self._library is not None and self._finder is not None

    def sources(self, wait_ms: int = 0) -> list[NDISourceInfo]:
        if not self.available:
            return []
        assert self._library is not None and self._finder is not None
        with self._lock:
            if wait_ms:
                self._library.NDIlib_find_wait_for_sources(self._finder, wait_ms)
            count = ctypes.c_uint32()
            pointer = self._library.NDIlib_find_get_current_sources(self._finder, ctypes.byref(count))
            found: list[NDISourceInfo] = []
            for index in range(count.value):
                source = pointer[index]
                label = source.name.decode("utf-8", errors="replace") if source.name else "Unnamed NDI source"
                url = source.url.decode("utf-8", errors="replace") if source.url else ""
                found.append(NDISourceInfo(id=f"ndi:{label}", label=label, url=url))
            return found

    def close(self) -> None:
        if self._library is not None and self._finder is not None:
            self._library.NDIlib_find_destroy(self._finder)
            self._finder = None

    def receiver(self, source_name: str) -> "NDIReceiver":
        if self._library is None:
            raise RuntimeError(self.error or "NDI is unavailable")
        return NDIReceiver(self._library, source_name)

    def _load(self) -> None:
        if platform.system() != "Darwin":
            self.error = "The first PJM NDI adapter currently targets macOS."
            return
        override = os.environ.get("PJM_QR_NDI_LIB") or os.environ.get("PJM_NDI_LIB")
        candidates = (Path(override),) if override else DEFAULT_LIBRARY_CANDIDATES
        library_path = next((path for path in candidates if path.exists()), None)
        if library_path is None:
            self.error = "NDI Tools runtime was not found."
            return
        try:
            library = ctypes.CDLL(str(library_path))
            library.NDIlib_initialize.restype = ctypes.c_bool
            library.NDIlib_find_create_v2.argtypes = [ctypes.POINTER(_FindCreate)]
            library.NDIlib_find_create_v2.restype = ctypes.c_void_p
            library.NDIlib_find_wait_for_sources.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            library.NDIlib_find_wait_for_sources.restype = ctypes.c_bool
            library.NDIlib_find_get_current_sources.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            library.NDIlib_find_get_current_sources.restype = ctypes.POINTER(_Source)
            library.NDIlib_find_destroy.argtypes = [ctypes.c_void_p]
            library.NDIlib_recv_create_v3.argtypes = [ctypes.POINTER(_ReceiverCreate)]
            library.NDIlib_recv_create_v3.restype = ctypes.c_void_p
            library.NDIlib_recv_capture_v3.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(_VideoFrameV2),
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_uint32,
            ]
            library.NDIlib_recv_capture_v3.restype = ctypes.c_int
            library.NDIlib_recv_free_video_v2.argtypes = [ctypes.c_void_p, ctypes.POINTER(_VideoFrameV2)]
            library.NDIlib_recv_destroy.argtypes = [ctypes.c_void_p]
            if not library.NDIlib_initialize():
                self.error = "NDI runtime initialization failed."
                return
            finder = library.NDIlib_find_create_v2(ctypes.byref(_FindCreate(True, None, None)))
            if not finder:
                self.error = "NDI source finder could not be created."
                return
            self._library = library
            self._finder = finder
        except Exception as error:
            self.error = f"NDI runtime load failed: {error}"


class NDIReceiver:
    def __init__(self, library: ctypes.CDLL, source_name: str):
        self._library = library
        self.source_name = source_name
        self._source_name_bytes = source_name.encode("utf-8")
        self._receiver_name_bytes = b"PJM QR Operator"
        settings = _ReceiverCreate(
            _Source(self._source_name_bytes, None),
            COLOR_FORMAT_BGRX_BGRA,
            BANDWIDTH_HIGHEST,
            False,
            self._receiver_name_bytes,
        )
        self._receiver = library.NDIlib_recv_create_v3(ctypes.byref(settings))
        if not self._receiver:
            raise RuntimeError(f"Could not connect an NDI receiver to {source_name}")

    def capture(self, timeout_ms: int = 1000):
        import numpy as np

        frame = _VideoFrameV2()
        frame_type = self._library.NDIlib_recv_capture_v3(
            self._receiver,
            ctypes.byref(frame),
            None,
            None,
            timeout_ms,
        )
        if frame_type != FRAME_TYPE_VIDEO:
            return None
        try:
            if frame.fourcc not in (FOURCC_BGRX, FOURCC_BGRA):
                raise RuntimeError(f"Unsupported NDI FourCC: {frame.fourcc:#x}")
            if not frame.data or frame.xres <= 0 or frame.yres <= 0 or frame.line_stride_in_bytes <= 0:
                return None
            byte_count = frame.line_stride_in_bytes * frame.yres
            flat = np.ctypeslib.as_array(frame.data, shape=(byte_count,))
            rows = flat.reshape(frame.yres, frame.line_stride_in_bytes)
            bgra = rows[:, :frame.xres * 4].reshape(frame.yres, frame.xres, 4)
            bgr = bgra[:, :, :3].copy()
            fps = frame.frame_rate_n / frame.frame_rate_d if frame.frame_rate_d else 0.0
            return bgr, fps, frame.timestamp
        finally:
            self._library.NDIlib_recv_free_video_v2(self._receiver, ctypes.byref(frame))

    def close(self) -> None:
        if self._receiver:
            self._library.NDIlib_recv_destroy(self._receiver)
            self._receiver = None
