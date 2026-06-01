from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True, slots=True)
class CameraConfig:
    camera_ids: tuple[int, int] = (0, 1)
    width: int = 1280
    height: int = 720
    fps: int = 30


class FrameSource(Protocol):
    stream_names: tuple[str, ...]

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def read_frames(self) -> dict[str, np.ndarray]: ...
    def status(self) -> dict[str, object]: ...


class Picamera2DualSource:
    """Captures the latest frame from two Raspberry Pi CSI cameras."""

    stream_names = ("camera_0", "camera_1")

    def __init__(self, config: CameraConfig) -> None:
        self._config = config
        self._cameras: list[object] = []
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._frames: dict[str, np.ndarray] = {}
        self._errors: dict[str, str] = {}

    def start(self) -> None:
        try:
            from picamera2 import Picamera2
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "picamera2 is not installed. On Raspberry Pi OS install python3-picamera2 "
                "and use a venv created with --system-site-packages."
            ) from exc

        self._stop_event = threading.Event()
        self._frames = {}
        self._errors = {}
        self._cameras = []
        self._threads = []

        for pos, camera_id in enumerate(self._config.camera_ids):
            stream_name = self.stream_names[pos]
            camera = Picamera2(camera_num=camera_id)
            video_config = camera.create_video_configuration(
                main={
                    "size": (self._config.width, self._config.height),
                    "format": "RGB888",
                },
                controls={"FrameRate": float(self._config.fps)},
                buffer_count=4,
            )
            camera.configure(video_config)
            camera.start()
            self._cameras.append(camera)
            thread = threading.Thread(
                target=self._capture_loop,
                args=(stream_name, camera),
                daemon=True,
                name=f"csi-capture-{camera_id}",
            )
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop_event.set()
        for thread in self._threads:
            thread.join(timeout=1.0)
        for camera in self._cameras:
            try:
                camera.stop()
                camera.close()
            except Exception:
                pass
        self._threads = []
        self._cameras = []

    def read_frames(self) -> dict[str, np.ndarray]:
        with self._lock:
            return {name: frame.copy() for name, frame in self._frames.items()}

    def status(self) -> dict[str, object]:
        with self._lock:
            active = sorted(self._frames)
            errors = dict(self._errors)
        return {
            "provider": type(self).__name__,
            "healthy": len(active) == len(self._config.camera_ids) and not errors,
            "camera_ids": list(self._config.camera_ids),
            "active_streams": active,
            "errors": errors,
            "width": self._config.width,
            "height": self._config.height,
            "fps": self._config.fps,
        }

    def _capture_loop(self, stream_name: str, camera: object) -> None:
        while not self._stop_event.is_set():
            try:
                frame = camera.capture_array("main")
            except Exception as exc:
                with self._lock:
                    self._errors[stream_name] = str(exc)
                time.sleep(0.05)
                continue

            if frame is None:
                time.sleep(0.01)
                continue

            prepared = _rgb_to_bgr(frame)
            with self._lock:
                self._frames[stream_name] = prepared
                self._errors.pop(stream_name, None)


class TestPatternSource:
    """Synthetic source for development and automated tests."""

    stream_names = ("camera_0", "camera_1")

    def __init__(self, width: int = 1280, height: int = 720, fps: int = 30) -> None:
        self._width = width
        self._height = height
        self._fps = fps
        self._started_at = time.monotonic()
        self._running = False

    def start(self) -> None:
        self._started_at = time.monotonic()
        self._running = True

    def stop(self) -> None:
        self._running = False

    def read_frames(self) -> dict[str, np.ndarray]:
        elapsed = time.monotonic() - self._started_at
        return {
            "camera_0": self._make_frame(elapsed, left=True),
            "camera_1": self._make_frame(elapsed, left=False),
        }

    def status(self) -> dict[str, object]:
        return {
            "provider": type(self).__name__,
            "healthy": self._running,
            "active_streams": list(self.stream_names) if self._running else [],
            "width": self._width,
            "height": self._height,
            "fps": self._fps,
        }

    def _make_frame(self, elapsed: float, *, left: bool) -> np.ndarray:
        y = np.linspace(0, 255, self._height, dtype=np.uint8)[:, None]
        x = np.linspace(0, 255, self._width, dtype=np.uint8)[None, :]
        phase = int((math.sin(elapsed * 2.0) + 1.0) * 80)
        frame = np.zeros((self._height, self._width, 3), dtype=np.uint8)
        if left:
            frame[:, :, 0] = x
            frame[:, :, 1] = y
            frame[:, :, 2] = phase
        else:
            frame[:, :, 0] = phase
            frame[:, :, 1] = x
            frame[:, :, 2] = y
        _draw_label(frame, "CSI 0" if left else "CSI 1")
        return frame


def _rgb_to_bgr(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 3 and frame.shape[2] >= 3:
        return np.ascontiguousarray(frame[:, :, :3][:, :, ::-1])
    if frame.ndim == 2:
        return np.repeat(frame[:, :, None], 3, axis=2)
    return np.ascontiguousarray(frame)


def _draw_label(frame: np.ndarray, label: str) -> None:
    # Avoid a cv2 dependency in tests; this simple marker is enough for test video.
    frame[24:72, 24:240] = (20, 20, 20)
    stripe = 40 if label.endswith("0") else 180
    frame[34:62, 36:220, 1] = stripe

