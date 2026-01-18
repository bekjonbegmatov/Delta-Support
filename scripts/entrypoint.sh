#!/usr/bin/env bash
set -euo pipefail

python - <<'PY'
import os
import socket
import time
from urllib.parse import urlparse


def wait(host: str, port: int, name: str, timeout: int = 60):
    start = time.time()
    while True:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError:
            if time.time() - start > timeout:
                raise SystemExit(f"Timeout waiting for {name} at {host}:{port}")
            time.sleep(1)


db_url = (os.getenv("DATABASE_URL") or "").strip()
if db_url:
    parsed = urlparse(db_url.replace("postgresql://", "postgres://", 1))
    if parsed.hostname and parsed.port:
        wait(parsed.hostname, parsed.port, "postgres")

redis_host = (os.getenv("REDIS_HOST") or "").strip()
redis_port = (os.getenv("REDIS_PORT") or "").strip()
if redis_host and redis_port:
    wait(redis_host, int(redis_port), "redis")
PY

exec python main.py

