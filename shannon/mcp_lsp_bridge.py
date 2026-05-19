#!/usr/bin/env python3
"""Bridge between Content-Length framed stdio (LSP-style) and raw JSON-RPC lines.

Node.js MCP SDK (Qwen Code, claw, Claude Code) sends:
    Content-Length: 123\r\n\r\n{"jsonrpc":"2.0",...}

Python MCP SDK (mcp>=1.0) expects:
    {"jsonrpc":"2.0",...}\n

This bridge translates between the two.
"""
import subprocess
import sys
import threading
import os


def log(msg):
    sys.stderr.write(f"[bridge] {msg}\n")
    sys.stderr.flush()


def read_content_length_message(stream):
    """Read a Content-Length framed message from a binary stream."""
    content_length = None
    while True:
        line = stream.readline()
        if not line:
            return None  # EOF
        line_str = line.decode('utf-8', errors='replace').strip()
        if line_str == '':
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

    payload = b''
    while len(payload) < content_length:
        chunk = stream.read(content_length - len(payload))
        if not chunk:
            return None
        payload += chunk
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
            log(f"← subprocess: {line[:120]}...")
            write_content_length_message(sys_stdout, line)
    except (BrokenPipeError, OSError) as e:
        log(f"stdout writer error: {e}")


def main():
    log("Bridge starting")
    
    shannon_home = os.path.dirname(os.path.abspath(__file__))
    venv_bin = os.path.join(os.path.dirname(shannon_home), '.venv', 'bin')
    shannon_mcp = os.path.join(venv_bin, 'shannon-mcp')

    if not os.path.exists(shannon_mcp):
        shannon_mcp = None

    if shannon_mcp:
        cmd = [shannon_mcp]
    else:
        cmd = [os.path.join(venv_bin, 'python'), '-m', 'shannon.mcp_main']

    log(f"Launching: {cmd}")
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,  # Pass through subprocess stderr
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
                log("EOF on stdin")
                break
            log(f"→ subprocess: {payload[:120]}...")
            proc.stdin.write(payload + b'\n')
            proc.stdin.flush()
    except (BrokenPipeError, OSError, KeyboardInterrupt) as e:
        log(f"stdin reader error: {e}")
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.wait(timeout=5)
        log("Bridge exiting")


if __name__ == '__main__':
    main()
