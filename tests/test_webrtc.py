from __future__ import annotations

from calibration_server.webrtc import _INDEX_HTML


def test_index_binds_each_video_element_to_one_track() -> None:
    assert "video.srcObject = new MediaStream([event.track]);" in _INDEX_HTML
    assert "video.srcObject = event.streams[0]" not in _INDEX_HTML
