from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
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
    video { width: 100%; background: #000; aspect-ratio: 16 / 9; }
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
    let pc;

    document.getElementById("connect").onclick = async () => {
      if (pc) pc.close();
      videosEl.innerHTML = "";
      pc = new RTCPeerConnection();
      pc.onconnectionstatechange = () => statusEl.textContent = pc.connectionState;
      pc.ontrack = (event) => {
        const video = document.createElement("video");
        video.autoplay = true;
        video.playsInline = true;
        video.muted = true;
        video.srcObject = event.streams[0] || new MediaStream([event.track]);
        videosEl.appendChild(video);
      };
      pc.addTransceiver("video", {direction: "recvonly"});
      pc.addTransceiver("video", {direction: "recvonly"});
      await pc.setLocalDescription(await pc.createOffer());
      const response = await fetch("/offer", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(pc.localDescription),
      });
      if (!response.ok) throw new Error(await response.text());
      await pc.setRemoteDescription(await response.json());
    };
  </script>
</body>
</html>
"""
