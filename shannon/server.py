```python
import http.server
import threading
import json
import time
from flask import Flask, jsonify

app = Flask(__name__)

_start_time = time.time()

@app.route('/health', methods=['GET'])
def health():
    data = dict(status="ok", uptime_seconds=time.time() - _start_time)
    return jsonify(data)

def start_health_server(port=8484):
    app.run(host='0.0.0.0', port=port, threaded=True)

if __name__ == "__main__":
    import os
    health_port = int(os.getenv('SHANNON_HEALTH_PORT', 8484))
    thread = threading.Thread(target=start_health_server, args=(health_port,))
    thread.start()
```
