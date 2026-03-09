# Simple Baby Monitor --- Intended for Raspberry Pi SBC

A lightweight **DIY baby monitor system** designed to run on **Raspberry
Pi 4 or newer**, using Python, OpenCV, and a web browser.

It streams video from one or more webcams, monitors microphone volume,
and triggers an alarm when sound exceeds a threshold.\
A small web portal allows viewing **one or two monitors
simultaneously**.

The system is designed to run **continuously on a Raspberry Pi**,
allowing parents to monitor a baby's room from any device on the local
network.

------------------------------------------------------------------------

# Features

-   📷 **Live video streaming** from `/dev/video*` devices using MJPEG
-   🎤 **Microphone monitoring** with real-time volume analysis
-   🚨 **Audio alarm trigger** when noise exceeds threshold
-   📅 **Timestamp overlay** on video frames
-   📊 **Live volume meter overlay** on the video stream
-   🌐 **Browser-based interface** (no apps required)
-   🖥 **Dual monitor portal** to view multiple cameras
-   🔄 Frame reload and swap functionality
-   🔊 Built-in fallback beep generator if audio playback fails

------------------------------------------------------------------------

# Designed For Raspberry Pi

This project is specifically intended for **Raspberry Pi 4 or newer**.

Typical setup:

    Raspberry Pi 4
    ├── USB Camera 1
    ├── USB Camera 2 (optional)
    ├── USB Microphone
    └── WiFi / Ethernet

The Raspberry Pi acts as the **monitor server**, while phones, tablets,
or laptops connect through a web browser.

Recommended:

-   Raspberry Pi 4 / 5
-   Raspberry Pi OS (64‑bit)
-   USB webcam(s)
-   USB microphone

------------------------------------------------------------------------

# System Architecture

    Camera 0 ── babyMonitor.py ──> Video Stream (MJPEG)
    Camera 1 ── babyMonitor.py ──> Video Stream (MJPEG)

                    ↓
                 portal.py
                    ↓
              Browser Dashboard

Each camera runs its own monitor server, while the **portal aggregates
multiple monitors** into one page.

------------------------------------------------------------------------

# Requirements

Linux system (Raspberry Pi OS recommended).

Install dependencies:

``` bash
pip install opencv-python numpy sounddevice
```

Python 3.9+ recommended.

------------------------------------------------------------------------

# Running a Single Monitor

Start a monitor instance:

``` bash
python3 babyMonitor.py /dev/video0 8080
```

This starts:

    Main UI:
    http://localhost:8080

    Video stream:
    http://localhost:8081/stream.mjpg

------------------------------------------------------------------------

# Running Multiple Cameras

Each monitor instance uses **two ports**:

    main_port
    main_port + 1  (video stream)

Example:

    Camera 0 → ports 8090 / 8091
    Camera 1 → ports 8093 / 8094

Start them like:

``` bash
python3 babyMonitor.py /dev/video0 8090
python3 babyMonitor.py /dev/video1 8093
```

------------------------------------------------------------------------

# Web Portal (Multi Monitor View)

Run the portal server:

``` bash
python3 portal.py --ip 192.168.1.12 -p 8080 -d 8090 -d 8093
```

Options:

    --ip            Device IP hosting monitors
    -p              Portal port
    -d              Monitor ports (repeatable)

Example portal URL:

    http://192.168.1.12:8080

Portal features:

-   Dual monitor layout
-   Swap camera views
-   Reload streams

------------------------------------------------------------------------

# Automatic Startup Script

Example launcher:

``` bash
./startup.sh
```

Example:

``` bash
python3 babyMonitor.py /dev/video0 8090 &
python3 babyMonitor.py /dev/video1 8093

python3 portal.py --ip 192.168.1.12 -p 8080 -d 8090 -d 8093
```

This launches:

    2 camera monitors
    1 web portal

------------------------------------------------------------------------

# Alarm System

The monitor continuously measures microphone loudness.

If sound exceeds the threshold:

    Volume > 30%

Then:

-   Alarm sound plays
-   Screen flashes red/yellow
-   Status updates in the UI

If the audio file cannot be played, a **generated WebAudio beep** is
used instead.

------------------------------------------------------------------------

# Audio Files

Optional alarm files supported:

    beep_short.wav
    beep_short.mp3
    beep_short.ogg

If none exist, the system generates a beep using the browser.

------------------------------------------------------------------------

# Camera Overlay

Each video frame includes:

-   📅 timestamp
-   📊 vertical volume bar
-   🎚 sound level percentage

This allows quick visual monitoring.

------------------------------------------------------------------------

# Example Setup

    Baby Monitor Raspberry Pi
    │
    ├── Camera 1 (crib view)
    ├── Camera 2 (room view)
    ├── USB microphone
    │
    └── Parents connect via:
         • Phone
         • Tablet
         • Laptop
         • Smart TV browser

------------------------------------------------------------------------

# Security Notes

This project is intentionally simple and designed for **local network
use**.

If exposing outside your network, consider:

-   VPN access
-   reverse proxy
-   authentication

------------------------------------------------------------------------

# Possible Improvements

Ideas for future work:

-   motion detection
-   cry detection using ML
-   event recording
-   night vision / IR camera support
-   WebRTC streaming instead of MJPEG
-   authentication
-   mobile UI improvements

------------------------------------------------------------------------

# License

GPL3 License
