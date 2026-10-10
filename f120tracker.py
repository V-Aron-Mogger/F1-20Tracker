import json
import socket
import struct
import threading
import math
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

UDP_PORT = 20777      # a játék ide küldi a telemetriát
HTTP_PORT = 8000      # ezen éri el a telefon az oldalt
NUM_CARS = 22
MOTION_PACKET_ID = 0
MIN_TRAIL_STEP = 3.0  # méter

HEADER_FMT = "<HBBBBQfIBB"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
CAR_FMT = "<6f6h6f"
CAR_SIZE = struct.calcsize(CAR_FMT)

state = {"cars": [], "player": 0, "trail": []}
lock = threading.Lock()


def udp_receiver():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", UDP_PORT))

    while True:
        data, _ = sock.recvfrom(4096)
        if len(data) < HEADER_SIZE:
            continue
        header = struct.unpack_from(HEADER_FMT, data, 0)
        if header[4] != MOTION_PACKET_ID:
            continue

        my_index = header[8]
        cars = []
        for i in range(NUM_CARS):
            off = HEADER_SIZE + i * CAR_SIZE
            if off + CAR_SIZE > len(data):
                break
            vals = struct.unpack_from(CAR_FMT, data, off)
            cars.append((round(vals[0], 1), round(vals[2], 1)))

        with lock:
            state["cars"] = cars
            state["player"] = my_index
            if my_index < len(cars):
                px, pz = cars[my_index]
                trail = state["trail"]
                if not trail or math.hypot(px - trail[-1][0], pz - trail[-1][1]) > MIN_TRAIL_STEP:
                    trail.append((px, pz))


PAGE = """<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>F1 2020 térkép</title>
<style>
  html, body { margin: 0; height: 100%; background: #111; overflow: hidden; }
  canvas { display: block; }
  #info { position: fixed; top: 8px; left: 10px; color: #ccc;
          font: 14px sans-serif; background: rgba(0,0,0,.5);
          padding: 4px 8px; border-radius: 6px; }
</style>
</head>
<body>
<div id="info">Várakozás a játékra...</div>
<canvas id="c"></canvas>
<script>
const c = document.getElementById('c');
const g = c.getContext('2d');
const info = document.getElementById('info');
let data = null;

function resize() {
  const dpr = window.devicePixelRatio || 1;
  c.width = innerWidth * dpr;
  c.height = innerHeight * dpr;
  c.style.width = innerWidth + 'px';
  c.style.height = innerHeight + 'px';
  draw();
}
addEventListener('resize', resize);

function draw() {
  const dpr = window.devicePixelRatio || 1;
  const W = c.width, H = c.height;
  g.fillStyle = '#111';
  g.fillRect(0, 0, W, H);
  if (!data) return;

  const pts = data.trail.concat(data.cars);
  if (pts.length === 0) return;

  let minx = Infinity, maxx = -Infinity, minz = Infinity, maxz = -Infinity;
  for (const [x, z] of pts) {
    if (x < minx) minx = x;
    if (x > maxx) maxx = x;
    if (z < minz) minz = z;
    if (z > maxz) maxz = z;
  }
  const pad = 40 * dpr;
  const spanX = (maxx - minx) || 1;
  const spanZ = (maxz - minz) || 1;
  const sc = Math.min((W - 2 * pad) / spanX, (H - 2 * pad) / spanZ);
  const ox = (W - spanX * sc) / 2;
  const oy = (H - spanZ * sc) / 2;
  const tx = x => ox + (x - minx) * sc;
  const ty = z => H - (oy + (z - minz) * sc);

  // pálya
  if (data.trail.length > 1) {
    g.beginPath();
    data.trail.forEach(([x, z], i) => {
      i ? g.lineTo(tx(x), ty(z)) : g.moveTo(tx(x), ty(z));
    });
    g.strokeStyle = '#888';
    g.lineWidth = 6 * dpr;
    g.lineJoin = 'round';
    g.lineCap = 'round';
    g.stroke();
  }

  // ellenfelek
  data.cars.forEach(([x, z], i) => {
    if (i === data.player) return;
    g.beginPath();
    g.arc(tx(x), ty(z), 7 * dpr, 0, Math.PI * 2);
    g.fillStyle = '#4aa3ff';
    g.fill();
  });

  // saját autó
  if (data.player < data.cars.length) {
    const [x, z] = data.cars[data.player];
    g.beginPath();
    g.arc(tx(x), ty(z), 10 * dpr, 0, Math.PI * 2);
    g.fillStyle = '#ff3b3b';
    g.fill();
    g.lineWidth = 2 * dpr;
    g.strokeStyle = '#000';
    g.stroke();
  }

  info.textContent = `Autók: ${data.cars.length}, pályapontok: ${data.trail.length}`;
}

async function poll() {
  try {
    const r = await fetch('/data', { cache: 'no-store' });
    data = await r.json();
    draw();
  } catch (e) {
    info.textContent = 'Nincs kapcsolat a szerverrel';
  }
  setTimeout(poll, 200);
}

resize();
poll();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/data":
            with lock:
                body = json.dumps(state).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # ne spammelje a konzolt


if __name__ == "__main__":
    threading.Thread(target=udp_receiver, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    print(f"Szerver fut. Telefonon nyisd meg: http://<PC-IP>:{HTTP_PORT}")
    server.serve_forever()