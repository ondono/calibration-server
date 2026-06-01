from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import zlib
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

import numpy as np

from .cameras import FrameSource

logger = logging.getLogger(__name__)

try:
    from aiohttp import web
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency
    web = None

try:
    from aiortc import VideoStreamTrack
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency
    VideoStreamTrack = object


@dataclass(frozen=True, slots=True)
class WebRtcConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    width: int = 1280
    height: int = 720
    fps: int = 30


class DualCameraWebRtcServer:
    """HTTP signaling server that publishes each camera as a WebRTC video track."""

    def __init__(self, source: FrameSource, config: WebRtcConfig) -> None:
        self._source = source
        self._config = config
        self._pcs: set[Any] = set()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    @property
    def url(self) -> str:
        return f"http://{self._config.host}:{self._config.port}/"

    async def start(self) -> None:
        if web is None:
            raise RuntimeError("aiohttp is required to run the WebRTC signaling server")
        self._source.start()
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/health", self._health)
        app.router.add_post("/offer", self._offer)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._config.host, self._config.port)
        await self._site.start()

    async def stop(self) -> None:
        pcs = list(self._pcs)
        self._pcs.clear()
        await asyncio.gather(*(pc.close() for pc in pcs), return_exceptions=True)
        if self._runner is not None:
            await self._runner.cleanup()
        self._runner = None
        self._site = None
        self._source.stop()

    async def _index(self, _request: web.Request) -> web.Response:
        return web.Response(text=_INDEX_HTML, content_type="text/html")

    async def _health(self, _request: web.Request) -> web.Response:
        frames = self._source.read_frames()
        return web.json_response(
            {
                "server": {
                    "provider": type(self).__name__,
                    "healthy": True,
                    "url": self.url,
                    "peers": len(self._pcs),
                    "streams": list(self._source.stream_names),
                },
                "source": self._source.status(),
                "frames": _frame_diagnostics(frames),
            }
        )

    async def _offer(self, request: web.Request) -> web.Response:
        try:
            from aiortc import RTCPeerConnection, RTCSessionDescription
        except ModuleNotFoundError as exc:
            raise web.HTTPServiceUnavailable(text="aiortc is not installed") from exc

        params = await request.json()
        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
        pc = RTCPeerConnection()
        self._pcs.add(pc)

        @pc.on("connectionstatechange")
        async def _on_connectionstatechange() -> None:
            if pc.connectionState in {"failed", "closed", "disconnected"}:
                self._pcs.discard(pc)
                await pc.close()

        for stream_name in self._source.stream_names:
            pc.addTrack(LatestFrameVideoTrack(self._source, stream_name, fps=self._config.fps))

        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return web.json_response(
            {
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type,
                "streams": list(self._source.stream_names),
            }
        )


class LatestFrameVideoTrack(VideoStreamTrack):
    kind = "video"

    def __init__(self, source: FrameSource, stream_name: str, *, fps: int) -> None:
        super().__init__()
        self._source = source
        self._stream_name = stream_name
        self._fps = max(1, fps)
        self._start = time.monotonic()
        self._sequence = 0

    async def recv(self):
        import av

        await asyncio.sleep(1.0 / self._fps)
        frames = self._source.read_frames()
        frame = frames.get(self._stream_name)
        if frame is None:
            frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        video_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
        self._sequence += 1
        video_frame.pts = int((time.monotonic() - self._start) * 90_000)
        video_frame.time_base = Fraction(1, 90_000)
        return video_frame


