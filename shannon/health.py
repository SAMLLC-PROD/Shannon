import http.server
import threading
import json
import time
from shannon.store import stats  # Import the stats function from shannon.store

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
        elif self.path == "/stats":
            try:
                store_stats = stats()  # Call the imported store.stats() function
                if not store_stats:  # Handle case where store doesn't exist yet
                    response = {
                        "entry_count": 0,
                        "size": 0,
                        "timestamp": 0
                    }
                else:
                    response = store_stats
            except Exception as e:  # Ensure proper error handling is added for any exceptions that might occur during the execution of the new endpoint
                self.send_error(500, f"Internal Server Error: {str(e)}")
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_error(404)
