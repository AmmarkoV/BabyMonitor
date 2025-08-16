import os
import sys
import cv2
import json
import threading
import time
from datetime import datetime
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

def draw_datetime(frame: np.ndarray):
    """
    Renders the current date and time (up to seconds)
    in the top-right corner of the image.
    """
    h, w = frame.shape[:2]

    # Get formatted date/time string
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Choose font and scale
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.5
    thickness = 1

    # Determine text size
    (text_w, text_h), baseline = cv2.getTextSize(timestamp, font, scale, thickness)

    # Position: top-right with small margin
    margin = max(10, w // 100)
    x = w - text_w - margin
    y = margin + text_h

    # Draw semi-transparent background for readability
    #overlay = frame.copy()
    #cv2.rectangle(overlay, (x - 5, y - text_h - 5), (x + text_w + 5, y + baseline + 5), (0, 0, 0), thickness=-1)
    #cv2.addWeighted(overlay, 0.35, frame, 0.65, 0, frame)

    # Draw text
    offs=1
    cv2.putText(frame, timestamp, (x+offs, y+offs), font, scale, (0, 0, 0), thickness, cv2.LINE_AA)
    cv2.putText(frame, timestamp, (x-offs, y-offs), font, scale, (0, 0, 0), thickness, cv2.LINE_AA)
    cv2.putText(frame, timestamp, (x-offs, y), font, scale, (0, 0, 0), thickness, cv2.LINE_AA)
    cv2.putText(frame, timestamp, (x+offs, y), font, scale, (0, 0, 0), thickness, cv2.LINE_AA)
    cv2.putText(frame, timestamp, (x, y), font, scale, (255, 255, 255), thickness, cv2.LINE_AA)

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

    def handle_image_stream(self, width=640, height=480, framerate=10):
        self.send_response(200)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        self.end_headers()

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("Failed to open camera")
            return


        # Apply requested width & height
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        # Compute frame interval from framerate
        frame_interval = 1.0 / framerate


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

                time.sleep(frame_interval)
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
        elif "beep_short.ogg" in self.path :
            self.handle_ogg()
        elif "beep_short.mp3" in self.path:
            self.handle_mp3()
        elif "beep_short.wav" in self.path:
            self.handle_wav()
        else:
            self.handle_html()
    #----------------------------------------------------------
 def handle_audio_file(self, filename, content_type):
    """Generic handler for audio files with robust error handling"""
    print(f"[AUDIO] Requested: {filename}")
    
    # Check if file exists
    if not os.path.exists(filename):
        print(f"[AUDIO] File not found: {filename}")
        self.send_error(404, f"Audio file not found: {filename}")
        return
    
    # Check if file is readable
    if not os.access(filename, os.R_OK):
        print(f"[AUDIO] File not readable: {filename}")
        self.send_error(403, f"Audio file not readable: {filename}")
        return
    
    try:
        # Get file stats first
        file_stat = os.stat(filename)
        file_size = file_stat.st_size
        print(f"[AUDIO] File size: {file_size} bytes")
        
        # Check for empty file
        if file_size == 0:
            print(f"[AUDIO] Empty file: {filename}")
            self.send_error(404, f"Audio file is empty: {filename}")
            return
        
        # Read file in chunks for large files (though audio files are usually small)
        data = bytearray()
        with open(filename, "rb") as f:
            while True:
                chunk = f.read(8192)  # 8KB chunks
                if not chunk:
                    break
                data.extend(chunk)
        
        # Verify we read the expected amount
        if len(data) != file_size:
            print(f"[AUDIO] Size mismatch: expected {file_size}, got {len(data)}")
            self.send_error(500, "File read error")
            return
        
        print(f"[AUDIO] Successfully read {len(data)} bytes")
        
        # Send response
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range")
        self.send_header("Cache-Control", "public, max-age=3600")
        
        # Add ETag for caching
        import hashlib
        etag = hashlib.md5(data).hexdigest()[:16]
        self.send_header("ETag", f'"{etag}"')
        
        self.end_headers()
        
        # Write data to client
        bytes_written = 0
        chunk_size = 8192
        for i in range(0, len(data), chunk_size):
            chunk = data[i:i+chunk_size]
            try:
                self.wfile.write(chunk)
                bytes_written += len(chunk)
            except BrokenPipeError:
                print(f"[AUDIO] Client disconnected after {bytes_written} bytes")
                break
            except Exception as e:
                print(f"[AUDIO] Write error after {bytes_written} bytes: {e}")
                break
        
        print(f"[AUDIO] Sent {bytes_written}/{len(data)} bytes successfully")
        
    except FileNotFoundError:
        print(f"[AUDIO] File disappeared: {filename}")
        self.send_error(404, "File not found")
    except PermissionError:
        print(f"[AUDIO] Permission denied: {filename}")
        self.send_error(403, "Permission denied")
    except IOError as e:
        print(f"[AUDIO] IO Error reading {filename}: {e}")
        self.send_error(500, "File read error")
    except Exception as e:
        print(f"[AUDIO] Unexpected error serving {filename}: {type(e).__name__}: {e}")
        self.send_error(500, "Internal server error")

 # Handle OPTIONS requests for CORS preflight
 def do_OPTIONS(self):
    self.send_response(200)
    self.send_header("Access-Control-Allow-Origin", "*")
    self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
    self.send_header("Access-Control-Allow-Headers", "Range")
    self.end_headers()

 # Updated simplified handlers
 def handle_ogg(self):
    self.handle_audio_file("beep_short.ogg", "audio/ogg")

 def handle_mp3(self):
    self.handle_audio_file("beep_short.mp3", "audio/mpeg")

 def handle_wav(self):
    self.handle_audio_file("beep_short.wav", "audio/wav")

 # Add HEAD method support for audio files
 def do_HEAD(self):
    if self.path == "/beep_short.ogg":
        self.handle_ogg()
    elif self.path == "/beep_short.mp3":
        self.handle_mp3()
    elif self.path == "/beep_short.wav":
        self.handle_wav()
    else:
        self.send_error(404, "Not Found")
    #----------------------------------------------------------
 def handle_volume(self):
        vol = get_volume()
        payload = json.dumps({"volume": vol})
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload.encode('utf-8'))

