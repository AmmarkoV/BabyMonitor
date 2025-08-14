from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import cv2
import subprocess

INDEX_HTML = b"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8" /><title>Combined Audio & Video Stream</title></head>
<body style="text-align:center; background:#222; color:#fff;">
  <h1>Combined Video + Audio Stream</h1>
  <img src="/image_stream" width="640" height="480" alt="Video Stream" />
  <br/>
  <audio controls autoplay>
    <source src="/audio.mp3" type="audio/mpeg" />
    Your browser does not support the audio element.
  </audio>
</body>
</html>
"""

class CombinedStreamHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(INDEX_HTML)))
            self.end_headers()
            self.wfile.write(INDEX_HTML)

        elif self.path == '/image_stream':
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.end_headers()

            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                print("Cannot open camera")
                return

            try:
                while True:
                    ret, frame = cap.read()
                    if not ret:
                        break

                    # Encode frame as JPEG
                    ret2, jpeg = cv2.imencode('.jpg', frame)
                    if not ret2:
                        continue
                    jpg_bytes = jpeg.tobytes()

                    # Write multipart headers and JPEG frame
                    self.wfile.write(b'--frame\r\n')
                    self.wfile.write(b'Content-Type: image/jpeg\r\n')
                    self.wfile.write(f'Content-Length: {len(jpg_bytes)}\r\n\r\n'.encode())
                    self.wfile.write(jpg_bytes)
                    self.wfile.write(b'\r\n')

            except (BrokenPipeError, ConnectionResetError):
                print("Client disconnected from video stream")
            finally:
                cap.release()

        elif self.path == '/audio.mp3':
            self.send_response(200)
            self.send_header('Content-Type', 'audio/mpeg')
            self.end_headers()

            ffmpeg_cmd = [
                'ffmpeg',
                '-f', 'pulse',       # or 'pulse' or whatever your audio input is
                '-i', 'default',    # adjust if necessary
                '-f', 'mp3',
                '-codec:a', 'libmp3lame',
                '-b:a', '128k',
                '-'
            ]
            ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

            try:
                while True:
                    data = ffmpeg_proc.stdout.read(1024)
                    if not data:
                        break
                    self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                print("Client disconnected from audio stream")
            finally:
                ffmpeg_proc.terminate()

        else:
            self.send_error(404, "Not Found")


def run(server_class=HTTPServer, handler_class=CombinedStreamHandler, port=8080):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    print(f"Starting combined audio+video server on port {port}")
    httpd.serve_forever()

if __name__ == '__main__':
    run()
