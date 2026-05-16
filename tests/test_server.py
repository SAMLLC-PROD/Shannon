```python
import pytest
from shannon.server import start_health_server, app

@pytest.fixture(scope="module")
def health_client():
    with app.test_client() as client:
        yield client

def test_health_endpoint(health_client):
    response = health_client.get('/health')
    assert response.status_code == 200
    data = response.json
    assert data['status'] == 'ok'
    assert isinstance(data['uptime_seconds'], float)

def test_uptime_increments_correctly(health_client):
    initial_response = health_client.get('/health')
    initial_uptime = initial_response.json['uptime_seconds']
    
    time.sleep(1)
    
    second_response = health_client.get('/health')
    second_uptime = second_response.json['uptime_seconds']
    
    assert second_uptime > initial_uptime
```
