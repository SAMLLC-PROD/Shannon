# Shannon

Shannon is a Python-based service designed for [brief description of the project's purpose].

## Installation

To install Shannon, follow these steps:

1. Clone the repository:
   ```sh
   git clone https://github.com/your-repo/shannon.git
   cd shannon
   ```

2. Install dependencies:
   ```sh
   pip install -r requirements.txt
   ```

3. Set environment variables (optional):
   ```sh
   export SHANNON_HEALTH_PORT=8484
   ```

## Usage

### Running the Service

To start the Shannon service, run:

```sh
python shannon/server.py
```

The service will listen on port 8484 by default. If you set the `SHANNON_HEALTH_PORT` environment variable, it will use that port instead.

### Health Check Endpoint

Shannon provides a simple HTTP health check endpoint at `/health`. You can access this endpoint to verify that the service is running correctly.

#### Example Request

```sh
curl http://localhost:8484/health
```

#### Expected Response

```json
{
  "status": "ok",
  "version": "0.1.0",
  "uptime_seconds": <float>
}
```

- `status`: Indicates the health status of the service.
- `version`: The version of the Shannon service.
- `uptime_seconds`: The number of seconds since the server started.

### Running Tests

To run the tests, use:

```sh
python -m pytest tests/
```

## Contributing

Contributions are welcome! Please read our [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to contribute to this project.
