import subprocess
import json

def test_regenerate_command():
    result = subprocess.run(['python', '-m', 'shannon.cli', 'regenerate'], capture_output=True, text=True)
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output['status'] == 'ok'
    assert isinstance(output['entries_processed'], int)
    assert isinstance(output['output_file'], str)

def test_regenerate_http_endpoint():
    import requests

    response = requests.post('http://localhost:8000/regenerate')
    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'ok'
    assert isinstance(data['entries_processed'], int)
    assert isinstance(data['output_file'], str)

#
