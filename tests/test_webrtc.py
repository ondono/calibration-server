from __future__ import annotations

import numpy as np

from calibration_server.webrtc import _INDEX_HTML, _frame_diagnostics


def test_index_binds_each_video_element_to_one_track() -> None:
    assert "video.srcObject = new MediaStream([event.track]);" in _INDEX_HTML
    assert "video.srcObject = event.streams[0]" not in _INDEX_HTML


def test_frame_diagnostics_reports_distinct_streams() -> None:
    frames = {
        "camera_0": np.zeros((32, 32, 3), dtype=np.uint8),
        "camera_1": np.full((32, 32, 3), 20, dtype=np.uint8),
    }

    diagnostics = _frame_diagnostics(frames)

    assert diagnostics["mean_abs_diff"] == 20.0
    streams = diagnostics["streams"]
    assert streams["camera_0"]["sample_crc32"] != streams["camera_1"]["sample_crc32"]
