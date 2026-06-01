from __future__ import annotations

import argparse
import logging
import signal
import threading

from .cameras import CameraConfig, Picamera2DualSource, TestPatternSource
from .webrtc import WebRtcConfig, run_server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish two Raspberry Pi CSI cameras over WebRTC.")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP/WebRTC signaling bind host.")
    parser.add_argument("--port", type=int, default=8080, help="HTTP/WebRTC signaling port.")
    parser.add_argument("--width", type=int, default=1280, help="Capture width.")
    parser.add_argument("--height", type=int, default=720, help="Capture height.")
    parser.add_argument("--fps", type=int, default=30, help="Capture and publish frame rate.")
    parser.add_argument(
        "--camera-id",
        type=int,
        action="append",
        default=None,
        help="Picamera2 camera id. Pass twice to override the default 0, 1 pair.",
    )
    parser.add_argument("--test-pattern", action="store_true", help="Publish synthetic video instead of CSI cameras.")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    camera_ids = tuple(args.camera_id or (0, 1))
    if len(camera_ids) != 2:
        raise SystemExit("--camera-id must be supplied exactly twice when overriding the default pair")

    if args.test_pattern:
        source = TestPatternSource(width=args.width, height=args.height, fps=args.fps)
    else:
        source = Picamera2DualSource(
            CameraConfig(
                camera_ids=(int(camera_ids[0]), int(camera_ids[1])),
                width=args.width,
                height=args.height,
                fps=args.fps,
            )
        )

    stop_event = threading.Event()

    def _request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    run_server(
        source,
        WebRtcConfig(host=args.host, port=args.port, width=args.width, height=args.height, fps=args.fps),
        stop_event,
    )


if __name__ == "__main__":
    main()

