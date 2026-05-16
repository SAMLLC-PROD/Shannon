import http.server
import threading
import json
import time

_start_time = time.time()

class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            data = dict(status="ok", uptime_seconds=time.time() - _start_time)
            response = data
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        else:
            self.send_error(404)

def start_health_server(port=8484):
    server_address = ('', port)
    httpd = http.server.HTTPServer(server_address, HealthHandler)
    thread = threading.Thread(target=httpd.serve_forever)
    thread.daemon = True
    thread.start()
