import os
import sys
import cv2
import json
import threading
import time
import math
import numpy as np
import sounddevice as sd
from http.server import HTTPServer, BaseHTTPRequestHandler

# ---------------------------
# Global volume variable
# --------------------------- 
_current_volume_pct = 0.0  # 0..100 mapped from mic RMS (dBFS)
_vol_lock = threading.Lock()

def set_volume(v: float):
    global _current_volume_pct
    with _vol_lock:
        _current_volume_pct = max(0.0, min(100.0, float(v)))

def get_volume() -> float:
    with _vol_lock:
        return _current_volume_pct

 
# ---------------------------
# Video overlay (volume bar)
# ---------------------------
def draw_volume_bar(frame: np.ndarray, volume_pct: float):
    """
    Draws a vertical volume bar on the left side of the frame.
    - Background track (semi-transparent)
    - Filled level from bottom up
    - Color shifts from green->yellow->red as level rises
    - Displays numeric percentage
    """
    h, w = frame.shape[:2]

    # Bar geometry
    bar_w = max(18, w // 40)
    margin = max(10, w // 100)
    x0 = margin
    x1 = x0 + bar_w
    y0 = margin
    y1 = h - margin

    # Draw track (rounded-ish rectangle effect)
    overlay = frame.copy()
    cv2.rectangle(overlay, (x0, y0), (x1, y1), (40, 40, 40), thickness=-1)
    # blend overlay for semi-transparency
    cv2.addWeighted(overlay, 0.35, frame, 0.65, 0, frame)

    # Level fill
    vol_clamped = max(0.0, min(100.0, volume_pct))
    level_h = int((vol_clamped / 100.0) * (y1 - y0))
    y_level_top = y1 - level_h

    # Color ramp: 0..60 green, 60..85 yellow, 85..100 red
    if vol_clamped < 60:
        color = (60, 180, 75)   # green-ish (BGR)
    elif vol_clamped < 85:
        color = (30, 200, 200)  # yellow-ish (BGR approximation)
    else:
        color = (50, 50, 230)   # red-ish

    cv2.rectangle(frame, (x0+2, y_level_top), (x1-2, y1-2), color, thickness=-1)

    # Tick marks (25/50/75)
    for pct in (25, 50, 75):
        y_tick = int(y1 - (pct/100.0)*(y1 - y0))
        cv2.line(frame, (x0-4, y_tick), (x1+4, y_tick), (90, 90, 90), 1)

    # Numeric label
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
    smoothing: float = 0.85,  # EMA smoothing for stable bar
    floor_db: float = -60.0,  # map -60 dBFS -> 0%
    ceil_db: float = 0.0      # map  0 dBFS  -> 100%
):
    """
    Starts a background thread that listens to the default microphone
    and updates the shared volume percentage (0..100) continuously.
    """

    def dbfs_from_block(indata: np.ndarray) -> float:
        # indata is float32 clipped to [-1, 1]
        # compute RMS, convert to dBFS
        # add tiny epsilon to avoid log(0)
        eps = 1e-12
        rms = math.sqrt(float(np.mean(np.square(indata), dtype=np.float64)) + eps)
        db = 20.0 * math.log10(max(rms, eps))
        return db

    def map_db_to_pct(db: float) -> float:
        # linear map floor_db..ceil_db => 0..100
        pct = (db - floor_db) / (ceil_db - floor_db) * 100.0
        return max(0.0, min(100.0, pct))

    def thread_target():
        ema = 0.0
        def callback(indata, frames, time_info, status):
            nonlocal ema
            if status:
                # You can log status warnings if desired
                pass
            db = dbfs_from_block(indata)
            pct = map_db_to_pct(db)
            # Exponential moving average for smoother UI
            ema = smoothing * ema + (1.0 - smoothing) * pct
            set_volume(ema)

        # Open default input stream; set channels=1 for simplicity
        with sd.InputStream(samplerate=samplerate,
                            blocksize=blocksize,
                            channels=1,
                            dtype='float32',
                            device=device,
                            callback=callback):
            # Keep the thread alive
            while True:
                time.sleep(0.25)

    t = threading.Thread(target=thread_target, daemon=True)
    t.start()

# ---------------------------
# Image stream server
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

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("Failed to open camera")
            return

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    continue
                draw_volume_bar(frame, get_volume())
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
# Main server (volume + audio + HTML)
# ---------------------------
class MainHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if "/volume" in self.path:
            self.handle_volume()
        elif self.path == "/audio.mp3":
            self.handle_audio()
        elif self.path == "/beep_short.ogg":
            self.handle_ogg()
        else:
            self.handle_html()



    def handle_ogg(self):
        try:
            with open("beep_short.ogg", "rb") as f:
                data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "audio/ogg")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404, "File not found")

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
        html = """
<!DOCTYPE html>
<body>
    <h1>Baby Monitor</h1>
    <img src="http://localhost:8001/stream.mjpg" width="640"/>
    <div id="volume">Volume: 0</div>

<audio id="alarm" src="/beep_short.ogg" preload="auto"></audio>
<button id="playBtn">Play Alarm</button> 
<button id="startBtn">Start Monitoring</button>

<script> 
const THRESHOLD = 60;
let monitoring = false;


function playSound () {
	let ding = new Audio('/beep_short.ogg');
	ding.play();
}

const alarm = document.getElementById('alarm');
document.getElementById('playBtn').addEventListener('click', async () => 
{
    try {
        playSound()
        await alarm.play();
        console.log("Playing alarm!");
    } catch(err) {
        console.error("Playback failed:", err);
    }
});

document.getElementById('startBtn').addEventListener('click', () => 
{
    // User gesture allows audio playback
    playSound()
    alarm.play().then(() => alarm.pause());
    monitoring = true;
    document.getElementById('startBtn').style.display = 'none';
});

setInterval(async () => {
    if (!monitoring) return;
    const r = await fetch('/volume');
    const j = await r.json();
    const vol = j.volume.toFixed(2);
    document.getElementById('volume').innerText = 'Volume: ' + vol;

    if (vol > THRESHOLD) {
        alarm.currentTime = 0;
        playSound()
    
        alarm.play().catch(e => console.log('Alarm play failed:', e));
    } else {
        alarm.pause();
    }
}, 200);
</script>
</body>
</html>

        """
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(html)))
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

# ---------------------------
# Run servers
# ---------------------------
def run_image_server(host='0.0.0.0', port=8001):
    server = HTTPServer((host, port), ImageStreamHandler)
    print(f"Image server running at http://{host}:{port}/stream.mjpg")
    server.serve_forever()

def run_main_server(host='0.0.0.0', port=8080):
    server = HTTPServer((host, port), MainHandler)
    print(f"Main server running at http://{host}:{port}")
    server.serve_forever()

if __name__ == "__main__":
    start_audio_volume_monitor()
    threading.Thread(target=run_image_server, daemon=True).start()
    run_main_server()

