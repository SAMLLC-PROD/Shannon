import http.server
import threading
import json
import time
from . import __version__

_start_time = time.time()

class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            data = {
                "status": "ok",
                "version": __version__,
                "uptime_seconds": time.time() - _start_time
            }
            response = json.dumps(data)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response.encode())
        else:
            self.send_error(404)

def start_health_server(port=8484):
    """Start a health check server in a background thread."""
    handler = HealthHandler
    httpd = http.server.HTTPServer(('localhost', port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
