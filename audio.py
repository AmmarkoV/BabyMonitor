from http.server import BaseHTTPRequestHandler, HTTPServer
import subprocess

INDEX_HTML = b"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Audio + Image Stream</title>
<style>
  body { text-align: center; background: #222; color: white; font-family: Arial, sans-serif; }
  video, audio { margin: 10px auto; display: block; max-width: 80vw; }
</style>
</head>
<body>
  <h1>Image and Audio Streaming</h1>
  
  <!-- Assuming your image stream is served here -->
  <img src="http://localhost:8080/image_stream" alt="Image Stream" width="640" height="480" />

  <audio controls autoplay>
    <source src="/audio.mp3" type="audio/mpeg" />
    Your browser does not support the audio element.
  </audio>
</body>
</html>
"""

class AudioStreamingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(INDEX_HTML)))
            self.end_headers()
            self.wfile.write(INDEX_HTML)

        elif self.path == '/audio.mp3':
            self.send_response(200)
            self.send_header('Content-Type', 'audio/mpeg')
            self.end_headers()

            ffmpeg_cmd = [
                'ffmpeg',
                '-f', 'alsa',  # Change as needed for your audio device
                '-i', 'default',
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
                    try:
                        self.wfile.write(data)
                    except (BrokenPipeError, ConnectionResetError):
                        print("Client disconnected")
                        break
            finally:
                ffmpeg_proc.terminate()

        else:
            self.send_error(404, "Not Found")

def run(server_class=HTTPServer, handler_class=AudioStreamingHandler, port=8081):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    print(f"Starting audio HTTP server on port {port}")
    httpd.serve_forever()

if __name__ == '__main__':
    run()

