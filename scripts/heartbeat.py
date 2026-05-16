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
        elif self.path == "/regenerate":
            from shannon.openclaw import regenerate_context
            result = regenerate_context()
            response = {
                "status": "ok",
                "entries_processed": result["entries_processed"],
                "output_file": result["output_file"]
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_error(404)

def run_health_server(port=8080):
    server_address = ('', port)
    httpd = http.server.HTTPServer(server_address, HealthHandler)
    print(f"Health server running on port {port}...")
    httpd.serve_forever()

if __name__ == "__main__":
    run_health_server()
