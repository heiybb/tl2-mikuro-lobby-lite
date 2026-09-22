"""
Runs every test against throwaway local servers (ports 14549/14559, 24600-24610).

    python run_tests.py
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable


def start_server(port: int, pool: str, **env) -> subprocess.Popen:
    e = {k: v for k, v in os.environ.items() if k not in (
        "LOBBY_PASSWORD", "ALLOW_TAPTAP", "RELAY_IP", "RELAY_PORT_RANGE", "LOBBY_VERBOSE")}
    e.update(env)
    p = subprocess.Popen([PY, str(HERE / "lobby_server.py"), "--env", "none", "--host", "127.0.0.1",
                          "--port", str(port), "--relay-port", str(port), "--relay-port-range", pool,
                          "--relay-ip", "127.0.0.1"],
                         cwd=HERE, env=e, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return p
        except OSError:
            time.sleep(0.1)
    p.kill()
    raise SystemExit(f"server on {port} did not start")


def run(*args: str) -> None:
    print(f"$ python {' '.join(args)}", flush=True)
    subprocess.run([PY, *args], cwd=HERE, check=True)


def main() -> int:
    run("test_tap_login.py")
    run("test_relay_pairs.py")

    open_srv = start_server(14549, "24600-24610")
    try:
        run("test_client.py", "--port", "14549")
        run("test_tap_login.py", "--port", "14549")
        run("test_matchmaking.py", "--port", "14549", "--range", "24600-24610")
    finally:
        open_srv.terminate()

    pw_srv = start_server(14559, "24620-24625", LOBBY_PASSWORD="correct horse", ALLOW_TAPTAP="0")
    try:
        run("test_client.py", "--port", "14559", "--password", "correct horse")
        run("test_client.py", "--port", "14559", "--password", "wrong", "--expect-reject")
        run("test_client.py", "--port", "14559", "--expect-reject")
        print("$ TapTap login on a server with ALLOW_TAPTAP=0 must be refused", flush=True)
        r = subprocess.run([PY, "test_tap_login.py", "--port", "14559"], cwd=HERE,
                           capture_output=True, encoding="utf-8", errors="replace")
        assert r.returncode != 0 and "refused" in r.stdout + r.stderr, r.stdout + r.stderr
        print("[OK] refused")
    finally:
        pw_srv.terminate()

    print("\n[OK] all tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
