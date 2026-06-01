from __future__ import annotations

from calibration_server.cameras import TestPatternSource


def test_test_pattern_source_returns_two_bgr_frames() -> None:
    source = TestPatternSource(width=64, height=48, fps=10)
    source.start()

    frames = source.read_frames()

    assert sorted(frames) == ["camera_0", "camera_1"]
    assert frames["camera_0"].shape == (48, 64, 3)
    assert frames["camera_1"].shape == (48, 64, 3)
    assert frames["camera_0"].dtype.name == "uint8"
    assert source.status()["healthy"] is True

