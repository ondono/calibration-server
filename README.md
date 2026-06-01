# Calibration Server

Raspberry Pi CM5 dual-CSI camera streamer for local-network WebRTC viewing.

The runtime captures two CSI camera feeds with Picamera2/libcamera and serves
them through a small aiortc signaling server. Browser clients can open the Pi's
HTTP page and receive both video tracks over WebRTC.

## Raspberry Pi Setup

Install OS camera prerequisites first:

```bash
sudo apt update
sudo apt install -y python3-picamera2 libcamera-apps python3.11-venv
```

Create a virtual environment and install the Python package:

```bash
python3 -m venv .venv --system-site-packages
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

`--system-site-packages` is recommended on Raspberry Pi OS so the venv can see
the distro-provided Picamera2/libcamera bindings.

This project is pinned to `aiortc==1.9.0` and `av==12.3.0` because that PyAV
release provides Linux aarch64 wheels for Python 3.11 and 3.12. Use the
Raspberry Pi OS system Python instead of Python 3.13; otherwise pip may try to
compile PyAV from source and require a matching FFmpeg development toolchain.

If you intentionally need to build PyAV from source, install the FFmpeg headers
and build tools first:

```bash
sudo apt install -y build-essential pkg-config cython3 \
  libavformat-dev libavcodec-dev libavdevice-dev libavutil-dev \
  libavfilter-dev libswscale-dev libswresample-dev
```

Run the server:

```bash
calibration-server --host 0.0.0.0 --port 8080
```

Or run the script directly from a checkout:

```bash
python3 apps/calibration_server.py --host 0.0.0.0 --port 8080
```

Then open `http://<pi-hostname-or-ip>:8080/` from another device on the local
network.

## Development

Use the synthetic test pattern when developing away from Raspberry Pi hardware:

```bash
calibration-server --test-pattern
```

Run tests:

```bash
pytest
```
