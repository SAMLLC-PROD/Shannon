#!/usr/bin/env python3
"""Bridge between Content-Length framed stdio (LSP-style) and raw JSON-RPC lines.

Claw (rusty-claude-cli) sends MCP messages with Content-Length headers:
    Content-Length: 123\r\n
    \r\n
    {"jsonrpc":"2.0","method":"initialize",...}

The Python MCP library (mcp>=1.0) expects raw JSON-RPC lines on stdin:
    {"jsonrpc":"2.0","method":"initialize",...}\n

This bridge translates between the two by:
1. Reading Content-Length framed messages from stdin
2. Forwarding them as lines to the MCP server subprocess
3. Reading line-delimited responses from the subprocess
4. Wrapping them in Content-Length frames to stdout
"""
import subprocess
import sys
import threading
import os


def read_content_length_message(stream):
    """Read a Content-Length framed message from a binary stream."""
    content_length = None
    while True:
        line = stream.readline()
        if not line:
            return None  # EOF
        line_str = line.decode('utf-8', errors='replace').strip()
        if line_str == '':
            # Empty line = end of headers
            if content_length is not None:
                break
            continue
        if line_str.lower().startswith('content-length:'):
            try:
                content_length = int(line_str.split(':', 1)[1].strip())
            except ValueError:
                continue
    
    if content_length is None:
        return None
    
    payload = stream.read(content_length)
    if len(payload) < content_length:
        return None
    return payload


def write_content_length_message(stream, payload: bytes):
    """Write a Content-Length framed message to a binary stream."""
    header = f"Content-Length: {len(payload)}\r\n\r\n".encode('utf-8')
    stream.write(header + payload)
    stream.flush()


def forward_subprocess_to_stdout(proc_stdout, sys_stdout):
    """Read line-delimited JSON from subprocess, wrap in Content-Length to stdout."""
    try:
        for line in proc_stdout:
            line = line.strip()
            if not line:
                continue
            write_content_length_message(sys_stdout, line)
    except (BrokenPipeError, OSError):
        pass


def main():
    # Find the shannon-mcp executable
    shannon_home = os.path.dirname(os.path.abspath(__file__))
    venv_bin = os.path.join(os.path.dirname(shannon_home), '.venv', 'bin')
    shannon_mcp = os.path.join(venv_bin, 'shannon-mcp')
    
    if not os.path.exists(shannon_mcp):
        # Fallback: try python -m
        shannon_mcp = None

    if shannon_mcp:
        cmd = [shannon_mcp]
    else:
        cmd = [os.path.join(venv_bin, 'python'), '-m', 'shannon.mcp_main']

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
    )

    # Thread: read subprocess stdout (line JSON) → wrap in Content-Length → our stdout
    reader_thread = threading.Thread(
        target=forward_subprocess_to_stdout,
        args=(proc.stdout, sys.stdout.buffer),
        daemon=True,
    )
    reader_thread.start()

    # Main thread: read Content-Length from our stdin → unwrap → line to subprocess stdin
    try:
        while True:
            payload = read_content_length_message(sys.stdin.buffer)
            if payload is None:
                break
            proc.stdin.write(payload + b'\n')
            proc.stdin.flush()
    except (BrokenPipeError, OSError, KeyboardInterrupt):
        pass
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.wait(timeout=5)


if __name__ == '__main__':
    main()
