#!/usr/bin/env python3

from http.server import HTTPServer, BaseHTTPRequestHandler
import argparse
import html


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    body {{
      margin: 0;
      background: #111;
      color: #eee;
      font-family: Arial, sans-serif;
    }}

    header {{
      padding: 12px 16px;
      background: #1b1b1b;
      border-bottom: 1px solid #2a2a2a;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
    }}

    .controls {{
      display: flex;
      gap: 8px;
    }}

    button {{
      padding: 8px 12px;
      border: 1px solid #333;
      border-radius: 8px;
      background: #222;
      color: #eee;
      cursor: pointer;
    }}

    button:hover {{
      background: #2c2c2c;
    }}

    main {{
      height: calc(100vh - 58px);
      display: grid;
      grid-template-columns: {grid_cols};
      gap: 10px;
      padding: 10px;
      box-sizing: border-box;
    }}

    .panel {{
      display: flex;
      flex-direction: column;
      border: 1px solid #2a2a2a;
      border-radius: 12px;
      overflow: hidden;
      background: #000;
      min-height: 0;
    }}

    .bar {{
      background: #181818;
      border-bottom: 1px solid #2a2a2a;
      padding: 8px 10px;
      font-size: 13px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    iframe {{
      border: 0;
      width: 100%;
      height: 100%;
      flex: 1;
      background: #000;
    }}

    @media (max-width: 900px) {{
      main {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <strong>{title}</strong>
    <div class="controls">
      <button onclick="reloadFrames()">Reload</button>
      {swap_button}
    </div>
  </header>

  <main>
    {panels}
  </main>

  <script>
    function reloadFrames() {{
      const frames = document.querySelectorAll("iframe");
      frames.forEach(f => f.src = f.src);
    }}

    function swapFrames() {{
      const f1 = document.getElementById("frame1");
      const f2 = document.getElementById("frame2");
      const t1 = document.getElementById("title1");
      const t2 = document.getElementById("title2");

      if (!f1 || !f2 || !t1 || !t2) return;

      const s = f1.src;
      f1.src = f2.src;
      f2.src = s;

      const tt = t1.textContent;
      t1.textContent = t2.textContent;
      t2.textContent = tt;
    }}
  </script>
</body>
</html>
"""


def build_panel(index: int, url: str) -> str:
    safe_url = html.escape(url, quote=True)
    return f"""
    <section class="panel">
      <div class="bar" id="title{index}">{safe_url}</div>
      <iframe id="frame{index}" src="{safe_url}" allow="autoplay; microphone; camera"></iframe>
    </section>
    """


class MainHandler(BaseHTTPRequestHandler):
    page_html = ""

    def do_GET(self):
        if self.path not in ["/", "/index.html"]:
            self.send_error(404, "Not Found")
            return

        payload = self.page_html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    parser = argparse.ArgumentParser(description="Serve single/dual monitor index page")
    parser.add_argument("--ip", required=True, help="IP/hostname to embed in iframe URLs")
    parser.add_argument("-p", "--port", type=int, required=True, help="Port to serve main page on")
    parser.add_argument(
        "-d",
        "--device-port",
        dest="device_ports",
        action="append",
        type=int,
        required=True,
        help="Monitor web port, can be repeated: -d 8080 -d 8090",
    )

    args = parser.parse_args()

    if len(args.device_ports) < 1:
        raise SystemExit("At least one -d port must be provided")

    if len(args.device_ports) > 2:
        raise SystemExit("Currently only 1 or 2 monitor ports are supported")

    urls = [f"http://{args.ip}:{port}/" for port in args.device_ports]

    if len(urls) == 1:
        title = "Single Baby Monitor View"
        grid_cols = "1fr"
        swap_button = ""
    else:
        title = "Dual Baby Monitor View"
        grid_cols = "1fr 1fr"
        swap_button = '<button onclick="swapFrames()">Swap</button>'

    panels = "\n".join(build_panel(i + 1, url) for i, url in enumerate(urls))

    MainHandler.page_html = HTML_TEMPLATE.format(
        title=title,
        grid_cols=grid_cols,
        swap_button=swap_button,
        panels=panels,
    )

    server = HTTPServer(("0.0.0.0", args.port), MainHandler)
    print(f"Serving {title} on http://0.0.0.0:{args.port}/")
    print("Embedded monitor URLs:")
    for url in urls:
        print(f"  {url}")
    server.serve_forever()


if __name__ == "__main__":
    main()
