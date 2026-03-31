#!/usr/bin/env python3
import os
import re
import cv2
import json
import threading
import time
from datetime import datetime
import math
import numpy as np
import sounddevice as sd
from http.server import HTTPServer, BaseHTTPRequestHandler
import argparse
import hashlib
from typing import Optional

# ---------------------------
# Globals
# ---------------------------
_current_volume_pct = 0.0  # 0..100 mapped from mic RMS (dBFS)                                                                                                              
_vol_lock = threading.Lock()                                                                                                                                                

VIDEO_DEVICE = "/dev/video0"
MAIN_PORT = 8080
IMAGE_PORT = 8081
HOST = "0.0.0.0"

def set_volume(v: float):
    global _current_volume_pct
    with _vol_lock:
        _current_volume_pct = max(0.0, min(100.0, float(v)))

def get_volume() -> float:
    with _vol_lock:
        return _current_volume_pct



# ---------------------------
# Video overlay
# ---------------------------

def get_video_to_usb_suffix_map():
    """
    Parse `v4l2-ctl --list-devices` into:
        {"/dev/video0": "1.1", "/dev/video1": "1.4", ...}
    """
    import re
    import subprocess

    mapping = {}

    try:
        out = subprocess.check_output(
            ["v4l2-ctl", "--list-devices"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"[AUDIO-DEBUG] v4l2-ctl failed: {e}")
        return mapping

    current_suffix = None

    for raw_line in out.splitlines():
        line = raw_line.rstrip()

        # Header example:
        # USB Camera-B4.09.24.1 (usb-0000:01:00.0-1.1):
        m = re.search(r"\(usb-[^)]+-([0-9]+(?:\.[0-9]+)*)\):\s*$", line)
        if m:
            current_suffix = m.group(1)
            print(f"[AUDIO-DEBUG] found USB suffix header: {current_suffix} from line: {line}")
            continue

        # Device example:
        #         /dev/video0
        m = re.match(r"\s*(/dev/video\d+)\s*$", line)
        if m and current_suffix:
            mapping[m.group(1)] = current_suffix
            print(f"[AUDIO-DEBUG] mapped {m.group(1)} -> {current_suffix}")

    print(f"[AUDIO-DEBUG] final video->usb map: {mapping}")
    return mapping

def get_video_usb_port_suffix(video_device: str):
    mapping = get_video_to_usb_suffix_map()
    return mapping.get(video_device)

def get_alsa_cards():
    cards = []
    with open("/proc/asound/cards", "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        m = re.match(r"\s*(\d+)\s+\[(.*?)\s*\]:\s*(.*)", lines[i])
        if m:
            card_num = int(m.group(1))
            block = lines[i]
            if i + 1 < len(lines):
                block += lines[i + 1]
            cards.append((card_num, block))
            i += 2
        else:
            i += 1
    return cards


def find_audio_card_for_video(video_device: str) -> Optional[int]:
    suffix = get_video_usb_port_suffix(video_device)
    print(f"[AUDIO-DEBUG] video_device={video_device} usb_suffix={suffix}")

    if not suffix:
        return None

    for card_num, text in get_alsa_cards():
        print(f"[AUDIO-DEBUG] ALSA card {card_num}: {text.strip()}")
        if f"-{suffix}" in text:
            return card_num

    return None


def find_portaudio_device_for_alsa_card(card_num: int):
    """
    Map USB camera ALSA card number to a unique PortAudio input device index.
    Assumes the USB camera input devices appear in the same relative order as ALSA cards.
    """
    devices = sd.query_devices()

    # Keep only input-capable USB camera audio devices
    pa_matches = []
    for idx, dev in enumerate(devices):
        try:
            name = dev["name"]
            if dev["max_input_channels"] > 0 and "USB Camera-B4.09.24.1" in name:
                pa_matches.append(idx)
                print(f"[AUDIO-DEBUG] PA candidate idx={idx} name={name}")
        except Exception:
            pass

    pa_matches = sorted(pa_matches)

    # Keep only ALSA USB camera cards
    usb_camera_cards = []
    for n, text in get_alsa_cards():
        if "USB Camera-B4.09.24.1" in text:
            usb_camera_cards.append(n)

    usb_camera_cards = sorted(usb_camera_cards)

    print(f"[AUDIO-DEBUG] USB camera ALSA cards: {usb_camera_cards}")
    print(f"[AUDIO-DEBUG] USB camera PortAudio devices: {pa_matches}")

    if not pa_matches:
        return None

    if card_num in usb_camera_cards:
        pos = usb_camera_cards.index(card_num)
        if pos < len(pa_matches):
            return pa_matches[pos]

    return None


def choose_working_samplerate(device, candidates=(48000, 44100, 16000), channels=1) -> Optional[int]:
    for rate in candidates:
        try:
            sd.check_input_settings(device=device, samplerate=rate, channels=channels)
            return rate
        except Exception:
            pass
    return None


def resolve_audio_for_video(video_device: str):
    card_num = find_audio_card_for_video(video_device)
    if card_num is None:
        raise RuntimeError(f"Could not find ALSA card for {video_device}")

    pa_device = find_portaudio_device_for_alsa_card(card_num)
    if pa_device is None:
        raise RuntimeError(
            f"Could not find PortAudio input device for ALSA card {card_num} ({video_device})"
        )

    rate = choose_working_samplerate(pa_device)
    if rate is None:
        raise RuntimeError(
            f"No supported sample rate found for PortAudio device {pa_device} (ALSA card {card_num})"
        )

    return pa_device, rate, card_num


# ---------------------------
# Video overlay
# ---------------------------
def draw_datetime(frame: np.ndarray):
    h, w = frame.shape[:2]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.5
    thickness = 1

    (text_w, text_h), baseline = cv2.getTextSize(timestamp, font, scale, thickness)
    margin = max(10, w // 100)
    x = w - text_w - margin
    y = margin + text_h

    offs = 1
    cv2.putText(frame, timestamp, (x+offs, y+offs), font, scale, (0, 0, 0), thickness, cv2.LINE_AA)
    cv2.putText(frame, timestamp, (x-offs, y-offs), font, scale, (0, 0, 0), thickness, cv2.LINE_AA)
    cv2.putText(frame, timestamp, (x-offs, y),      font, scale, (0, 0, 0), thickness, cv2.LINE_AA)
    cv2.putText(frame, timestamp, (x+offs, y),      font, scale, (0, 0, 0), thickness, cv2.LINE_AA)
    cv2.putText(frame, timestamp, (x, y),           font, scale, (255, 255, 255), thickness, cv2.LINE_AA)

def draw_volume_bar(frame: np.ndarray, volume_pct: float):
    h, w = frame.shape[:2]

    bar_w = max(18, w // 40)
    margin = max(10, w // 100)
    x0 = margin
    x1 = x0 + bar_w
    y0 = margin
    y1 = h - margin

    overlay = frame.copy()
    cv2.rectangle(overlay, (x0, y0), (x1, y1), (40, 40, 40), thickness=-1)
    cv2.addWeighted(overlay, 0.35, frame, 0.65, 0, frame)

    vol_clamped = max(0.0, min(100.0, float(volume_pct)))
    level_h = int((vol_clamped / 100.0) * (y1 - y0))
    y_level_top = y1 - level_h

    if vol_clamped < 60:
        color = (60, 180, 75)
    elif vol_clamped < 85:
        color = (30, 200, 200)
    else:
        color = (50, 50, 230)

    cv2.rectangle(frame, (x0+2, y_level_top), (x1-2, y1-2), color, thickness=-1)

    for pct in (25, 50, 75):
        y_tick = int(y1 - (pct/100.0)*(y1 - y0))
        cv2.line(frame, (x0-4, y_tick), (x1+4, y_tick), (90, 90, 90), 1)

    label = f"{int(round(vol_clamped))}%"
    cv2.putText(frame, label, (x1 + 10, y1 - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (230, 230, 230), 2, cv2.LINE_AA)


# ---------------------------
# Audio monitor (microphone)
# ---------------------------
def start_audio_volume_monitor(
    samplerate: int = 16000,
    blocksize: int = 1024,
    device=None,
    smoothing: float = 0.85,
    floor_db: float = -60.0,
    ceil_db: float = 0.0,
    video_device: Optional[str] = None
):
    def dbfs_from_block(indata):
        rms = np.sqrt(np.mean(np.square(indata), axis=0))[0]
        if rms <= 1e-12:
            return -120.0
        return 20.0 * np.log10(rms)

    def map_db_to_pct(db):
        x = (db - floor_db) / (ceil_db - floor_db)
        x = max(0.0, min(1.0, x))
        return 100.0 * x

    def thread_target():
        ema = 0.0
        local_device = device
        local_samplerate = samplerate
        local_card_num = None

        try:
            if local_device is None:
                if not video_device:
                    raise RuntimeError("No audio device specified and no video_device provided")
                local_device, local_samplerate, local_card_num = resolve_audio_for_video(video_device)

            print(
                f"[AUDIO] video={video_device} "
                f"alsa_card={local_card_num} "
                f"portaudio_device={local_device} "
                f"samplerate={local_samplerate}"
            )
        except Exception as e:
            print(f"[AUDIO] Failed to resolve input device for {video_device}: {e}")
            return

        def callback(indata, frames, time_info, status):
            nonlocal ema
            if status:
                print(f"[AUDIO] status for {video_device}: {status}")
            db = dbfs_from_block(indata)
            pct = map_db_to_pct(db)
            ema = smoothing * ema + (1.0 - smoothing) * pct
            set_volume(ema)

        try:
            with sd.InputStream(
                samplerate=local_samplerate,
                blocksize=blocksize,
                channels=1,
                dtype='float32',
                device=local_device,
                callback=callback
            ):
                while True:
                    time.sleep(0.25)
        except Exception as e:
            print(f"[AUDIO] Failed to open stream for {video_device}: {e}")

    t = threading.Thread(target=thread_target, daemon=True)
    t.start()


# ---------------------------
# Image stream server (MJPEG)
# ---------------------------
class ImageStreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/stream.mjpg":
            self.handle_image_stream()
        else:
            self.send_error(404, "Not Found")

    def handle_image_stream(self):
        self.send_response(200)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        self.end_headers()

        cap = cv2.VideoCapture(VIDEO_DEVICE)
        if not cap.isOpened():
            print(f"Failed to open camera: {VIDEO_DEVICE}")
            return

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    continue

                draw_volume_bar(frame, get_volume())
                draw_datetime(frame)

                ret2, jpeg = cv2.imencode('.jpg', frame)
                if not ret2:
                    continue

                self.wfile.write(b"--frame\r\n")
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(jpeg)))
                self.end_headers()
                self.wfile.write(jpeg.tobytes())
                self.wfile.write(b'\r\n')
                time.sleep(0.03)

        except Exception as e:
            print("Image stream stopped:", e)
        finally:
            cap.release()


# ---------------------------
# Main server (HTML + volume + audio files)
# ---------------------------
class MainHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if "/volume" in self.path:
            self.handle_volume()
        elif self.path == "/audio.mp3":
            self.handle_audio()
        elif "beep_short.ogg" in self.path:
            self.handle_ogg()
        elif "beep_short.mp3" in self.path:
            self.handle_mp3()
        elif "beep_short.wav" in self.path:
            self.handle_wav()
        else:
            self.handle_html()

    def handle_audio_file(self, filename, content_type):
        print(f"[AUDIO] Requested: {filename}")

        if not os.path.exists(filename):
            print(f"[AUDIO] File not found: {filename}")
            self.send_error(404, f"Audio file not found: {filename}")
            return
        if not os.access(filename, os.R_OK):
            print(f"[AUDIO] File not readable: {filename}")
            self.send_error(403, f"Audio file not readable: {filename}")
            return

        try:
            file_stat = os.stat(filename)
            file_size = file_stat.st_size
            if file_size == 0:
                self.send_error(404, f"Audio file is empty: {filename}")
                return

            data = bytearray()
            with open(filename, "rb") as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    data.extend(chunk)

            if len(data) != file_size:
                self.send_error(500, "File read error")
                return

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Range")
            self.send_header("Cache-Control", "public, max-age=3600")

            etag = hashlib.md5(data).hexdigest()[:16]
            self.send_header("ETag", f'"{etag}"')

            self.end_headers()

            chunk_size = 8192
            for i in range(0, len(data), chunk_size):
                try:
                    self.wfile.write(data[i:i+chunk_size])
                except BrokenPipeError:
                    break

        except Exception as e:
            print(f"[AUDIO] Error serving {filename}: {type(e).__name__}: {e}")
            self.send_error(500, "Internal server error")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range")
        self.end_headers()

    def do_HEAD(self):
        if self.path == "/beep_short.ogg":
            self.handle_ogg()
        elif self.path == "/beep_short.mp3":
            self.handle_mp3()
        elif self.path == "/beep_short.wav":
            self.handle_wav()
        else:
            self.send_error(404, "Not Found")

    def handle_ogg(self):
        self.handle_audio_file("beep_short.ogg", "audio/ogg")

    def handle_mp3(self):
        self.handle_audio_file("beep_short.mp3", "audio/mpeg")

    def handle_wav(self):
        self.handle_audio_file("beep_short.wav", "audio/wav")

    def handle_volume(self):
        vol = get_volume()
        payload = json.dumps({"volume": vol})
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload.encode('utf-8'))

    def handle_audio(self):
        try:
            with open("audio.mp3", "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'audio/mpeg')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404, "Audio file not found")

    def handle_html(self):
        # NOTE: we point the <img> to the MJPEG server running on IMAGE_PORT
        html = f"""<!DOCTYPE html>
<html>
<head>
    <style>
        body    {{ font-family: Arial, sans-serif; margin: 20px; }}
        button  {{ padding: 10px 20px; margin: 10px; font-size: 16px; }}
        #status {{ font-weight: bold; margin: 10px 0; }}
        #volume {{ font-size: 18px; margin: 10px 0; }}
        .debug  {{ background: #f0f0f0; padding: 10px; margin: 10px 0; font-family: monospace; }}
    </style>
</head>
<body>
<center>
    <h1>Baby Monitor</h1>
    <div style="font-family: monospace; margin-bottom: 10px;">
      Device: {VIDEO_DEVICE} &nbsp; | &nbsp; Main: {MAIN_PORT} &nbsp; | &nbsp; Stream: {IMAGE_PORT}
    </div>

    <img id="videoStream" width="70%" alt="Loading video stream..."/>

    <div id="volume">Volume: 0</div>
    <div id="status">Status: Ready</div>
    <div id="debug" class="debug">Welcome, Click Start Monitor to begin Volume Monitoring...</div>

    <audio id="alarm" preload="auto">
        <source src="/beep_short.wav" type="audio/wav">
        <source src="/beep_short.mp3" type="audio/mpeg">
        <source src="/beep_short.ogg" type="audio/ogg">
    </audio>

    <br>
    <button id="playBtn">Test Alarm</button>
    <button id="genBeepBtn">Generate Beep</button>
    <button id="startBtn">Start Monitoring</button>
    <button id="testFiles">Test Audio Files</button>

    <script>
        function setupVideoStream() {{
            const currentHost = window.location.hostname;
            const streamUrl = `http://${{currentHost}}:{IMAGE_PORT}/stream.mjpg`;
            const videoImg = document.getElementById('videoStream');
            videoImg.src = streamUrl;
            log(`Video stream URL: ${{streamUrl}}`);
        }}

        const THRESHOLD = 23;
        let monitoring = false;
        let audioContext = null;
        let lastAlarmTime = 0;
        let flashInterval = null;
        const ALARM_COOLDOWN = 2000;

        const alarm = document.getElementById('alarm');
        const status = document.getElementById('status');
        const debug = document.getElementById('debug');

        function startFlashing() {{
            if (flashInterval) return;
            let isRed = false;
            flashInterval = setInterval(() => {{
                document.body.style.backgroundColor = isRed ? 'yellow' : 'red';
                isRed = !isRed;
            }}, 300);
        }}

        function stopFlashing() {{
            if (flashInterval) {{
                clearInterval(flashInterval);
                flashInterval = null;
            }}
            document.body.style.backgroundColor = '';
        }}

        function log(msg) {{
            console.log(msg);
            //debug.innerHTML += msg + '<br>';
        }}

        async function initAudio() {{
            try {{
                if (!audioContext) {{
                    audioContext = new (window.AudioContext || window.webkitAudioContext)();
                    log(`AudioContext created, state: ${{audioContext.state}}`);
                }}
                if (audioContext.state === 'suspended') {{
                    await audioContext.resume();
                    log(`AudioContext resumed, new state: ${{audioContext.state}}`);
                }}
                return true;
            }} catch (err) {{
                log(`AudioContext failed: ${{err.message}}`);
                return false;
            }}
        }}

        function generateBeep() {{
            if (!audioContext) return;
            const oscillator = audioContext.createOscillator();
            const gainNode = audioContext.createGain();
            oscillator.connect(gainNode);
            gainNode.connect(audioContext.destination);
            oscillator.frequency.value = 800;
            gainNode.gain.setValueAtTime(0, audioContext.currentTime);
            gainNode.gain.linearRampToValueAtTime(0.3, audioContext.currentTime + 0.05);
            gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.5);
            oscillator.start(audioContext.currentTime);
            oscillator.stop(audioContext.currentTime + 0.5);
            log('Generated beep sound');
        }}

        async function testAudioFiles() {{
            const files = ['/beep_short.ogg', '/beep_short.mp3', '/beep_short.wav'];
            for (const file of files) {{
                try {{
                    const response = await fetch(file, {{ method: 'GET' }});
                    log(`${{file}} -> ${{response.status}}`);
                }} catch (err) {{
                    log(`${{file}} fetch failed: ${{err.message}}`);
                }}
            }}
        }}

        async function playAlarm() {{
            const now = Date.now();
            if (now - lastAlarmTime < ALARM_COOLDOWN) {{
                log('Alarm on cooldown');
                return;
            }}

            log('Attempting to play alarm...');
            startFlashing();

            try {{
                if (alarm.readyState < 2) {{
                    await new Promise((resolve) => {{
                        const timeout = setTimeout(() => {{
                            log('Audio load timeout, using generated beep');
                            generateBeep();
                            resolve();
                        }}, 2000);

                        const onCanPlay = () => {{
                            clearTimeout(timeout);
                            alarm.removeEventListener('canplay', onCanPlay);
                            resolve();
                        }};
                        alarm.addEventListener('canplay', onCanPlay);
                        alarm.load();
                    }});
                }}

                alarm.currentTime = 0;

                if (alarm.readyState >= 2) {{
                    await alarm.play();
                    status.textContent = 'ALARM PLAYING';
                    lastAlarmTime = now;
                }} else {{
                    throw new Error('Audio still not ready after waiting');
                }}
            }} catch (err) {{
                log(`Alarm play failed: ${{err.name}} - ${{err.message}}`);
                status.textContent = `Audio Error: ${{err.message}}`;
                generateBeep();
            }}
        }}

        document.getElementById('playBtn').addEventListener('click', async () => {{
            await initAudio();
            await playAlarm();
        }});

        document.getElementById('genBeepBtn').addEventListener('click', async () => {{
            await initAudio();
            generateBeep();
        }});

        document.getElementById('testFiles').addEventListener('click', async () => {{
            await testAudioFiles();
        }});

        document.getElementById('startBtn').addEventListener('click', async () => {{
            const audioReady = await initAudio();
            if (!audioReady) {{
                status.textContent = 'Audio initialization failed';
                return;
            }}
            monitoring = true;
            document.getElementById('startBtn').style.display = 'none';
            status.textContent = 'Monitoring active';
            debug.innerHTML = '<br>';
        }});

        alarm.addEventListener('loadstart',  () => log('Audio loading started'));
        alarm.addEventListener('loadeddata', () => log('Audio data loaded'));
        alarm.addEventListener('canplay',    () => log('Audio can play'));
        alarm.addEventListener('error',      (e) => log(`Audio error event`));

        setInterval(async () => {{
            if (!monitoring) return;
            try {{
                const response = await fetch('/volume');
                if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
                const data = await response.json();
                const vol = parseFloat(data.volume);
                document.getElementById('volume').textContent = `Volume: ${{vol.toFixed(2)}}`;

                if (vol > THRESHOLD) {{
                    await playAlarm();
                }} else if (status.textContent.includes('ALARM')) {{
                    status.textContent = 'Monitoring active';
                    stopFlashing();
                }}
            }} catch (error) {{
                status.textContent = `Connection error: ${{error.message}}`;
                stopFlashing();
            }}
        }}, 200);

        window.addEventListener('load', () => {{
            setupVideoStream();
            testAudioFiles();
        }});
    </script>
</center>
</body>
</html>"""

        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(html.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))


# ---------------------------
# Run servers
# ---------------------------
def run_image_server(host: str, port: int):
    server = HTTPServer((host, port), ImageStreamHandler)
    print(f"[{VIDEO_DEVICE}] Image server: http://{host}:{port}/stream.mjpg")
    server.serve_forever()

def run_main_server(host: str, port: int):
    server = HTTPServer((host, port), MainHandler)
    print(f"[{VIDEO_DEVICE}] Main server : http://{host}:{port}")
    server.serve_forever()


def parse_args():
    p = argparse.ArgumentParser(description="Multi-instance baby monitor stream server")
    p.add_argument("video_device", help="Video device path (e.g. /dev/video0)")
    p.add_argument("port", type=int, help="Starting port for MAIN server (MJPEG server uses port+1)")
    p.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    p.add_argument("--audio-device", type=int, default=None,
                   help="Explicit PortAudio input device index (default: auto-resolve from video device)")
    p.add_argument("--samplerate", type=int, default=None,
                   help="Explicit audio sample rate (default: auto-probe from device)")
    p.add_argument("--no-audio", action="store_true", help="Disable microphone monitoring")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()

    VIDEO_DEVICE = args.video_device
    MAIN_PORT = int(args.port)
    IMAGE_PORT = MAIN_PORT + 1
    HOST = args.host

    if not args.no_audio:
        start_audio_volume_monitor(
                                    samplerate=args.samplerate if args.samplerate is not None else 16000,
                                    device=args.audio_device,
                                    video_device=VIDEO_DEVICE,
                                  )

    threading.Thread(target=run_image_server, args=(HOST, IMAGE_PORT), daemon=True).start()
    run_main_server(HOST, MAIN_PORT)

