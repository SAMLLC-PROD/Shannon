import pytest
from fastapi.testclient import TestClient
from shannon.server import app

@pytest.fixture(scope="module")
def client():
    return TestClient(app)

def test_stats_endpoint_no_store(client):
    response = client.get("/stats")
    assert response.status_code == 200
    data = response.json()
    assert data == {"entry_count": 0, "store_size": 0, "latest_entry_timestamp": None}

# Assuming you have a way to populate the store for testing purposes
def test_stats_endpoint_with_store(client):
    # This is a placeholder. You need to implement a way to add entries to the store.
    # For example, you might call a function that writes data to the store.
    response = client.get("/stats")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["entry_count"], int)
    assert isinstance(data["store_size"], int)
    assert isinstance(data["latest_entry_timestamp"], str) or data["latest_entry_timestamp"] is None