def _frame_diagnostics(frames: dict[str, np.ndarray]) -> dict[str, object]:
    diagnostics: dict[str, object] = {
        "streams": {},
    }
    stream_diagnostics = diagnostics["streams"]
    assert isinstance(stream_diagnostics, dict)

    for stream_name, frame in sorted(frames.items()):
        sample = np.ascontiguousarray(frame[::16, ::16, :3])
        stream_diagnostics[stream_name] = {
            "shape": list(frame.shape),
            "mean": round(float(frame.mean()), 3),
            "sample_crc32": f"{zlib.crc32(sample.tobytes()):08x}",
        }

    names = sorted(frames)
    if len(names) >= 2:
        left = frames[names[0]].astype(np.int16)
        right = frames[names[1]].astype(np.int16)
        if left.shape == right.shape:
            diagnostics["mean_abs_diff"] = round(float(np.abs(left - right).mean()), 3)

    return diagnostics


def run_server(source: FrameSource, config: WebRtcConfig, stop_event: threading.Event) -> None:
    async def _main() -> None:
        server = DualCameraWebRtcServer(source, config)
        await server.start()
        logger.info("serving dual CSI WebRTC streams at %s", server.url)
        try:
            while not stop_event.is_set():
                await asyncio.sleep(0.2)
        finally:
            await server.stop()

    asyncio.run(_main())


_INDEX_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Calibration Cameras</title>
  <style>
    body { margin: 0; background: #101316; color: #f5f7fa; font-family: system-ui, sans-serif; }
    header { padding: 12px 16px; background: #1b2229; display: flex; gap: 16px; align-items: center; }
    h1 { font-size: 16px; margin: 0; font-weight: 650; }
    button { font: inherit; padding: 7px 12px; border: 1px solid #52616f; background: #26323d; color: #fff; border-radius: 6px; }
    main { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 8px; padding: 8px; }
    figure { margin: 0; position: relative; background: #000; }
    video { width: 100%; background: #000; aspect-ratio: 16 / 9; }
    figcaption { position: absolute; left: 8px; top: 8px; padding: 3px 6px; background: rgba(0,0,0,.65); font-size: 12px; }
    .status { color: #aab8c5; font-size: 13px; }
  </style>
</head>
<body>
  <header>
    <h1>Calibration Cameras</h1>
    <button id="connect">Connect</button>
    <span id="status" class="status">idle</span>
  </header>
  <main id="videos"></main>
  <script>
    const statusEl = document.getElementById("status");
    const videosEl = document.getElementById("videos");
    const connectButton = document.getElementById("connect");
    let pc;
    let connecting = false;

    connectButton.onclick = async () => {
      if (connecting) return;
      connecting = true;
      connectButton.disabled = true;
      statusEl.textContent = "connecting";
      if (pc) {
        pc.ontrack = null;
        pc.close();
      }
      videosEl.innerHTML = "";
      pc = new RTCPeerConnection();
      const trackSlots = [];
      let nextTrack = 0;
      pc.onconnectionstatechange = () => statusEl.textContent = pc.connectionState;
      pc.ontrack = (event) => {
        const slot = trackSlots[nextTrack++] || makeVideoSlot(`track ${nextTrack}`);
        const video = document.createElement("video");
        video.autoplay = true;
        video.playsInline = true;
        video.muted = true;
        video.srcObject = new MediaStream([event.track]);
        slot.appendChild(video);
      };
      pc.addTransceiver("video", {direction: "recvonly"});
      pc.addTransceiver("video", {direction: "recvonly"});
      try {
        await pc.setLocalDescription(await pc.createOffer());
        const response = await fetch("/offer", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(pc.localDescription),
        });
        if (!response.ok) throw new Error(await response.text());
        const answer = await response.json();
        for (const streamName of answer.streams || ["camera_0", "camera_1"]) {
          trackSlots.push(makeVideoSlot(streamName));
        }
        await pc.setRemoteDescription(answer);
      } catch (error) {
        statusEl.textContent = error.message;
      } finally {
        connecting = false;
        connectButton.disabled = false;
      }
    };

    function makeVideoSlot(label) {
      const figure = document.createElement("figure");
      const caption = document.createElement("figcaption");
      caption.textContent = label;
      figure.appendChild(caption);
      videosEl.appendChild(figure);
      return figure;
    }
  </script>
</body>
</html>
"""
