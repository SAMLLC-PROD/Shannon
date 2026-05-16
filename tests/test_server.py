import unittest
from shannon.server import start_health_server, HealthHandler
import requests
import time

class TestServer(unittest.TestCase):
    def setUp(self):
        self.port = 8485
        start_health_server(port=self.port)

    def test_health_endpoint(self):
        response = requests.get(f"http://localhost:{self.port}/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("status", data)
        self.assertIn("uptime_seconds", data)
        self.assertEqual(data["status"], "ok")

    def test_uptime_increments(self):
        start_time = time.time()
        response1 = requests.get(f"http://localhost:{self.port}/health")
        time.sleep(2)
        response2 = requests.get(f"http://localhost:{self.port}/health")
        data1 = response1.json()
        data2 = response2.json()
        self.assertGreater(data2["uptime_seconds"], data1["uptime_seconds"])

if __name__ == '__main__':
    unittest.main()