#----------------------------------------------------------
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
<html>
<head>
    <style>
        body    { font-family: Arial, sans-serif; margin: 20px; }
        button  { padding: 10px 20px; margin: 10px; font-size: 16px; }
        #status { font-weight: bold; margin: 10px 0; }
        #volume { font-size: 18px; margin: 10px 0; }
        .debug  { background: #f0f0f0; padding: 10px; margin: 10px 0; font-family: monospace; }
    </style>
</head>
<body>
    <h1>Baby Monitor</h1> 
    <img id="videoStream" width="640" alt="Loading video stream..."/>


    <div>
        <label for="thresholdSlider">
            Threshold: <span id="thresholdValue">50</span>
        </label>
        <input type="range" id="thresholdSlider" min="0" max="100" value="50">
    </div>

    <div id="volume">Volume: 0</div>
    <div id="status">Status: Ready</div>
    <div id="debug" class="debug">Welcome, Click Start Monitor to begin Volume Monitoring...</div>
     
    <!-- Multiple audio elements for better browser support -->
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

        // Get current page's IP and construct stream URL
        function setupVideoStream() {
            const currentHost = window.location.hostname;
            const currentPort = window.location.port;
            const streamUrl = `http://${currentHost}:8001/stream.mjpg`;
            
            const videoImg = document.getElementById('videoStream');
            videoImg.src = streamUrl;
            
            log(`Video stream URL: ${streamUrl}`);
            log(`Current page: ${currentHost}:${currentPort}`);
        }


        let monitoring = false;
        let audioContext = null;
        let lastAlarmTime = 0;
        let flashInterval = null;
        const ALARM_COOLDOWN = 2000;
        
        const alarm = document.getElementById('alarm');
        const status = document.getElementById('status');
        const debug = document.getElementById('debug');

    let THRESHOLD = 30; // default
    const thresholdSlider = document.getElementById('thresholdSlider');
    const thresholdValueDisplay = document.getElementById('thresholdValue');

    thresholdSlider.addEventListener('input', () => {
        THRESHOLD = parseInt(thresholdSlider.value, 10);
        thresholdValueDisplay.textContent = THRESHOLD;
    });


    function startFlashing() {
        if (flashInterval) return; // already flashing

        let isRed = false;
        flashInterval = setInterval(() => {
            document.body.style.backgroundColor = isRed ? 'yellow' : 'red';
            isRed = !isRed;
        }, 300);
    }

    function stopFlashing() {
        if (flashInterval) {
            clearInterval(flashInterval);
            flashInterval = null;
        }
        document.body.style.backgroundColor = ''; // reset
    }


        function log(msg) {
            console.log(msg);
            //debug.innerHTML += msg + '<br>';
        }
        
        
        // Initialize audio context
        async function initAudio() {
            try {
                if (!audioContext) {
                    audioContext = new (window.AudioContext || window.webkitAudioContext)();
                    log(`AudioContext created, state: ${audioContext.state}`);
                }
                if (audioContext.state === 'suspended') {
                    await audioContext.resume();
                    log(`AudioContext resumed, new state: ${audioContext.state}`);
                }
                return true;
            } catch (err) {
                log(`AudioContext failed: ${err.message}`);
                return false;
            }
        }
        
        // Generate a beep sound programmatically
        function generateBeep() {
            if (!audioContext) return;
            
            const oscillator = audioContext.createOscillator();
            const gainNode = audioContext.createGain();
            
            oscillator.connect(gainNode);
            gainNode.connect(audioContext.destination);
            
            oscillator.frequency.value = 800; // 800 Hz
            gainNode.gain.setValueAtTime(0, audioContext.currentTime);
            gainNode.gain.linearRampToValueAtTime(0.3, audioContext.currentTime + 0.05);
            gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.5);
            
            oscillator.start(audioContext.currentTime);
            oscillator.stop(audioContext.currentTime + 0.5);
            
            log('Generated beep sound');
        }
        
        // Test if audio files exist
        async function testAudioFiles() {
            const files = ['/beep_short.ogg', '/beep_short.mp3', '/beep_short.wav'];
            
            for (const file of files) {
                try {
                    const response = await fetch(file, { method: 'GET' });
                    if (response.ok) {
                        log(`${file} exists (${response.status})`);
                    } else {
                        log(`${file} not found (${response.status})`);
                    }
                } catch (err) {
                    log(`${file} fetch failed: ${err.message}`);
                }
            }
        }
        
        // Play alarm with detailed logging
        async function playAlarm() {
            const now = Date.now();
            if (now - lastAlarmTime < ALARM_COOLDOWN) {
                log('Alarm on cooldown');
                return;
            }
            
            log('Attempting to play alarm...');
            startFlashing(); //start background flashing
            
            try {
                // Wait for audio to be ready
                if (alarm.readyState < 2) { // HAVE_CURRENT_DATA = 2
                    log(`Audio not ready (readyState: ${alarm.readyState}), waiting...`);
                    
                    // Wait for canplay event or timeout
                    await new Promise((resolve, reject) => {
                        const timeout = setTimeout(() => {
                            log('Audio load timeout, using generated beep');
                            generateBeep();
                            resolve();
                        }, 2000);
                        
                        const onCanPlay = () => {
                            clearTimeout(timeout);
                            alarm.removeEventListener('canplay', onCanPlay);
                            log(`Audio ready! (readyState: ${alarm.readyState})`);
                            resolve();
                        };
                        
                        alarm.addEventListener('canplay', onCanPlay);
                        alarm.load(); // Force reload
                    });
                }
                
                // Reset and play
                alarm.currentTime = 0;
                log(`Audio currentTime reset to: ${alarm.currentTime}`);
                log(`Audio readyState: ${alarm.readyState}`);
                log(`Audio paused: ${alarm.paused}`);
                
                if (alarm.readyState >= 2) {
                    const playPromise = alarm.play();
                    await playPromise;
                    
                    log('Alarm playing successfully!');
                    status.textContent = 'ALARM PLAYING';
                    lastAlarmTime = now;
                } else {
                    throw new Error('Audio still not ready after waiting');
                }
                
            } catch (err) {
                log(`Alarm play failed: ${err.name} - ${err.message}`);
                status.textContent = `Audio Error: ${err.message}`;
                
                // Try generated beep as fallback
                log('Trying generated beep fallback...');
                generateBeep();
                stopFlashing();
            }
        }
        
        // Event listeners
        document.getElementById('playBtn').addEventListener('click', async () => {
            log('Test button clicked');
            await initAudio();
            await playAlarm();
        });
        
        document.getElementById('genBeepBtn').addEventListener('click', async () => {
            log('Generate beep clicked');
            await initAudio();
            generateBeep();
        });
        
        document.getElementById('testFiles').addEventListener('click', async () => {
            log('Test files clicked');
            await testAudioFiles();
        });
        
        document.getElementById('startBtn').addEventListener('click', async () => {
            log('Start monitoring clicked');
            const audioReady = await initAudio();
            if (!audioReady) {
                status.textContent = 'Audio initialization failed';
                return;
            }
            
            monitoring = true;
            document.getElementById('startBtn').style.display = 'none';
            status.textContent = 'Monitoring active';
            debug.innerHTML = '<br>';
            log('Monitoring started');
        });
        
        // Monitor audio loading
        alarm.addEventListener('loadstart',  () => log('Audio loading started'));
        alarm.addEventListener('loadeddata', () => log('Audio data loaded'));
        alarm.addEventListener('canplay',    () => log('Audio can play'));
        alarm.addEventListener('error',     (e) => log(`Audio error event: ${e.message || 'Unknown error'}`));
        
        // Volume monitoring loop
        setInterval(async () => {
            if (!monitoring) return;
            
            try {
                const response = await fetch('/volume');
                if (!response.ok) 
                {
                    stopFlashing();
                    throw new Error(`HTTP ${response.status}`);
                }
                
                const data = await response.json();
                const vol = parseFloat(data.volume);
                
                document.getElementById('volume').textContent = `Volume: ${vol.toFixed(2)}`;
                
                if (vol > THRESHOLD) 
                {
                    await playAlarm();
                } else 
                if (status.textContent.includes('ALARM')) 
                {
                    status.textContent = 'Monitoring active';
                    stopFlashing();
                }
                
            } catch (error) 
            {
                console.error("Volume fetch failed:", error);
                status.textContent = `Connection error: ${error.message}`;
                stopFlashing();
            }
        }, 200);
        
        // Initial file test
        window.addEventListener('load', () => {
            log('Page loaded, testing audio files...');
            setupVideoStream(); // Set up dynamic video URL
            testAudioFiles();
        });
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

